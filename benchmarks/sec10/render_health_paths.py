"""SEC-10 / Render (D-137): what Render's health checker WOULD see on each
candidate ``healthCheckPath`` — measured locally, never on Render.

Render's documented semantics (https://render.com/docs/health-checks): an
HTTP GET to the path, healthy = 2xx/3xx within 5 s; 15 s of consecutive
failures => stop routing to the instance; 60 s => restart it; a deploy is
live only when the new instance passes, else cancelled after 15 min.

This tool starts a real SUT (separate OS process, AGOS_ENV=production, the
default rate limit, an API key configured — the checker sends NO key) on the
throwaway LOCAL PostgREST, then samples the three candidate paths once per
second with a 5 s client timeout through three phases:
  healthy  -> PostgREST SIGSTOP (hung store) -> SIGCONT (recovery)
  and a SIGKILL-free "refused" phase is NOT run (it would need restarting
  the shared local stack); the hung store is the worse case for timeouts.
For every path it reports the per-phase status codes, the max latency, and
the derived Render outcome (would stop routing / would restart).

Honest limits: local loopback, not Render's network or checker; one shared
2-CPU sandbox; local PostgREST not hosted Supabase; the checker interval is
"every few seconds" on Render (we sample every 1 s — a superset).

Usage: R177_STORAGE_URL=http://127.0.0.1:54321 R177_STORAGE_KEY=... \
       python3 benchmarks/sec10/render_health_paths.py --json out.json [--outage-s 75]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
PATHS = ["/api/system/status", "/health/ready", "/health/live", "/health"]
RENDER_TIMEOUT_S = 5.0
STOP_ROUTING_S = 15.0
RESTART_S = 60.0


def _r177():
    spec = importlib.util.spec_from_file_location("r177", REPO / "scripts" / "r177_per_service.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r177"] = mod
    spec.loader.exec_module(mod)
    return mod


def get(url: str) -> tuple:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=RENDER_TIMEOUT_S) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:  # noqa: BLE001 — timeout / refused = a failed check
        code = 0
    return code, round((time.monotonic() - t0) * 1000, 1)


def render_ok(code: int, ms: float) -> bool:
    return 200 <= code < 400 and ms < RENDER_TIMEOUT_S * 1000


def postgrest_pid() -> int:
    out = subprocess.run(["pgrep", "-x", "postgrest"], capture_output=True, text=True).stdout.split()
    if len(out) != 1:
        raise SystemExit(f"need exactly one local postgrest process, got {out}")
    return int(out[0])


def sample_phase(base: str, seconds: float, phase: str, samples: list) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t = time.monotonic()
        for p in PATHS:
            code, ms = get(base + p)
            samples.append({"phase": phase, "path": p, "code": code, "ms": ms,
                            "ok": render_ok(code, ms), "t": round(t, 3)})
        rest = 1.0 - (time.monotonic() - t)
        if rest > 0:
            time.sleep(rest)


def longest_fail_run_s(rows: list) -> float:
    """Longest wall-clock span of CONSECUTIVE failed checks (Render's rule)."""
    best, start = 0.0, None
    for r in rows:
        if not r["ok"]:
            start = r["t"] if start is None else start
            best = max(best, r["t"] - start + 1.0)
        else:
            start = None
    return round(best, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--healthy-s", type=float, default=10)
    ap.add_argument("--outage-s", type=float, default=75)
    ap.add_argument("--recovery-s", type=float, default=15)
    a = ap.parse_args()
    r177 = _r177()
    import secrets as _s
    sut = r177.SutProcess(api_key="rhp-" + _s.token_hex(16), storage="live-local")  # prod needs >=16 chars
    # production-like: the real default rate limit and AGOS_ENV=production
    sut.env.pop("AGOS_RATE_LIMIT_REQUESTS", None)
    sut.env["AGOS_ENV"] = "production"
    sut.env.pop("RENDER", None)
    pid = postgrest_pid()
    samples: list = []
    stopped = False
    try:
        sut.start()
        sample_phase(sut.url, a.healthy_s, "healthy", samples)
        os.kill(pid, signal.SIGSTOP)
        stopped = True
        sample_phase(sut.url, a.outage_s, "store_hung", samples)
        os.kill(pid, signal.SIGCONT)
        stopped = False
        sample_phase(sut.url, a.recovery_s, "recovered", samples)
    finally:
        if stopped:
            os.kill(pid, signal.SIGCONT)
        sut.stop()
    summary = {}
    for p in PATHS:
        per = {}
        for ph in ("healthy", "store_hung", "recovered"):
            rows = [s for s in samples if s["path"] == p and s["phase"] == ph]
            codes: dict = {}
            for r in rows:
                codes[str(r["code"])] = codes.get(str(r["code"]), 0) + 1
            fail = longest_fail_run_s(rows)
            per[ph] = {"n": len(rows), "codes": codes,
                       "max_ms": max((r["ms"] for r in rows), default=None),
                       "ok_fraction": round(sum(r["ok"] for r in rows) / len(rows), 3) if rows else None,
                       "longest_consecutive_fail_s": fail,
                       "render_would_stop_routing": fail >= STOP_ROUTING_S,
                       "render_would_restart": fail >= RESTART_S}
        summary[p] = per
    out = {"tool": "benchmarks/sec10/render_health_paths.py",
           "render_rules": {"timeout_s": RENDER_TIMEOUT_S, "stop_routing_after_s": STOP_ROUTING_S,
                            "restart_after_s": RESTART_S,
                            "source": "https://render.com/docs/health-checks"},
           "env": {"AGOS_ENV": "production", "rate_limit": "default (100/60s per client)",
                   "storage": "live-local PostgREST (SIGSTOP = hung store)",
                   "checker_sends_api_key": False},
           "phases_s": {"healthy": a.healthy_s, "store_hung": a.outage_s, "recovered": a.recovery_s},
           "summary": summary, "samples": samples}
    pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
