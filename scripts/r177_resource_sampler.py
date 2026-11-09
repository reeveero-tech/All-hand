#!/usr/bin/env python3
"""R-177 (D-144): sample the SUT process's resources and compute leak indicators.

Why: in the soak of log 390 the external sidecar (``ls /proc/<pid>/fd | wc -l``
with ``2>/dev/null``) wrote **0 open fds** for every sample. A live uvicorn
process can never have 0 fds, so the read had failed and the pipe turned the
failure into a number.

Root cause (proven, log 395): ``server.py`` calls
``safety.harden_server_process()`` = ``prctl(PR_SET_DUMPABLE, 0)``, a
deliberate security control (R-100). A non-dumpable process's
``/proc/<pid>/fd`` and ``/proc/<pid>/smaps_rollup`` are unreadable
(EACCES) to every other process of the same user, **including its parent**.
``/proc/<pid>/status`` and ``stat`` stay world-readable, which is why RSS
was measured and fds were not. The control is NOT disabled for measurement.

This module fixes the class of error two ways:

* an fd count is ``os.listdir('/proc/<pid>/fd')``. If it cannot be read, the
  value is ``None`` with the errno reason. It is **never** 0 by accident, and
  an unreadable metric is classified ``not_measured``.
* **in-process** sampling (authoritative for fds/PSS): the SUT is started
  through ``scripts/r177_sut_launcher.py``, which starts one daemon thread
  inside the SUT process and then runs uvicorn exactly as ``python -m
  uvicorn`` does. A process may always read its own /proc entries, so the
  hardening stays on and fds/PSS become measurable. The thread reads
  /proc/<pid>/status (VmRSS, VmHWM, Threads), smaps_rollup (Pss,
  Private_Dirty, Anonymous), stat (utime, stime, in ticks) and the fd
  directory every N seconds (cost: one small /proc read per tick, declared).
* **external** sampling from the harness (cross-check): the same reads from the
  parent; on the hardened SUT the fd/smaps fields are None with the
  PermissionError reason, which is recorded rather than hidden.
* leak **indicators** (not a pass/fail gate; no approved threshold exists):
  after a warm-up cut, an ordinary-least-squares slope per hour with a 95%
  confidence interval for each series, plus the relative growth per hour and
  the first-quarter/last-quarter means.
  ``growth_significant`` is true when the 95% CI lower bound is > 0.
  Significance is a statistical statement only. Any tolerance turning it into
  a verdict is **proposed**, owner-approval pending; this module never says
  "no leak".

The sampler follows a pid *provider* (callable), so a crash/restart of the
SUT is followed and every pid change is recorded.
"""
from __future__ import annotations

import csv
import math
import os
import pathlib
import threading
import time
from typing import Any, Callable, Dict, List, Optional

FIELDS = ["utc", "ts", "t_s", "pid", "rss_kb", "hwm_kb", "pss_kb", "private_dirty_kb",
          "anonymous_kb", "threads", "fds", "fds_error", "utime_ticks", "stime_ticks"]
SERIES = ("rss_kb", "pss_kb", "private_dirty_kb", "anonymous_kb", "threads", "fds")

# two-sided 95% t quantiles (df -> t); df >= 120 uses the normal value
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042,
        40: 2.021, 60: 2.000, 120: 1.980}


def _t95(df: int) -> float:
    if df >= 120:
        return 1.960
    keys = sorted(k for k in _T95 if k <= df)
    return _T95[keys[-1]] if keys else _T95[1]


def count_fds(pid: int, proc_root: str = "/proc") -> Dict[str, Any]:
    """Return {'fds': int} or {'fds': None, 'fds_error': '<errno name>: ...'}.

    Never returns 0 for a read failure. 0 is only possible if the directory
    was read and really was empty.
    """
    path = os.path.join(proc_root, str(pid), "fd")
    try:
        return {"fds": len(os.listdir(path)), "fds_error": ""}
    except OSError as exc:  # PermissionError, FileNotFoundError, ...
        return {"fds": None, "fds_error": f"{type(exc).__name__}: {exc.strerror or exc}"}


def _read_kv_kb(path: str, keys: Dict[str, str]) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {v: None for v in keys.values()}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                name, _, rest = line.partition(":")
                if name in keys:
                    out[keys[name]] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return out


def read_sample(pid: int, proc_root: str = "/proc") -> Dict[str, Any]:
    base = os.path.join(proc_root, str(pid))
    s: Dict[str, Any] = {"pid": pid}
    s.update(_read_kv_kb(os.path.join(base, "status"),
                         {"VmRSS": "rss_kb", "VmHWM": "hwm_kb", "Threads": "threads"}))
    s.update(_read_kv_kb(os.path.join(base, "smaps_rollup"),
                         {"Pss": "pss_kb", "Private_Dirty": "private_dirty_kb",
                          "Anonymous": "anonymous_kb"}))
    s.update(count_fds(pid, proc_root))
    try:
        with open(os.path.join(base, "stat"), encoding="utf-8") as fh:
            parts = fh.read().rsplit(")", 1)[1].split()
        s["utime_ticks"], s["stime_ticks"] = int(parts[11]), int(parts[12])
    except (OSError, IndexError, ValueError):
        s["utime_ticks"] = s["stime_ticks"] = None
    return s


def ols(ts: List[float], ys: List[float]) -> Optional[Dict[str, float]]:
    """Least-squares slope (per second) with a 95% CI. Returns None if n < 3."""
    n = len(ts)
    if n < 3:
        return None
    mt, my = sum(ts) / n, sum(ys) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx == 0:
        return None
    b = sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / sxx
    a = my - b * mt
    sse = sum((y - (a + b * t)) ** 2 for t, y in zip(ts, ys))
    se = math.sqrt(sse / (n - 2) / sxx) if n > 2 else float("inf")
    h = _t95(n - 2) * se
    return {"slope": b, "lo": b - h, "hi": b + h, "intercept": a, "n": n}


def leak_indicators(rows: List[Dict[str, Any]], warmup_frac: float = 0.25,
                    min_warmup_s: float = 600.0) -> Dict[str, Any]:
    """Indicators after the warm-up cut. Statistical only; no verdict is made."""
    if not rows:
        return {"status": "not_measured", "reason": "no samples"}
    t_end = rows[-1]["t_s"]
    cut = max(min_warmup_s if t_end > 2 * min_warmup_s else 0.0, warmup_frac * t_end)
    post = [r for r in rows if r["t_s"] >= cut]
    pids = sorted({r["pid"] for r in rows})
    out: Dict[str, Any] = {
        "status": "measured", "warmup_cut_s": round(cut, 1), "samples_total": len(rows),
        "samples_post_warmup": len(post), "pids": pids, "pid_changes": len(pids) - 1,
        "note": ("indicators only — OLS slope per hour with a 95% CI after the warm-up cut. "
                 "growth_significant = CI lower bound > 0. No approved leak threshold "
                 "exists (proposed only), so no pass/fail verdict and no 'no leak' claim."),
        "series": {}}
    for key in SERIES:
        pts = [(r["t_s"], float(r[key])) for r in post if r.get(key) is not None]
        missing = sum(1 for r in post if r.get(key) is None)
        entry: Dict[str, Any] = {"samples": len(pts), "missing": missing}
        if key == "fds":
            errs = sorted({r.get("fds_error") for r in post if r.get("fds_error")})
            if errs:
                entry["errors"] = errs
        if len(pts) < 3 or missing > len(post) // 2:
            entry["status"] = "not_measured"
            out["series"][key] = entry
            continue
        fit = ols([p[0] for p in pts], [p[1] for p in pts])
        ys = [p[1] for p in pts]
        q = max(1, len(ys) // 4)
        first_q, last_q = sum(ys[:q]) / q, sum(ys[-q:]) / q
        entry.update({
            "status": "measured", "first": ys[0], "last": ys[-1], "min": min(ys), "max": max(ys),
            "mean_first_quarter": round(first_q, 2), "mean_last_quarter": round(last_q, 2)})
        if fit:
            per_h = 3600.0
            entry.update({
                "slope_per_h": round(fit["slope"] * per_h, 4),
                "slope_per_h_ci95": [round(fit["lo"] * per_h, 4), round(fit["hi"] * per_h, 4)],
                "rel_growth_per_h_pct": (round(fit["slope"] * per_h / first_q * 100, 4)
                                         if first_q else None),
                "growth_significant": bool(fit["lo"] > 0)})
        out["series"][key] = entry
    return out


class ResourceSampler:
    """Background sampler of a pid (taken from ``pid_provider`` every tick)."""

    def __init__(self, pid_provider: Callable[[], Optional[int]], interval_s: float = 10.0,
                 csv_path: Optional[pathlib.Path] = None, proc_root: str = "/proc"):
        self.pid_provider = pid_provider
        self.interval_s = interval_s
        self.csv_path = pathlib.Path(csv_path) if csv_path else None
        self.proc_root = proc_root
        self.rows: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._t0 = 0.0
        self._th: Optional[threading.Thread] = None

    def _tick(self) -> None:
        pid = self.pid_provider()
        if not pid or not os.path.isdir(os.path.join(self.proc_root, str(pid))):
            return
        s = read_sample(pid, self.proc_root)
        s["ts"] = round(time.time(), 3)
        s["t_s"] = round(time.monotonic() - self._t0, 3)
        s["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.rows.append(s)
        if self.csv_path:
            _append_csv(self.csv_path, s)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 — a sampler must never break the run
                pass
            self._stop.wait(self.interval_s)

    def start(self) -> "ResourceSampler":
        self._t0 = time.monotonic()
        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._th = threading.Thread(target=self._loop, name="r177-resource-sampler",
                                    daemon=True)
        self._th.start()
        return self

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        if self._th:
            self._th.join(timeout=self.interval_s + 5)
        return {"csv": str(self.csv_path) if self.csv_path else None,
                "interval_s": self.interval_s, "leak_indicators": leak_indicators(self.rows)}


def _append_csv(path: pathlib.Path, row: Dict[str, Any]) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS})


def start_inprocess(csv_path: str, interval_s: float) -> threading.Thread:
    """Sample *this* process (used inside the SUT by r177_sut_launcher.py)."""
    path = pathlib.Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    def loop() -> None:
        while True:
            try:
                s = read_sample(pid)
                s["ts"] = round(time.time(), 3)
                s["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                _append_csv(path, s)
            except Exception:  # noqa: BLE001 — never break the SUT
                pass
            time.sleep(interval_s)

    th = threading.Thread(target=loop, name="r177-inproc-sampler", daemon=True)
    th.start()
    return th


def load_rows(csv_path: pathlib.Path, t_from: float, t_to: float) -> List[Dict[str, Any]]:
    """Rows with ts in [t_from, t_to]; t_s is relative to t_from; '' ⇒ None."""
    rows: List[Dict[str, Any]] = []
    p = pathlib.Path(csv_path)
    if not p.exists():
        return rows
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                ts = float(r["ts"])
            except (KeyError, ValueError):
                continue
            if not t_from <= ts <= t_to:
                continue
            row: Dict[str, Any] = {"ts": ts, "t_s": round(ts - t_from, 3),
                                   "utc": r.get("utc"), "fds_error": r.get("fds_error") or ""}
            for k in ("pid",) + SERIES + ("hwm_kb", "utime_ticks", "stime_ticks"):
                v = r.get(k)
                row[k] = int(v) if v not in (None, "") else None
            rows.append(row)
    return rows
