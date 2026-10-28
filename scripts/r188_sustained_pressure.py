#!/usr/bin/env python3
"""R-188: latency/memory measurement under SUSTAINED pressure (local, volatile).

Closes the R-188 remaining item "قياس الكمون/الذاكرة تحت ضغط مستمر" — a
MEASUREMENT, not a new guarantee. Category: **local test** (one machine, one
real uvicorn subprocess over TCP loopback, volatile mode AGOS_TASK_QUEUE=0,
fake PostgREST, slow external recipient). No internet, no model provider, no
paid service, no production change. Not an integration test with Supabase, not
a live test.

What it measures (preregistered BEFORE the official run — see
docs/audit/M8/logs/raw/r188_sustained_pressure/PREREGISTRATION.md):
  - admission latency for POST /api/tasks, SEPARATED: accepted (202) vs
    capacity-refused (503); percentiles over ALL requests of each class;
    errors/timeouts recorded as-is;
  - completion latency: submit -> FIRST terminal observation via bounded
    polling of GET /api/tasks/{id} (the number includes polling delay — an
    upper bound on completion, stated as such);
  - throughput: completions/sec observed during the sustained window;
  - occupancy of unfinished tasks during the window (tasks_pending from
    /metrics — verified in server.py to count ALL non-terminal tasks in the
    process registry, i.e. exactly what the volatile cap applies to);
  - the server process's VmRSS/VmHWM/Threads sampled from /proc every 5 s:
    baseline after warmup, the pressure window, the drain, and a FIXED idle
    period after — start/peak/end reported as-is. NO "no memory leak" claim
    is made from a single run of this window;
  - in THIS run: one send per accepted task observed at the recipient (via a
    synthetic unique correlation id in the prompt — the volatile external
    path sends no Idempotency-Key, consistent with the contract), one
    terminal state per accepted task, and post-drain recovery (a NEW task
    accepted and completed; live count back to zero). Named as "observed in
    this run without restart" — NOT a proof of exactly-once in general.

Frozen params (any change after the official run = a NEW documented run):
  --window 120 (official)  --hold 30 (recipient hold per official request)
  --fillers 8  --poll 0.5  --warmup 10 (1 s hold)  --idle 20  --cap 500
Honest limits: single machine, fake backend, volatile mode, ONE run — no
production claim, no multi-host claim, no durable-mode claim.

    python3 scripts/r188_sustained_pressure.py --out report.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AGOS_ENV", "development")  # before importing server code
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_r188_operational_limits as oplim  # REAL_CAP + admin headers helpers
import test_r188_task_loss as loss  # the shared real-server harness

REAL_CAP = oplim.REAL_CAP  # 500 — asserted by the imported test module


# ---------------------------------------------------------------- slow recipient
class _SlowRecipientState:
    """Holds every request for a jittered hold duration; records key + prompt.

    Official requests hold --hold seconds × uniform [0.75, 1.25] (a SEEDED
    deterministic sequence — the graduated release: a uniform hold
    synchronizes completions into waves that oscillate the occupancy; the
    jitter desynchronizes them into the steady state being measured).
    Warmup requests hold a flat 1 s (fast baseline, not part of the window).
    """

    def __init__(self, hold_s: float, seed: int):
        import random
        self.hold_s = hold_s
        self.rand = random.Random(seed)
        self.requests = []  # {"key": str|None, "prompt": str} per call
        self.lock = threading.Lock()

    def record(self, key, prompt):
        with self.lock:
            self.requests.append({"key": key, "prompt": prompt})


class _SlowRecipient(BaseHTTPRequestHandler):
    state: _SlowRecipientState = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key")
        prompt = ""
        try:
            prompt = str((body.get("messages") or [{}])[0].get("content", ""))
        except Exception:
            pass
        self.state.record(key, prompt)
        # warmup requests are identified by their correlation prefix (flat 1 s)
        if "r188sp-warmup-" in prompt:
            hold = 1.0
        else:
            with self.state.lock:
                hold = self.state.hold_s * (0.75 + 0.5 * self.state.rand.random())
        time.sleep(hold)
        out = json.dumps({"choices": [
            {"message": {"role": "assistant", "content": "r188 sustained run done"}}
        ]}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a client that died mid-hold is fine here


def _slow_recipient(hold_s: float, seed: int = 20261028):
    state = _SlowRecipientState(hold_s, seed)
    handler = type("_SlowRecipient_r188sp", (_SlowRecipient,), {"state": state})

    class _Srv(ThreadingHTTPServer):
        request_queue_size = 2048  # ~cap concurrent holds, like the oplimits harness
        daemon_threads = True

    httpd = _Srv(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return state, f"http://127.0.0.1:{httpd.server_address[1]}/run"


# ---------------------------------------------------------------- helpers
def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))], 4)


def lat(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99),
            "max": round(max(xs), 4) if xs else None,
            "mean": round(statistics.fmean(xs), 4) if xs else None}


def proc_status(pid: int):
    try:
        out = {}
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("VmRSS", "VmHWM", "Threads"):
                    out[k] = int(v.split()[0])
        return out
    except Exception:
        return None


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.accept_lat = []      # POST -> 202
        self.refuse_lat = []      # POST -> 503 (queue full)
        self.errors = []          # POST exceptions/timeouts, recorded as-is
        self.completions = []     # (submit_t, first_terminal_observation_t)
        self.rss_series = []       # (t_monotonic, phase, VmRSS, VmHWM, Threads, tasks_pending)

    def snapshot(self):
        with self.lock:
            return {"accept_lat": list(self.accept_lat), "refuse_lat": list(self.refuse_lat),
                    "errors": list(self.errors), "completions": list(self.completions),
                    "rss_series": list(self.rss_series)}


def _post(port, headers, prompt, timeout=120):
    """One admission POST. Returns (status, task_id|None, body) — or raises."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/tasks", method="POST",
        data=json.dumps({"specialization": "external", "prompt": prompt}).encode(),
        headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
            return r.status, body.get("task_id"), body, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, json.loads(e.read().decode() or b"{}"), time.monotonic() - t0


def _get_task(port, headers, task_id, timeout=15):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tasks/{task_id}",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or b"{}")


def _metrics_pending(port, admin_headers):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics",
                                     headers=admin_headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            for line in r.read().decode().splitlines():
                # the gauge is exported with the agos_ prefix (metrics.export_prometheus)
                if line.startswith("agos_tasks_pending "):
                    return float(line.split()[1])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- the run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=120.0)
    ap.add_argument("--hold", type=float, default=30.0)
    ap.add_argument("--fillers", type=int, default=8,
                    help="submitter threads during the window (paced, not a refusal storm)")
    ap.add_argument("--submit-pace", type=float, default=0.2,
                    help="sleep between window POSTs per submitter (bounds the POST rate)")
    ap.add_argument("--pollers", type=int, default=12,
                    help="poller threads observing first-terminal times (bounded concurrency)")
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--idle", type=float, default=20.0)
    ap.add_argument("--cap", type=int, default=REAL_CAP)
    ap.add_argument("--official", action="store_true",
                    help="marks the report as the preregistered official run")
    args = ap.parse_args()

    m = Metrics()
    t_start = time.monotonic()

    # 1) fake PostgREST (storage mirror — same scenario as the oplimits tests)
    loss.unit._PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    loss.unit._PostgREST.gets = []
    loss.unit._PostgREST.mode[0] = "ok"
    backend = ThreadingHTTPServer(("127.0.0.1", 0), loss._BarrierPostgREST)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    os.environ["R188_FAKE_URL"] = f"http://127.0.0.1:{backend.server_address[1]}"

    # 2) slow recipient + real server subprocess (volatile mode)
    recipient_state, recipient_url = _slow_recipient(args.hold)
    admin_key = "r188sp-admin-" + secrets.token_urlsafe(12)
    admin_headers = {"X-API-Key": admin_key, "Content-Type": "application/json"}
    srv = loss._Server(env_extra={
        "EXTERNAL_AGENTS": json.dumps([{"name": "r188sp", "url": recipient_url}]),
        "AGOS_TASK_QUEUE": "0",                      # volatile mode (the measured mode)
        "AGOS_RATE_LIMIT_REQUESTS": "100000",       # target = QUEUE cap, not request rate
        "AGOS_API_KEY": admin_key,                   # operator/admin for all_tenants reads
        "AGOS_TASK_LEASE_S": "4",
        "AGOS_TASK_REAP_S": "2",
        "AGOS_TASK_DISPATCH_IDLE_S": "0.25",
    })
    srv.start()
    try:
        headers = dict(loss.HEADERS)
        port = srv.port
        accepted = {}          # task_id -> submit_t
        accepted_lock = threading.Lock()

        def sample_rss(phase):
            pid = srv.proc.pid
            st = proc_status(pid)
            pending = _metrics_pending(port, admin_headers)
            with m.lock:
                m.rss_series.append({
                    "t": round(time.monotonic() - t_start, 2), "phase": phase,
                    **(st or {}), "tasks_pending": pending})

        # ---- warmup (1 s hold): completes fast, then a fixed idle baseline
        for i in range(args.warmup):
            st, tid, _, _ = _post(port, headers, f"r188sp-warmup-{i}")
            if st == 202 and tid:
                with accepted_lock:
                    accepted[tid] = time.monotonic()
        deadline = time.time() + 60
        while time.time() < deadline:
            live = [t for t in accepted if _get_task(port, headers, t)[1].get("status")
                    not in ("completed", "failed", "cancelled")]
            if not live:
                break
            time.sleep(0.5)
        sample_rss("baseline_after_warmup")

        # ---- fill to the cap (barrier first: zero completions during fill).
        # The server's own 503 is the authoritative "cap reached" signal; the
        # metrics pending count (now that the agos_ prefix is parsed) is the
        # occupancy evidence. Hard bound = cap + warmup + 50 (never runaway).
        fill_accepted = 0
        fill_refused = 0
        fill_t0 = time.monotonic()
        n = 0
        hard_bound = args.cap + args.warmup + 50
        while True:
            with accepted_lock:
                if len(accepted) >= hard_bound:
                    break
            pending_now = _metrics_pending(port, admin_headers)
            if pending_now is not None and pending_now >= args.cap:
                break
            st, tid, body, dt = _post(port, headers, f"r188sp-{n}")
            n += 1
            if st == 202 and tid:
                with accepted_lock:
                    accepted[tid] = time.monotonic()
                with m.lock:
                    m.accept_lat.append(dt)
                fill_accepted += 1
            elif st == 503:
                with m.lock:
                    m.refuse_lat.append(dt)
                fill_refused += 1
                # the server's own signal: the cap is full — stop filling
                break
            else:
                with m.lock:
                    m.errors.append({"phase": "fill", "status": st, "body": str(body)[:200]})
        fill_s = time.monotonic() - fill_t0
        sample_rss("after_fill")

        # ---- sustained window: paced submitters keep occupancy at/near the
        # cap; SEPARATE bounded-concurrency pollers observe first-terminal
        # times (a submitter must NOT wait for its task to complete — that
        # collapses the occupancy; the pollers own the waiting).
        poll_set = set()            # accepted, not yet observed terminal
        recorded = set()            # first-terminal observation recorded (once)
        stop_submitters = threading.Event()

        def submitter(idx):
            local_n = 0
            while not stop_submitters.is_set():
                st, tid, body, dt = _post(port, headers, f"r188sp-w{idx}-{local_n}")
                local_n += 1
                if st == 202 and tid:
                    with accepted_lock:
                        accepted[tid] = time.monotonic()
                    with m.lock:
                        m.accept_lat.append(dt)
                    with accepted_lock:
                        poll_set.add(tid)
                elif st == 503:
                    with m.lock:
                        m.refuse_lat.append(dt)
                else:
                    with m.lock:
                        m.errors.append({"phase": "sustain", "status": st,
                                          "body": str(body)[:200]})
                stop_submitters.wait(args.submit_pace)

        def poller(idx):
            while True:
                with accepted_lock:
                    mine = [t for i, t in enumerate(poll_set) if i % args.pollers == idx]
                if not mine and stop_submitters.is_set():
                    return
                for tid in mine:
                    gs, gbody = _get_task(port, headers, tid)
                    if gs == 200 and gbody.get("status") in ("completed", "failed", "cancelled"):
                        with m.lock:
                            if tid not in recorded:
                                recorded.add(tid)
                                m.completions.append((accepted[tid], time.monotonic()))
                        with accepted_lock:
                            poll_set.discard(tid)
                    time.sleep(0.02)  # pacing: bounded GET rate, not a poll storm

        sampler_stop = threading.Event()

        def sampler():
            while not sampler_stop.is_set():
                sample_rss("sustain")
                sampler_stop.wait(5.0)

        threads = [threading.Thread(target=submitter, args=(i,), daemon=True)
                   for i in range(args.fillers)]
        for t in threads:
            t.start()
        poll_threads = [threading.Thread(target=poller, args=(i,), daemon=True)
                       for i in range(args.pollers)]
        for t in poll_threads:
            t.start()
        th_sampler = threading.Thread(target=sampler, daemon=True)
        th_sampler.start()
        window_t0 = time.monotonic()
        while time.monotonic() - window_t0 < args.window:
            time.sleep(1.0)
        window_t1 = time.monotonic()
        stop_submitters.set()
        for t in threads:
            t.join(timeout=10)
        # let the pollers finish what is already terminal, then stop them
        # (a TOTAL deadline — the per-thread 5 s joins once cost 60 s)
        join_deadline = time.monotonic() + 10
        for t in poll_threads:
            t.join(timeout=max(0.0, join_deadline - time.monotonic()))

        # ---- drain: no new admissions; every accepted task reaches terminal
        sampler_stop.set()
        th_sampler.join(timeout=2)
        drain_t0 = time.monotonic()
        drain_deadline = drain_t0 + args.hold * 3 + 120
        still = dict(accepted)
        while still and time.monotonic() < drain_deadline:
            for tid in list(still):
                gs, gbody = _get_task(port, headers, tid)
                if gs == 200 and gbody.get("status") in ("completed", "failed", "cancelled"):
                    with m.lock:
                        if tid not in recorded:  # a poller may have recorded it first
                            recorded.add(tid)
                            m.completions.append((accepted[tid], time.monotonic()))
                    del still[tid]
            if still:
                time.sleep(args.poll)
        drain_s = time.monotonic() - drain_t0
        sample_rss("after_drain")

        # ---- fixed idle period (post-pressure memory observation)
        idle_t0 = time.monotonic()
        while time.monotonic() - idle_t0 < args.idle:
            time.sleep(2.0)
            sample_rss("idle_after")

        # ---- verification (this run, no restart)
        # every accepted task terminal?
        non_terminal = []
        for tid in accepted:
            gs, gbody = _get_task(port, headers, tid)
            if not (gs == 200 and gbody.get("status") in ("completed", "failed", "cancelled")):
                non_terminal.append(tid)
        # recipient: one send per accepted task (correlation id in prompt)
        import re as _re
        sends = {}
        for r in recipient_state.requests:
            mm = _re.search(r"r188sp-(?:warmup-)?\d+|r188sp-w\d+-\d+", r["prompt"] or "")
            corr = (mm.group(0) if mm else (r["prompt"] or "")[:40])
            sends[corr] = sends.get(corr, 0) + 1
        # Idempotency-Key must be ABSENT on the volatile external path (contract)
        keys_seen = [r["key"] for r in recipient_state.requests]
        # live count back to zero?
        live_now = None
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tasks?all_tenants=true",
                                         headers=admin_headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                rows = json.loads(r.read().decode())
                rows = rows.get("tasks", rows) if isinstance(rows, dict) else rows
                live_now = sum(1 for t in rows if t.get("status")
                               not in ("completed", "failed", "cancelled"))
        except Exception as e:
            live_now = f"error: {e}"
        # recovery: a NEW task accepted and completed (no leaked slot)
        rec_st, rec_tid, _, _ = _post(port, headers, "r188sp-recovery-check")
        rec_terminal = None
        if rec_st == 202 and rec_tid:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                gs, gbody = _get_task(port, headers, rec_tid)
                if gs == 200 and gbody.get("status") in ("completed", "failed", "cancelled"):
                    rec_terminal = gbody.get("status")
                    break
                time.sleep(0.5)

        # ---- window occupancy evidence (sustained pressure verification)
        window_samples = [s for s in m.snapshot()["rss_series"] if s["phase"] == "sustain"]
        pendings = [s["tasks_pending"] for s in window_samples if s["tasks_pending"] is not None]

        report = {
            "official": bool(args.official),
            "preregistration": "docs/audit/M8/logs/raw/r188_sustained_pressure/PREREGISTRATION.md",
            "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "category": "local test — one machine, real uvicorn subprocess, volatile mode, "
                        "fake PostgREST, slow recipient; no internet/provider/production",
            "environment": {
                "machine": f"{os.cpu_count()} vCPU",
                "meminfo_total_kb": _meminfo_total(),
                "python": platform.python_version(),
                "kernel": platform.release(),
                "runs": 1,
            },
            "params": {"window_s": args.window, "hold_s": args.hold,
                       "fillers": args.fillers, "poll_s": args.poll,
                       "warmup_tasks": args.warmup, "idle_s": args.idle,
                       "cap": args.cap},
            "fill": {"accepted": fill_accepted, "refused_503": fill_refused,
                     "duration_s": round(fill_s, 2)},
            "sustained_window": {
                "duration_s": round(window_t1 - window_t0, 2),
                "tasks_pending_min": min(pendings) if pendings else None,
                "tasks_pending_median": statistics.median(pendings) if pendings else None,
                "tasks_pending_max": max(pendings) if pendings else None,
                "samples_at_ge_90pct_cap": sum(1 for p in pendings if p >= 0.9 * args.cap),
                "samples_total": len(pendings),
            },
            "admission_latency_202": lat([c for c in m.snapshot()["accept_lat"]]),
            "admission_latency_503": lat([c for c in m.snapshot()["refuse_lat"]]),
            "errors": m.snapshot()["errors"],
            "completion_latency_first_observation": lat(
                [b - a for a, b in m.snapshot()["completions"]]),
            "completions_total_observed": len(m.snapshot()["completions"]),
            "memory_server_process": {
                "series": m.snapshot()["rss_series"],
                "note": "VmRSS/VmHWM/Threads of the SERVER process only; single run — "
                        "no leak/no-leak claim is made from this window",
            },
            "verification_this_run": {
                "accepted_tasks": len(accepted),
                "non_terminal_after_drain": len(non_terminal),
                "recipient_sends_per_correlation_max": max(sends.values()) if sends else None,
                "recipient_requests_total": len(recipient_state.requests),
                "idempotency_key_present_on_volatile_external_path": any(keys_seen),
                "live_tasks_after_drain": live_now,
                "recovery_new_task": {"status": rec_st, "terminal": rec_terminal},
            },
        }
        # throughput over the window: completions FIRST OBSERVED strictly
        # inside the window (the drain is reported separately, never merged)
        comps = m.snapshot()["completions"]
        in_window = [b for a, b in comps if window_t0 <= b <= window_t1]
        report["throughput_completions_per_sec_observed"] = (
            round(len(in_window) / (window_t1 - window_t0), 2) if comps else None)
        report["drain"] = {"duration_s": round(drain_s, 2),
                           "still_pending_at_timeout": len(still)}

        with open(args.out, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps({k: v for k, v in report.items()
                          if k != "memory_server_process"}, ensure_ascii=False, indent=2))
        print(f"\nfull report (with memory series): {args.out}")
    finally:
        srv.stop()
        backend.shutdown()


def _meminfo_total():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1])
    except Exception:
        return None


if __name__ == "__main__":
    main()
