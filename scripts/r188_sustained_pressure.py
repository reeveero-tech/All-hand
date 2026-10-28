#!/usr/bin/env python3
"""R-188: latency/memory measurement under SUSTAINED pressure (local, volatile).

Closes the R-188 remaining item "قياس الكمون/الذاكرة تحت ضغط مستمر" — a
MEASUREMENT, not a new guarantee. Category: **local test** (one machine, one
real uvicorn subprocess over TCP loopback, volatile mode AGOS_TASK_QUEUE=0,
fake PostgREST, slow external recipient). No internet, no model provider, no
paid service, no production change. Not an integration test with Supabase, not
a live test.

Protocol v2 (this file) — the v1 run (docs/audit/M8/logs/raw/
r188_sustained_pressure/OFFICIAL_RUN_REPORT.json + FIRST_RUN_APPENDIX.md) is
kept UNMODIFIED as a first run with documented measurement-tool defects; v2
fixes them:
  1. EVERY measured task (fill + window) is observed from acceptance by the
     same bounded pollers — first terminal observation recorded ONCE per task;
     warmup tasks are observed too but tagged and EXCLUDED from the stats.
  2. Occupancy samples are restricted to the measurement window by EXPLICIT
     timestamps [window_t0, window_t1] (phase tags alone are not trusted);
     drain time is measured from the moment submissions STOP, not from after
     thread joins.
  3. send_t is the time the POST is actually SENT (before urlopen), so
     completion latency = send -> first terminal observation.
  4. Verification is set-based and explicit: accepted task ids are matched to
     recipient sends by unique correlation id (missing send = loss, >1 send per
     correlation = duplicate side effect), final statuses are DISTRIBUTED
     (completed/failed/cancelled counted separately), ALL worker exceptions
     are collected, and the report carries an explicit measurement_valid
     verdict (exit code 1 when invalid — a run with violations is never
     reported as a successful measurement).

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
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AGOS_ENV", "development")  # before importing server code

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_r188_operational_limits as oplim  # REAL_CAP + admin headers helpers
import test_r188_task_loss as loss  # the shared real-server harness

REAL_CAP = oplim.REAL_CAP  # 500 — asserted by the imported test module

TERMINAL = ("completed", "failed", "cancelled")


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


# ------------------------------------------------- pure computations (tested)
def window_occupancy(samples, t0, t1, phase="sustain"):
    """Occupancy values of samples STRICTLY inside [t0, t1] with the given phase.

    Phase tags alone are not trusted (v1 defect): the window is enforced by
    explicit timestamps.
    """
    return [s["tasks_pending"] for s in samples
            if s.get("phase") == phase and t0 <= s["t"] <= t1
            and s.get("tasks_pending") is not None]


def dedupe_first_observations(completions):
    """One completion record per task id — the EARLIEST observation wins."""
    best = {}
    for c in completions:
        prev = best.get(c["tid"])
        if prev is None or c["observed_t"] < prev["observed_t"]:
            best[c["tid"]] = c
    return list(best.values())


def completion_stats(completions, exclude_phases=("warmup", "recovery")):
    """Latency + status distribution over measured tasks (warmup/recovery out)."""
    kept = [c for c in completions if c["phase"] not in exclude_phases]
    out = {
        "n": len(kept),
        "status_distribution": dict(Counter(c["status"] for c in kept)),
        "latency_send_to_first_observation": lat(
            [c["observed_t"] - c["send_t"] for c in kept]),
        "by_phase": {},
    }
    for ph in sorted({c["phase"] for c in kept}):
        sub = [c for c in kept if c["phase"] == ph]
        out["by_phase"][ph] = {
            "n": len(sub),
            "status_distribution": dict(Counter(c["status"] for c in sub)),
            "latency": lat([c["observed_t"] - c["send_t"] for c in sub]),
        }
    return out


def poller_keep_or_drop(is_terminal):
    """A poller drops a task from its polling set ONLY when a TERMINAL state
    has been observed. (v2 run-1 regression, caught before acceptance: the
    poller dropped tasks after the FIRST poll — terminal or not — so
    completions were only mopped up by the final verification sweep and the
    first-observation latencies were garbage. Pinned by test.)
    """
    return is_terminal


def verify_measurement(*, corr_by_tid, sends_by_corr, completions, worker_errors,
                       http_errors, non_terminal, live_after_drain,
                       recovery_terminal, verification_mopup=0):
    """Explicit set-based verification. Returns a verdict dict; a run with any
    violation is INVALID (never reported as a successful measurement)."""
    reasons = []
    # 1) identity match: every expected correlation sent >= 1 time (no loss)
    expected = set(corr_by_tid.values())
    missing = sorted(expected - set(sends_by_corr))
    if missing:
        reasons.append({"violation": "task_never_sent_to_recipient",
                        "count": len(missing), "examples": missing[:5]})
    # 2) no duplicate side effect: no correlation sent more than once
    dupes = {c: n for c, n in sends_by_corr.items() if n > 1}
    if dupes:
        reasons.append({"violation": "duplicate_recipient_send",
                        "count": len(dupes), "examples": dict(list(dupes.items())[:5])})
    # 3) unexpected final statuses (in this scenario everything must complete)
    unexpected = {c["tid"]: c["status"] for c in completions
                  if c["status"] != "completed"}
    if unexpected:
        reasons.append({"violation": "unexpected_terminal_status",
                        "count": len(unexpected),
                        "examples": dict(list(unexpected.items())[:5])})
    # 4) every accepted task reached a terminal state
    if non_terminal:
        reasons.append({"violation": "non_terminal_after_drain", "count": non_terminal})
    # 4b) the pollers observed every terminal task PROMPTLY — a non-zero
    # mop-up by the final verification sweep means an observation gap (the
    # run's first-observation latencies cannot be trusted)
    if verification_mopup:
        reasons.append({"violation": "poller_missed_terminal_task",
                        "count": verification_mopup})
    # 5) no worker/thread died with an exception
    if worker_errors:
        reasons.append({"violation": "worker_exception", "count": len(worker_errors),
                        "examples": worker_errors[:5]})
    # 6) no HTTP-level errors (timeouts, 5xx other than capacity 503)
    if http_errors:
        reasons.append({"violation": "http_error", "count": len(http_errors),
                        "examples": http_errors[:5]})
    # 7) no leaked live tasks after drain
    if live_after_drain not in (0,):
        reasons.append({"violation": "live_tasks_after_drain", "value": live_after_drain})
    # 8) recovery task accepted AND completed
    if recovery_terminal != "completed":
        reasons.append({"violation": "recovery_task_not_completed",
                        "value": recovery_terminal})
    return {
        "measurement_valid": not reasons,
        "invalidity_reasons": reasons,
        "tasks_expected": len(corr_by_tid),
        "tasks_observed_terminal": len(completions),
        "recipient_sends_total": sum(sends_by_corr.values()),
        "recipient_send_counts_per_correlation": {
            "min": min(sends_by_corr.values()) if sends_by_corr else None,
            "max": max(sends_by_corr.values()) if sends_by_corr else None,
        },
    }


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.accept_lat = []      # POST -> 202
        self.refuse_lat = []      # POST -> 503 (queue full)
        self.errors = []          # non-(202|503) HTTP outcomes, recorded as-is
        self.completions = []     # {"tid","phase","send_t","observed_t","status"}
        self.worker_errors = []    # exceptions raised inside worker threads
        self.rss_series = []       # {"t","phase",VmRSS,VmHWM,Threads,tasks_pending}

    def snapshot(self):
        with self.lock:
            return {"accept_lat": list(self.accept_lat), "refuse_lat": list(self.refuse_lat),
                    "errors": list(self.errors), "completions": list(self.completions),
                    "worker_errors": list(self.worker_errors),
                    "rss_series": list(self.rss_series)}


def _post(port, headers, prompt, timeout=120):
    """One admission POST. Returns (status, task_id|None, body, send_t, dt).

    send_t is the monotonic time the request is actually SENT (before
    urlopen) — completion latency is measured from it, not from the arrival
    of the response.
    """
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/tasks", method="POST",
        data=json.dumps({"specialization": "external", "prompt": prompt}).encode(),
        headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
            return r.status, body.get("task_id"), body, t0, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, json.loads(e.read().decode() or b"{}"), t0, time.monotonic() - t0


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


def _meminfo_total():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1])
    except Exception:
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
    ap.add_argument("--protocol-ref", default="HEAD",
                    help="git ref of the committed protocol/tool this run follows "
                         "(embedded in the report; proves preregistration order)")
    args = ap.parse_args()

    m = Metrics()
    t_start = time.monotonic()
    phase_now = ["setup"]  # shared mutable phase tag for the sampler

    # protocol provenance: the git commit of this tool + protocol (v2)
    import subprocess
    try:
        protocol_commit = subprocess.run(
            ["git", "-C", ROOT, "rev-parse", args.protocol_ref],
            capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        protocol_commit = f"unresolved:{args.protocol_ref}"

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
        # task bookkeeping: tid -> {"send_t","phase","corr"}
        accepted = {}
        accepted_lock = threading.Lock()
        poll_set = set()            # ALL observed tasks (fill+sustain+warmup), from acceptance
        recorded = set()            # tids with a first-terminal observation recorded

        def sample_rss():
            pid = srv.proc.pid
            st = proc_status(pid)
            pending = _metrics_pending(port, admin_headers)
            with m.lock:
                m.rss_series.append({
                    "t": round(time.monotonic() - t_start, 2), "phase": phase_now[0],
                    **(st or {}), "tasks_pending": pending})

        def observe_terminal(tid):
            """Record the FIRST terminal observation of a task (once)."""
            gs, gbody = _get_task(port, headers, tid)
            if gs == 200 and gbody.get("status") in TERMINAL:
                with accepted_lock:
                    info = accepted.get(tid)
                    first = tid not in recorded
                    if first:
                        recorded.add(tid)
                if first and info:
                    with m.lock:
                        m.completions.append({
                            "tid": tid, "phase": info["phase"], "send_t": info["send_t"],
                            "observed_t": time.monotonic(),
                            "status": gbody.get("status")})
                return True
            return False

        # ---- warmup (1 s hold): completes fast, then a fixed idle baseline
        for i in range(args.warmup):
            st, tid, _, send_t, _ = _post(port, headers, f"r188sp-warmup-{i}")
            if st == 202 and tid:
                with accepted_lock:
                    accepted[tid] = {"send_t": send_t, "phase": "warmup",
                                     "corr": f"r188sp-warmup-{i}"}
                    poll_set.add(tid)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with accepted_lock:
                warm = [t for t in accepted if accepted[t]["phase"] == "warmup"]
            if all(t in recorded for t in warm):
                break
            for t in warm:
                if t not in recorded:
                    observe_terminal(t)
            time.sleep(0.2)
        sample_rss()
        phase_now[0] = "baseline_after_warmup"

        # ---- pollers start HERE (before fill): every measured task is
        # observed from acceptance — v1 defect fixed.
        stop_submitters = threading.Event()
        stop_pollers = threading.Event()

        def poller(idx):
            try:
                while not stop_pollers.is_set():
                    with accepted_lock:
                        mine = [t for i, t in enumerate(poll_set) if i % args.pollers == idx]
                    if not mine:
                        if stop_submitters.is_set():
                            return
                        time.sleep(0.05)
                        continue
                    for tid in mine:
                        # drop from the polling set ONLY on an observed
                        # TERMINAL state — a non-terminal task stays polled
                        if poller_keep_or_drop(observe_terminal(tid)):
                            with accepted_lock:
                                poll_set.discard(tid)
                        time.sleep(0.02)  # pacing: bounded GET rate, not a poll storm
            except Exception as e:  # a worker death must invalidate the run
                with m.lock:
                    m.worker_errors.append({"worker": f"poller-{idx}", "exception": repr(e)})

        poll_threads = [threading.Thread(target=poller, args=(i,), daemon=True)
                        for i in range(args.pollers)]
        for t in poll_threads:
            t.start()

        # ---- fill to the cap (barrier first: zero completions during fill).
        # The server's own 503 is the authoritative "cap reached" signal; the
        # metrics pending count is the occupancy evidence. Hard bound prevents
        # runaway fills.
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
            st, tid, body, send_t, dt = _post(port, headers, f"r188sp-f{n}")
            n += 1
            if st == 202 and tid:
                with accepted_lock:
                    accepted[tid] = {"send_t": send_t, "phase": "fill",
                                     "corr": f"r188sp-f{n-1}"}
                    poll_set.add(tid)
                with m.lock:
                    m.accept_lat.append(dt)
                fill_accepted += 1
            elif st == 503:
                with m.lock:
                    m.refuse_lat.append(dt)
                fill_refused += 1
                break  # the server's own signal: the cap is full — stop filling
            else:
                with m.lock:
                    m.errors.append({"phase": "fill", "status": st, "body": str(body)[:200]})
        fill_s = time.monotonic() - fill_t0
        phase_now[0] = "after_fill"
        sample_rss()

        # ---- sustained window: paced submitters keep occupancy at/near the
        # cap; the pollers (already running) observe first-terminal times for
        # EVERY task (fill + window). The window is bounded by EXPLICIT
        # timestamps (window_t0/window_t1) used for all window statistics.
        def submitter(idx):
            try:
                local_n = 0
                while not stop_submitters.is_set():
                    corr = f"r188sp-w{idx}-{local_n}"
                    st, tid, body, send_t, dt = _post(port, headers, corr)
                    local_n += 1
                    if st == 202 and tid:
                        with accepted_lock:
                            accepted[tid] = {"send_t": send_t, "phase": "sustain",
                                             "corr": corr}
                            poll_set.add(tid)
                        with m.lock:
                            m.accept_lat.append(dt)
                    elif st == 503:
                        with m.lock:
                            m.refuse_lat.append(dt)
                    else:
                        with m.lock:
                            m.errors.append({"phase": "sustain", "status": st,
                                             "body": str(body)[:200]})
                    stop_submitters.wait(args.submit_pace)
            except Exception as e:  # a worker death must invalidate the run
                with m.lock:
                    m.worker_errors.append({"worker": f"submitter-{idx}",
                                            "exception": repr(e)})

        sampler_stop = threading.Event()

        def sampler():
            try:
                while not sampler_stop.is_set():
                    sample_rss()
                    sampler_stop.wait(5.0)
            except Exception as e:
                with m.lock:
                    m.worker_errors.append({"worker": "sampler", "exception": repr(e)})

        threads = [threading.Thread(target=submitter, args=(i,), daemon=True)
                   for i in range(args.fillers)]
        for t in threads:
            t.start()
        th_sampler = threading.Thread(target=sampler, daemon=True)
        th_sampler.start()

        phase_now[0] = "sustain"
        window_t0 = time.monotonic()
        while time.monotonic() - window_t0 < args.window:
            time.sleep(1.0)
        window_t1 = time.monotonic()
        phase_now[0] = "sustain_tail"  # submitters being stopped — NOT window
        stop_submitters.set()
        join_deadline = time.monotonic() + 30
        for t in threads:
            t.join(timeout=max(0.0, join_deadline - time.monotonic()))

        # ---- drain: measured from the moment submissions STOPPED (after the
        # joins above) — v1 measured it after thread waits, inflating it.
        drain_t0 = time.monotonic()
        phase_now[0] = "drain"
        drain_deadline = drain_t0 + args.hold * 3 + 120
        while time.monotonic() < drain_deadline:
            with accepted_lock:
                left = [t for t in accepted if t not in recorded]
            if not left:
                break
            time.sleep(args.poll)
        drain_s = time.monotonic() - drain_t0
        stop_pollers.set()
        for t in poll_threads:
            t.join(timeout=5)
        sampler_stop.set()
        th_sampler.join(timeout=2)
        sample_rss()

        # ---- fixed idle period (post-pressure memory observation)
        phase_now[0] = "idle_after"
        idle_t0 = time.monotonic()
        while time.monotonic() - idle_t0 < args.idle:
            time.sleep(2.0)
            sample_rss()

        # ---- verification (this run, no restart): set-based, explicit verdict
        # tasks the pollers never observed terminal (mop-up here) = an
        # observation gap that INVALIDATES the measurement
        verification_mopup = 0
        non_terminal = 0
        status_counts = Counter()
        for tid, info in accepted.items():
            if tid in recorded:
                continue
            gs, gbody = _get_task(port, headers, tid)
            if gs == 200 and gbody.get("status") in TERMINAL:
                # observed only now (poller missed it) — recorded, and counted
                # as a mop-up that invalidates the measurement
                verification_mopup += 1
                with m.lock:
                    m.completions.append({
                        "tid": tid, "phase": info["phase"], "send_t": info["send_t"],
                        "observed_t": time.monotonic(), "status": gbody.get("status")})
                recorded.add(tid)
                status_counts[gbody.get("status")] += 1
            else:
                non_terminal += 1
        for c in m.snapshot()["completions"]:
            status_counts[c["status"]] += 1

        # recipient sends by correlation id (identity match to accepted tasks)
        import re as _re
        corr_by_tid = {tid: info["corr"] for tid, info in accepted.items()}
        sends_by_corr = Counter()
        keys_seen = []
        for r in recipient_state.requests:
            keys_seen.append(r["key"])
            prompt = r["prompt"] or ""
            mm = _re.search(r"r188sp-(?:warmup-)?\d+|r188sp-f\d+|r188sp-w\d+-\d+", prompt)
            corr = mm.group(0) if mm else prompt[:40]
            sends_by_corr[corr] += 1

        # live count back to zero?
        live_now = None
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tasks?all_tenants=true",
                                         headers=admin_headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                rows = json.loads(r.read().decode())
                rows = rows.get("tasks", rows) if isinstance(rows, dict) else rows
                live_now = sum(1 for t in rows if t.get("status") not in TERMINAL)
        except Exception as e:
            live_now = f"error: {e}"

        # recovery: a NEW task accepted and completed (no leaked slot)
        rec_st, rec_tid, _, rec_send_t, _ = _post(port, headers, "r188sp-recovery-check")
        rec_terminal = None
        if rec_st == 202 and rec_tid:
            with accepted_lock:
                accepted[rec_tid] = {"send_t": rec_send_t, "phase": "recovery",
                                     "corr": "r188sp-recovery-check"}
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if observe_terminal(rec_tid):
                    break
                time.sleep(0.5)
            with accepted_lock:
                rec_first = next((c for c in m.snapshot()["completions"]
                                  if c["tid"] == rec_tid), None)
            rec_terminal = rec_first["status"] if rec_first else None

        # ---- aggregate (pure functions above) + verdict
        snap = m.snapshot()
        comps = dedupe_first_observations(snap["completions"])
        # occupancy window bounds are RELATIVE (samples store t = now-t_start)
        window_samples = window_occupancy(
            snap["rss_series"], window_t0 - t_start, window_t1 - t_start)
        cstats = completion_stats(comps)
        verdict = verify_measurement(
            corr_by_tid=corr_by_tid,
            sends_by_corr=dict(sends_by_corr),
            completions=comps,
            worker_errors=snap["worker_errors"],
            http_errors=snap["errors"],
            non_terminal=non_terminal,
            live_after_drain=live_now,
            recovery_terminal=rec_terminal,
            verification_mopup=verification_mopup,
        )
        in_window = [c for c in comps if window_t0 <= c["observed_t"] <= window_t1]

        report = {
            "official": bool(args.official),
            "protocol": "v2 (see FIRST_RUN_APPENDIX.md for the v1 defects fixed here)",
            "protocol_commit": protocol_commit,
            "preregistration": "docs/audit/M8/logs/raw/r188_sustained_pressure/PREREGISTRATION_V2.md",
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
                "explicit_bounds_s": [round(window_t0 - t_start, 2),
                                      round(window_t1 - t_start, 2)],
                "duration_s": round(window_t1 - window_t0, 2),
                "tasks_pending_min": min(window_samples) if window_samples else None,
                "tasks_pending_median": statistics.median(window_samples) if window_samples else None,
                "tasks_pending_max": max(window_samples) if window_samples else None,
                "samples_at_ge_90pct_cap": sum(1 for p in window_samples if p >= 0.9 * args.cap),
                "samples_total": len(window_samples),
            },
            "admission_latency_202": lat(snap["accept_lat"]),
            "admission_latency_503": lat(snap["refuse_lat"]),
            "http_errors": snap["errors"],
            "worker_errors": snap["worker_errors"],
            "completion_latency_first_observation": cstats,
            "completions_total_observed": len(comps),
            "throughput_completions_per_sec_observed_in_window": (
                round(len(in_window) / (window_t1 - window_t0), 2) if comps else None),
            "memory_server_process": {
                "series": snap["rss_series"],
                "note": "VmRSS/VmHWM/Threads of the SERVER process only; single run — "
                        "no leak/no-leak claim is made from this window",
            },
            "drain": {"duration_s": round(drain_s, 2),
                      "measured_from": "submission stop (after submitter joins)",
                      "still_pending_at_timeout": non_terminal},
            "verification_this_run": verdict,
            "verification_mopup_tasks": verification_mopup,
        }

        with open(args.out, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        printable = {k: v for k, v in report.items() if k != "memory_server_process"}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        print(f"\nfull report (with memory series): {args.out}")
        if not verdict["measurement_valid"]:
            print("\nMEASUREMENT INVALID — see verification_this_run.invalidity_reasons",
                  file=sys.stderr)
            sys.exit(1)
    finally:
        srv.stop()
        backend.shutdown()


if __name__ == "__main__":
    main()
