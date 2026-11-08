"""R-178 ladder runner + P6 measurement collection (D-139).

The R-178 ladder is 100 -> 500 -> 1k -> 2.5k -> 5k -> 10k logical agents
(``AGENTS_PER_SPEC = level // 20``, 20 specializations). Per level the plan
(R178_R184_EXECUTION_PLAN) requires a CREATION run and an EXECUTION run.
This tool drives a REAL SUT process (separate OS process, like
scripts/r177_per_service.py) and collects the raw data the acceptance
criteria in benchmarks/p6/acceptance_criteria.json need:

  creation:  r178.creation_time_s (first request -> registry complete),
             r178.creation_peak_rss_bytes (SUT VmHWM sampled from /proc),
             r178.rss_per_agent_bytes
  execution: r178.dispatch_p95/p99 (POST /api/tasks latencies),
             r178.queue_wait_p95 (SSE task_accepted -> task_started, client clock),
             r178.error_rate (S1), r178.unstructured_refusals,
             r178.worker_fairness_jain (completed per agent from /api/agents),
             r178.specialization_imbalance (max/min completed per specialization),
             r178.duplicate_side_effects (task_completed events per task_id)
  recovery:  r178.agents_lost_after_kill (SIGKILL mid-execution -> restart ->
             registry count identical)

Reporting rules — forward corrections for the log-380 findings (F2/F3/F4):
  * percentiles are NEAREST-RANK (ceil) from raw samples, computed via
    benchmarks/p6/metrics.py — never an interpolated engine number;
  * DROPPED iterations (submissions that never returned a status) are
    reported next to every throughput figure;
  * p99 AND max are always reported next to p95 (the auth p99 tail of log
    368 was never discussed — that must not happen again);
  * ``not_measured`` is never ``pass`` (metrics.evaluate enforces it).

Guard (D-134/Q-023): only L0 (100) may run on the shared sandbox as a
correctness/health check — it is NOT a ladder step and proves no capacity
level. Larger levels refuse to start without the double explicit override
``--q023-resolved --owner-approved-large-run`` (same pattern as the R-176
stress/spike/soak profiles).

Usage (L0 health, live-local throwaway stack):
  R177_STORAGE_URL=http://127.0.0.1:54321 R177_STORAGE_KEY=<local throwaway> \
    python3 benchmarks/r178/ladder.py --level 100 --storage live-local \
        --json out.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional

REPO = pathlib.Path(__file__).resolve().parents[2]

LEVELS = (100, 500, 1000, 2500, 5000, 10000)  # R-178 ladder (plan §R-178)
L0 = 100  # the ONLY level allowed on the shared sandbox (health check)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def r177():
    return _load("r177", REPO / "scripts" / "r177_per_service.py")


def p6_metrics():
    return _load("p6_metrics", REPO / "benchmarks" / "p6" / "metrics.py")


def criteria() -> Dict[str, Any]:
    d = json.loads((REPO / "benchmarks" / "p6" / "acceptance_criteria.json").read_text())
    return {c["id"]: c for c in d["criteria"]}


# --------------------------------------------------------------- RSS sampling

def read_status_kb(pid: int) -> Optional[Dict[str, int]]:
    """VmRSS/VmHWM in KiB from /proc/<pid>/status, or None if unreadable."""
    try:
        with open(f"/proc/{pid}/status") as f:
            out: Dict[str, int] = {}
            for line in f:
                if line.startswith(("VmRSS:", "VmHWM:")):
                    k, v = line.split(":")
                    out[k.strip()] = int(v.split()[0])
            return out or None
    except OSError:
        return None


class RssSampler(threading.Thread):
    """Samples a process's VmRSS until stopped; keeps the peak seen."""

    def __init__(self, pid: int, interval_s: float = 0.25):
        super().__init__(daemon=True)
        self.pid, self.interval_s = pid, interval_s
        self.peak_kb = 0
        self.samples = 0
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            s = read_status_kb(self.pid)
            if s:
                self.peak_kb = max(self.peak_kb, s.get("VmRSS", 0))
                self.samples += 1
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)

    def peak_bytes(self) -> Optional[int]:
        # /proc VmHWM is the kernel-tracked high-water mark — more reliable
        # than any sampling; the sampler is the fallback if it is unavailable.
        final = read_status_kb(self.pid) or {}
        hwm = final.get("VmHWM") or self.peak_kb
        return hwm * 1024 if hwm else None


# --------------------------------------------------------------- SSE queue wait

class QueueWaitListener(threading.Thread):
    """Subscribes to /api/events/stream and records, per task_id, the local
    monotonic time of task_created and task_started (queue wait = started
    - created, both on the subscriber's clock — the only measurable pair:
    task_started is SSE-only, log-380 gap note in acceptance_criteria)."""

    def __init__(self, url: str, key: str, want_ids: set):
        super().__init__(daemon=True)
        self.url, self.key, self.want_ids = url.rstrip("/"), key, set(want_ids)
        self.accepted: Dict[str, float] = {}
        self.started: Dict[str, float] = {}
        self.lines = 0
        self._stop = threading.Event()
        self._sock = None

    def run(self) -> None:
        import http.client
        from urllib.parse import urlparse
        host, port, path = (lambda u: (u.hostname, u.port or 80, u.path + "?" + (u.query or "")))(
            urlparse(self.url + "/api/events/stream"))
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", path, headers={"X-API-Key": self.key, "Accept": "text/event-stream"})
            self._sock = conn.getresponse()
        except OSError:
            return
        buf = b""
        while not self._stop.is_set():
            chunk = self._sock.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle(line.decode("utf-8", "replace"))

    def _handle(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        self.lines += 1
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            return
        tid = ev.get("task_id")
        if tid not in self.want_ids:
            return
        t = time.monotonic()
        if ev.get("type") == "task_created" and tid not in self.accepted:
            self.accepted[tid] = t
        elif ev.get("type") == "task_started" and tid not in self.started:
            self.started[tid] = t

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=5)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def waits_s(self) -> List[float]:
        return [self.started[k] - self.accepted[k]
                for k in self.started if k in self.accepted]


# --------------------------------------------------------------- runner

def _report(name: str, values_ms: List[float], m) -> Dict[str, Any]:
    """Percentile report with the log-380 corrections: nearest-rank, p99+max
    always present, sample count always present."""
    return {
        "metric": name, "n": len(values_ms),
        "p50_ms": m.nearest_rank(values_ms, 0.50),
        "p95_ms": m.p95_ms(values_ms),
        "p99_ms": m.p99_ms(values_ms),
        "max_ms": max(values_ms) if values_ms else None,
        "percentile_method": "nearest_rank_ceil",
    }


def run_level(level: int, storage: str, n_tasks: int, drain_timeout_s: float,
              json_out: str, m, r, crit) -> Dict[str, Any]:
    per_spec = level // 20
    run = "r178-l0-" + secrets.token_hex(4) if level == L0 else f"r178-{level}-" + secrets.token_hex(4)
    out: Dict[str, Any] = {
        "tool": "benchmarks/r178/ladder.py", "run": run, "level": level,
        "agents_per_spec": per_spec, "storage": storage, "n_tasks": n_tasks,
        "workload": "validator + JSON (tool-only, no LLM)",
        "percentile_method": "nearest_rank_ceil",
        "is_l0_health_check_only": level == L0,
        "not": ("L0 is a correctness/health check, NOT an R-178 ladder step "
                "(D-134) and proves no capacity level" if level == L0 else
                "large-run flag present; Q-023 owner approval recorded in this file"),
    }
    sut = r.SutProcess(storage=storage)
    # ---------------------------------------------------------- creation run
    sut.env["AGENTS_PER_SPEC"] = str(per_spec)
    sampler: Optional[RssSampler] = None
    t_create0 = time.monotonic()
    try:
        sut.start()
        sampler = RssSampler(sut.proc.pid)
        sampler.start()
        # creation_time_s: from process start to every logical agent registered
        deadline = t_create0 + 300
        registered = 0
        while time.monotonic() < deadline:
            st, b = r._http(sut.url + "/api/agents", key=sut.tenants.a_key, timeout=10)
            registered = b.get("count", 0) if isinstance(b, dict) else 0
            if st == 200 and registered >= level:
                break
            time.sleep(0.2)
        creation_s = time.monotonic() - t_create0
        sampler.stop()
        out["creation"] = {
            "time_s": round(creation_s, 3),
            "registered_agents": registered,
            "target_agents": level,
            "complete": registered >= level,
            "peak_rss_bytes": sampler.peak_bytes(),
            "rss_sampler_samples": sampler.samples,
            "rss_per_agent_bytes": (sampler.peak_bytes() // level) if sampler.peak_bytes() else None,
        }
        # ------------------------------------------------------ execution run
        keys = [f"{run}-{i:05d}" for i in range(n_tasks)]
        want = set()
        listener = QueueWaitListener(sut.url, sut.tenants.a_key, want)
        listener.start()
        lat_ms: List[float] = []
        statuses: List[int] = []
        dropped = 0
        body = lambda k: {"specialization": "validator", "prompt": r.TRACK_PROMPT,
                          "idempotency_key": k}  # noqa: E731
        import concurrent.futures as cf
        t_exec0 = time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=16) as ex:
            def post(k):
                t0 = time.monotonic()
                st, b = r._http(sut.url + "/api/tasks", "POST", sut.tenants.a_key,
                                body(k), timeout=30)
                return k, st, b, (time.monotonic() - t0) * 1000.0
            for k, st, b, ms in ex.map(post, keys):
                if st is None:
                    dropped += 1
                    continue
                statuses.append(st)
                if st in (200, 202) and isinstance(b, dict):
                    lat_ms.append(ms)
                    want.add(b.get("task_id"))
                    listener.want_ids.add(b.get("task_id"))
        submit_s = time.monotonic() - t_exec0
        # drain
        api_state: Dict[str, str] = {}
        t0 = time.monotonic()
        while time.monotonic() - t0 < drain_timeout_s:
            live = [i for i in want if api_state.get(i) not in r.TERMINAL]
            if not live:
                break
            for i in live:
                st, b = r._http(f"{sut.url}/api/tasks/{i}", key=sut.tenants.a_key, timeout=10)
                if st == 200 and isinstance(b, dict):
                    api_state[i] = b.get("status")
            time.sleep(0.5)
        drain_s = time.monotonic() - t0
        listener.stop()
        # fairness + imbalance from the live registry (works on both storages)
        st, agents_body = r._http(sut.url + "/api/agents", key=sut.tenants.a_key, timeout=30)
        per_agent: Dict[str, int] = {}
        per_spec_done: Dict[str, int] = {}
        agents_by_spec: Dict[str, List[str]] = {}
        if st == 200 and isinstance(agents_body, dict):
            for a in agents_body.get("agents", []):
                done = (a.get("stats") or {}).get("tasks_completed", 0)
                per_agent[a["name"]] = done
                agents_by_spec.setdefault(a["specialization"], []).append(a["name"])
                per_spec_done[a["specialization"]] = per_spec_done.get(a["specialization"], 0) + done
        # duplicate side effects: task_completed events per task_id (proxy at L0,
        # real recipient-side keying is the ladder-run contract)
        ev_counts: Dict[str, int] = {}
        if storage == "live-local":
            import urllib.request
            url = os.environ["R177_STORAGE_URL"].rstrip("/") + "/rest/v1/"
            key = os.environ["R177_STORAGE_KEY"]
            ids = ",".join(sorted(want))
            q = "events?select=event_data&event_type=eq.task_completed&event_data->>task_id=in." \
                + urllib.parse.quote("(" + ids + ")")
            req = urllib.request.Request(url + q, headers={"apikey": key, "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                for e in json.loads(resp.read()):
                    tid = e["event_data"].get("task_id")
                    ev_counts[tid] = ev_counts.get(tid, 0) + 1
        else:
            for i in want:
                ev_counts[i] = 1 if api_state.get(i) in r.TERMINAL else 0
        # ------------------------------------------------ kill/restart recovery
        killed_registered = None
        names_before: set = set()
        names_after: set = set()
        if level == L0 or level >= 1000:  # kill check per plan: at 50% of 5k/10k — L0 keeps a light version
            names_before = set(per_agent)
            sut.kill()
            sut.start()
            st, b = r._http(sut.url + "/api/agents", key=sut.tenants.a_key, timeout=30)
            killed_registered = b.get("count", 0) if isinstance(b, dict) else 0
            if isinstance(b, dict):
                names_after = {a["name"] for a in b.get("agents", [])}
        out["execution"] = {
            "submit_s": round(submit_s, 3),
            "n_submitted": len(keys), "n_accepted": len(want),
            "dropped_submissions": dropped,
            "status_codes": sorted({str(s) for s in statuses}),
            "dispatch_latency": _report("dispatch", lat_ms, m),
            "queue_wait_s": _report("queue_wait", [w * 1000.0 for w in listener.waits_s()], m),
            "drain_s": round(drain_s, 3),
            "final_states": {s: list(api_state.values()).count(s) for s in set(api_state.values())},
            "per_agent_completed": per_agent,
            "per_specialization_completed": per_spec_done,
            "task_completed_event_counts": {k: v for k, v in ev_counts.items() if v != 1},
        }
        out["recovery"] = {"agents_after_kill_restart": killed_registered,
                           "agents_target": level,
                           "agents_lost": (level - killed_registered) if killed_registered is not None else None}
        # -------------------------------------------------- criteria evaluation
        # Jain fairness per the criterion data_source: WITHIN one specialization,
        # over every agent of that specialization (zeros included — filtering
        # them would inflate the index). L0's workload touches one specialization.
        jain_counts: List[float] = []
        for spec_name, spec_agents in agents_by_spec.items():
            if per_spec_done.get(spec_name):
                jain_counts = [float(per_agent.get(a, 0)) for a in spec_agents]
        evals = []
        if level == L0:
            # L0 health check evaluates the correctness criteria that do not
            # depend on scale; capacity criteria stay not_measured by design.
            # specialization_imbalance needs an EQUAL-MIX workload (its
            # data_source) — the L0 validator-only workload cannot measure it,
            # so it stays not_measured here (never pass).
            evals = [
                m.evaluate(crit["r178.error_rate"], statuses=statuses),
                m.evaluate(crit["r178.duplicate_side_effects"],
                           effect_keys=[k for k, v in ev_counts.items() for _ in range(v)]),
                m.evaluate(crit["r178.worker_fairness_jain"], counts=jain_counts),
                m.evaluate(crit["r178.specialization_imbalance"], counts=[]),
                m.evaluate(crit["r178.agents_lost_after_kill"],
                           accepted_ids=sorted(names_before), terminal_ids=sorted(names_after)),
            ]
            for e in evals:
                if e["id"] == "r178.specialization_imbalance":
                    e["why"] = "L0 workload is single-specialization; the criterion needs an equal mix"
        out["criteria"] = evals
        # L0 health: every MEASURABLE criterion passes; specialization_imbalance
        # is expected not_measured (single-specialization workload); recovery
        # loses nothing; nothing dropped; everything accepted and terminal.
        measurable_pass = all(e["verdict"] == "pass" for e in evals
                              if e["id"] != "r178.specialization_imbalance")
        imbalance_honest = all(e["verdict"] == "not_measured" for e in evals
                               if e["id"] == "r178.specialization_imbalance")
        all_terminal = bool(want) and all(s in r.TERMINAL for s in api_state.values()) \
            and len(api_state) == len(want)
        out["l0_healthy"] = (
            level != L0 or (
                out["creation"]["complete"] and dropped == 0
                and measurable_pass and imbalance_honest
                and out["recovery"]["agents_lost"] == 0
                and len(want) == n_tasks and all_terminal))
    finally:
        if sampler:
            sampler.stop()
        sut.stop()
    pathlib.Path(json_out).write_text(json.dumps(out, indent=1))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="R-178 ladder runner (see module docstring)")
    ap.add_argument("--level", type=int, default=L0, choices=LEVELS)
    ap.add_argument("--storage", choices=("volatile", "live-local"), default="volatile")
    ap.add_argument("--tasks", type=int, default=100, help="execution-run task count (L0 default 100)")
    ap.add_argument("--drain-timeout-s", type=float, default=300)
    ap.add_argument("--json", required=True)
    ap.add_argument("--q023-resolved", action="store_true",
                    help="Q-023 (isolated environment + budget) resolved by the owner")
    ap.add_argument("--owner-approved-large-run", action="store_true",
                    help="owner explicitly approved running levels above L0")
    args = ap.parse_args(argv)
    if args.level > L0 and not (args.q023_resolved and args.owner_approved_large_run):
        print(f"REFUSED: level {args.level} > L0 ({L0}). Large runs need the isolated "
              "environment decision (Q-023) — pass BOTH --q023-resolved AND "
              "--owner-approved-large-run to record the owner approval.", file=sys.stderr)
        return 2
    out = run_level(args.level, args.storage, args.tasks, args.drain_timeout_s,
                    args.json, p6_metrics(), r177(), criteria())
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("criteria", "execution")}, indent=1, default=str))
    print("dispatch:", json.dumps(out["execution"]["dispatch_latency"]))
    print("queue_wait:", json.dumps(out["execution"]["queue_wait_s"]))
    for e in out.get("criteria", []):
        print(e["id"], e["verdict"], e["value"], "approved" if e["approved"] else "threshold proposed")
    if args.level == L0:
        return 0 if out.get("l0_healthy") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
