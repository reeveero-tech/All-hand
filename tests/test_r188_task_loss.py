"""R-188 (D-102): task loss on restart in the DEFAULT (volatile) mode.

R-163 built the durable queue (AGOS_TASK_QUEUE=1) and proved survival with
it — but it is OPT-IN. The default mode keeps task state in RAM (TASKS) and
only mirrors status into storage; on a process death mid-task the storage
row is never brought to a terminal state by the next process: no TASKS
entry, no dispatcher for it, no reaper. That silent zombie IS the task loss
R-188 exists to close.

The red test below reproduces the loss with a deterministic, test-controlled
barrier: the fake PostgREST is armed BEFORE the task is created to BLOCK the
first tasks-table PATCH (the mid-flight update — inserts are POSTs and
pass through). The server is then provably inside the task's mid-flight
storage write when the test kills it (SIGKILL); the blocked write is
DISCARDED, never applied (a request that reached the fake is not cancelled
by the client's death — without the discard it could land post-mortem and
make the test pass vacuously); the handler signals the END of the discard
before the row is snapshotted; and the snapshot (a COPY, not a live reference) is
asserted non-terminal. Only then does a fresh process start on the same
backend — any terminal state the row reaches afterwards can only be the
fresh process's own recovery.

Deliberately separated from side-effect duplication (advisor, R-188
scoping): this test proves NO LOSS only — it does not assert attempt counts
or single execution (recovery may re-run; whether side effects may duplicate
is a separate, later test). Scope note (advisor): this is an INITIAL recovery
proof on a simple local task — the full criterion (a) (a MULTI-STAGE task)
is a later item inside R-188.

Honest limits: the backend is the fake PostgREST (this scenario on a real
backend is NOT proven here); the analyst prompt is numeric ("3 4 5") so the
dispatcher's analyst path is the pure-stdlib statistics branch
(real_dispatcher._analyst — no LLM, no network); recovery of rows stuck
BEFORE the activation is untested (not claimed either way); the recovery
time is not measured here.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_r163_durable_queue as unit  # the fake PostgREST + row helper

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _BarrierPostgREST(unit._PostgREST):
    """Fake PostgREST with a test-controlled barrier on tasks-table updates.

    While `armed` is set, any PATCH to the tasks table blocks and — once
    released — is DISCARDED, never applied: the discard decision is made at
    ENTRY (a request that entered the barrier never lands), not by a flag
    the test could clear at the wrong moment. This is what makes the kill
    deterministic: a dead process's write cannot land post-mortem and fake
    a recovery the fresh process never performed. `discarded` is set at the
    END of the discard branch (handler-completed signal — the write is
    provably not going to land). Inserts are POSTs and are never blocked,
    so task creation itself is unaffected. A barrier timeout is LOUD
    (raises) — it never silently lets the write through.
    """

    armed = threading.Event()
    release = threading.Event()
    entered = threading.Event()
    discarded = threading.Event()
    # R-188 recipient stability: when set, a tasks-table PATCH whose body
    # carries a "payload" column (only the recipient-pin write does) fails
    # with 500 — the pin-write-failure condition (zero external sends).
    fail_payload = [False]

    def do_PATCH(self):  # noqa: N802
        table, _ = self._table_q()
        if table == "tasks" and self.armed.is_set():
            self.entered.set()
            if not self.release.wait(timeout=30):
                # LOUD: a silent timeout could let the write pass and the
                # test would prove nothing
                raise RuntimeError("R-188 barrier: released nobody within 30s")
            # entered-while-armed => discarded (never applied)
            try:
                self._send(409)
            except BrokenPipeError:
                pass  # the client is dead — the write is already discarded
            self.discarded.set()  # END of the discard branch
            return
        if table == "tasks" and self.fail_payload[0]:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) or b""
            if b'"payload"' in body:
                self._send(500)
                return
            # not the pin write: apply it here (the body is already read —
            # the base handler cannot re-read it)
            patch = json.loads(body)
            q = self._table_q()[1]
            matched = self._matched(table, q)
            for r in matched:
                r.update(patch)
            if "return=representation" in self.headers.get("Prefer", ""):
                self._send(200, matched)
            else:
                self._send(204)
            return
        try:
            super().do_PATCH()
        except BrokenPipeError:
            pass  # the client died (SIGKILL) mid-write — expected here


class _Server:
    """A real uvicorn subprocess — kill -9 is a genuine process death.

    The DEFAULT configuration: AGOS_TASK_QUEUE is NOT set (no R-163 opt-in).
    """

    def __init__(self, env_extra: dict = None, module: str = "server:app",
                 pythonpath: str = None):
        """env_extra: additional env vars for the subprocess (e.g. EXTERNAL_AGENTS
        for the side-effect test, or a longer lease) — merged AFTER the base
        env so a test can override the timing knobs. None keeps the exact
        behavior the existing tests rely on.

        module: the uvicorn app spec to launch. The default is the real
        server; the multi-stage scenario launches a TEST-ONLY wrapper module
        (injected via pythonpath) that imports the real server and swaps the
        analyst pool for a two-stage test agent — declared scope: the wrapper
        exists to give the durable queue a task whose stage-2 start depends
        on the coordinator process (R-188 full criterion a)."""
        self.port = _free_port()
        self.proc = None
        self.env_extra = dict(env_extra or {})
        self.module = module
        self.pythonpath = pythonpath

    def start(self):
        env = dict(os.environ)
        env.pop("AGOS_TASK_QUEUE", None)  # default mode — no opt-in
        env.update({
            "AGOS_ENV": "development",
            "SUPABASE_URL": os.environ["R188_FAKE_URL"],
            "SUPABASE_SERVICE_KEY": "r188-task-loss-test",
            # timing knobs tuned for a bounded test: the row may freeze as
            # 'claimed' with a lease held by the dead process, so recovery
            # waits for the lease to expire (AGOS_TASK_LEASE_S) and the reaper
            # scan (AGOS_TASK_REAP_S). The default under test is the queue
            # ACTIVATION, not the lease timings.
            "AGOS_TASK_LEASE_S": "4",
            "AGOS_TASK_REAP_S": "2",
            "AGOS_TASK_DISPATCH_IDLE_S": "0.25",
            "AGOS_TENANT_KEYS": json.dumps([{
                "tenant": "r188_tenant",
                "key_sha256": hashlib.sha256(RKEY.encode()).hexdigest(),
                "role": "user"}]),
        })
        env.update(self.env_extra)
        if self.pythonpath:
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (self.pythonpath + os.pathsep + existing) if existing \
                else self.pythonpath
        import tempfile
        self._log = tempfile.NamedTemporaryFile(
            prefix="r188-server-", suffix=".log", delete=False)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", self.module, "--port", str(self.port),
             "--log-level", "warning"],
            cwd=ROOT, env=env,
            stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self._tail()
                raise RuntimeError(f"server died at boot:\n{out}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError(f"server did not become healthy in 60s; server log tail:\n{self._tail()}")

    def _tail(self, n=3000):
        try:
            self._log.flush()
            with open(self._log.name) as f:
                return f.read()[-n:]
        except Exception:
            return "<no server output>"

    def kill(self):
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(timeout=10)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


RKEY = "r188-task-loss-" + secrets.token_urlsafe(12)
HEADERS = {"X-API-Key": RKEY, "Content-Type": "application/json"}


class _EffectServer:
    """The external-agent service — the SIDE-EFFECT TARGET (R-188 criterion
    B). It runs in the TEST process, so it survives the uvicorn subprocess's
    SIGKILL: its counters are independent evidence, never the killed
    process's memory.

    The contract under test: each call is RECORDED (attempt evidence); the
    EFFECT is applied once per idempotency key — a request WITHOUT a key
    cannot be deduplicated and applies again (an external service cannot
    guess the caller's intent). The FIRST request's response is blocked
    (test-controlled): the effect has landed but the run has not recorded
    its success — the exact at-least-once window between "effect applied"
    and "completion stored".
    """

    requests: list = []            # {"key": str|None, "prompt": str} per call
    applied: int = 0               # effects actually applied
    applied_keys: set = set()
    block = threading.Event()       # armed: the FIRST request blocks after applying
    release = threading.Event()

    @classmethod
    def _reset(cls):
        cls.requests = []
        cls.applied = 0
        cls.applied_keys = set()
        cls.block.clear()
        cls.release.clear()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            key = self.headers.get("Idempotency-Key")
            prompt = ""
            try:
                prompt = str((body.get("messages") or [{}])[0].get("content", ""))
            except Exception:
                pass
            _EffectServer.requests.append({"key": key, "prompt": prompt})
            # the effect: applied ONCE per key; a keyless call cannot be
            # deduplicated (the service cannot know it is a re-delivery)
            if not key or key not in _EffectServer.applied_keys:
                _EffectServer.applied += 1
                if key:
                    _EffectServer.applied_keys.add(key)
            if _EffectServer.block.is_set():
                _EffectServer.block.clear()  # only the FIRST request blocks
                if not _EffectServer.release.wait(timeout=30):
                    raise RuntimeError("R-188 effect barrier: released nobody within 30s")
            try:
                out = json.dumps({"choices": [
                    {"message": {"role": "assistant", "content": "r188 side-effect run done"}}
                ]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client died (SIGKILL) mid-call — expected here

        def log_message(self, *args):
            pass

    @classmethod
    def serve(cls):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd


def _api(port, method, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


# ------------------------------------------------- full criterion (a): a
# MULTI-STAGE task whose stage-2 START depends on the coordinator process.
# The system has no built-in multi-stage task execution (one agent.execute()
# call per task), so the scenario runs a TEST-ONLY wrapper module inside the
# uvicorn subprocess (declared scope — no production code is built for the
# test): the wrapper swaps the analyst pool for a two-stage test agent whose
# work is (1) stage A via HTTP, (2) a GATE the test controls, (3) stage B via
# HTTP. The kill lands AFTER stage A's result is back in the killed process
# and BEFORE stage B is requested — the transition to stage 2 depends on the
# process being killed, which is what distinguishes this from the single-call
# side-effect window of criterion (b).

class _MultiStageServer:
    """The two-stage work service (test process — survives the subprocess's
    SIGKILL, so its counters are independent evidence). Endpoints:

    - /stage-a and /stage-b: the WORK stages. Each applies its effect ONCE
      per idempotency key (stage A: record the input; stage B: produce the
      output) and answers 200 — the stage's RESULT is received by the CALLER
      (the coordinator), which is what proves the stage completed there.
    - /gate: the barrier between the stages. The FIRST entry blocks until
      the test releases it; later entries pass. The coordinator is provably
      BETWEEN the stages while blocked here.
    """

    requests: dict = {"stage-a": [], "gate": [], "stage-b": []}
    applied: dict = {"stage-a": 0, "stage-b": 0}      # effects actually applied
    applied_keys: dict = {"stage-a": set(), "stage-b": set()}
    aborted: list = []                                   # client-side cancel witnesses
    gate_armed = threading.Event()
    gate_release = threading.Event()

    @classmethod
    def _reset(cls):
        cls.requests = {"stage-a": [], "gate": [], "stage-b": []}
        cls.applied = {"stage-a": 0, "stage-b": 0}
        cls.applied_keys = {"stage-a": set(), "stage-b": set()}
        cls.aborted = []
        cls.gate_armed.clear()
        cls.gate_release.clear()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length)
            stage = self.path.lstrip("/")
            if stage == "aborted":
                # the CLIENT-side cancellation witness: the run's coroutine
                # was cancelled (recorded from inside the killed/cancelling
                # process, not from this server's own connection state)
                try:
                    _MultiStageServer.aborted.append(
                        json.loads(raw or b"{}").get("tid"))
                except Exception:
                    pass
                out = json.dumps({"ok": True}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            # consume the request body (the stage is keyed by header, not body)
            key = self.headers.get("Idempotency-Key")
            if stage not in ("stage-a", "gate", "stage-b"):
                self.send_response(404)
                self.end_headers()
                return
            _MultiStageServer.requests[stage].append({"key": key})
            if stage in ("stage-a", "stage-b"):
                if not key or key not in _MultiStageServer.applied_keys[stage]:
                    _MultiStageServer.applied[stage] += 1
                    if key:
                        _MultiStageServer.applied_keys[stage].add(key)
            if stage == "gate" and _MultiStageServer.gate_armed.is_set():
                _MultiStageServer.gate_armed.clear()  # only the FIRST entry blocks
                if not _MultiStageServer.gate_release.wait(timeout=30):
                    raise RuntimeError("R-188 gate: released nobody within 30s")
            out = json.dumps({"ok": True}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client died (SIGKILL) mid-call — expected here

        def log_message(self, *args):
            pass

    @classmethod
    def serve(cls):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd


# The wrapper module launched as the uvicorn app (written to tmp_path at
# test setup, injected via PYTHONPATH). TEST-ONLY — declared scope above.
_MULTISTAGE_WRAPPER_PY = '''"""R-188 test-only wrapper (NOT production code): imports the real server
and swaps the analyst pool for a two-stage agent so the durable queue can be
proven on a task whose stage-2 start depends on the coordinator process.
Also swaps the builder pool for a sleepy agent — the RECOVERY positive
control (a row left claimed/running with a lease by a killed process that the
next process's reaper MUST recover; proves the recovery machinery is alive)."""
import asyncio
import json
import os
import urllib.request

import aiohttp

import server

_EFFECT_URL = os.environ["R188_EFFECT_URL"]


def _aborted_marker(tid):
    """Execution-end witness (sync, best-effort): the run's coroutine was
    CANCELLED at the gate — recorded from the CLIENT side, because waiting
    for the gate handler on the server side alone proves nothing about the
    client coroutine's state."""
    try:
        req = urllib.request.Request(
            _EFFECT_URL + "/aborted", method="POST",
            data=json.dumps({"tid": tid}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # never mask the cancellation itself


class R188TwoStageAgent:
    name = "r188-two-stage-agent"
    specialization = "analyst"
    status = "idle"
    stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    async def execute(self, work):
        import time
        started = time.time()
        tid = str(work.get("task_id") or "")
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60.0)) as client:
            # stage 1: performed AND completed by THIS process (the result
            # comes back here)
            await client.post(_EFFECT_URL + "/stage-a",
                             json={"prompt": work.get("prompt", "")},
                             headers={"Idempotency-Key": tid + ":a"})
            # the gate: stage 2 begins only after the test releases it —
            # while blocked here the coordinator is provably BETWEEN stages
            try:
                await client.post(_EFFECT_URL + "/gate",
                                 json={"prompt": work.get("prompt", "")},
                                 headers={"Idempotency-Key": tid + ":gate"})
            except asyncio.CancelledError:
                # execution ended BY THE CANCELLATION, at the checkpoint
                # boundary (between the stages) — client-side witness
                _aborted_marker(tid)
                raise
            # stage 2
            await client.post(_EFFECT_URL + "/stage-b",
                             json={"prompt": work.get("prompt", "")},
                             headers={"Idempotency-Key": tid + ":b"})
        return {"ok": True, "agent": self.name,
                "elapsed_s": round(time.time() - started, 3),
                "result": {"answer": "r188 multistage done"}}


class R188SleepyAgent:
    """The recovery positive control: a task still mid-execution when its
    process is SIGKILLed (row claimed/running with a live lease) — the next
    process's reaper MUST recover it."""
    name = "r188-sleepy-agent"
    specialization = "builder"
    status = "idle"
    stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    async def execute(self, work):
        import time
        started = time.time()
        await asyncio.sleep(float(os.environ.get("R188_SLEEP_S", "20")))
        return {"ok": True, "agent": self.name,
                "elapsed_s": round(time.time() - started, 3),
                "result": {"answer": "r188 sleepy done"}}


server.CIVILIZATION["analyst"] = [R188TwoStageAgent()]
server.CIVILIZATION["builder"] = [R188SleepyAgent()]
app = server.app
'''


def test_r188_cancellation_stops_running_task_at_checkpoint_boundary(
        fake_backend, tmp_path):
    """R-188 cancellation criterion: cancelling a RUNNING task stops it AT
    THE CHECKPOINT BOUNDARY — the prior effect stays (no rollback invented),
    and the work after the tested boundary NEVER STARTS. The proof avoids the
    trap of hiding a cancellation failure behind a closed gate or a kill:

    1. CONTROL (no cancel): a two-stage task blocked at the gate is RELEASED
       and stage 2 DOES start — proving an open gate allows stage 2, so the
       main scenario's stop can only be the cancellation, never the barrier.
    2. MAIN: a second two-stage task is provably between the stages (stage A
       done BY the coordinator, blocked at the gate, row running with the
       claim fence and a valid lease) — then CANCELLED while the coordinator
       is STILL ALIVE and the gate is OPENED: the run's coroutine is provably
       ended BY THE CANCELLATION (a client-side witness recorded from inside
       the cancelling process), and stage 2 never begins.
    3. NO RESURRECTION: the coordinator is then SIGKILLed and a fresh process
       recovers ANOTHER task (the positive control — a sleepy task left
       mid-execution with a live lease: its recovery proves the fresh
       process's reaper is alive and would have resurrected anything
       recoverable), while the cancelled row stays cancelled across that very
       recovery cycle: no redelivery of stage A, no stage 2, the public
       mapping still `failed`/error `cancelled`.

    Honest limits: this proves the cancellation boundary semantics of the
    TEST two-stage agent (declared scope — the system has no built-in
    multi-stage execution); the observation window is bounded (the positive
    control's full recovery cycle), not a forever-proof or a general
    at-most-once guarantee; no rollback of effects already sent is claimed.
    """
    wrapper_dir = str(tmp_path)
    with open(os.path.join(wrapper_dir, "r188_ms_wrapper.py"), "w") as f:
        f.write(_MULTISTAGE_WRAPPER_PY)
    _MultiStageServer._reset()
    work_httpd = _MultiStageServer.serve()
    work_url = f"http://127.0.0.1:{work_httpd.server_address[1]}"
    env_extra = {"R188_EFFECT_URL": work_url, "AGOS_TASK_LEASE_S": "8"}
    kwargs = dict(env_extra=env_extra, module="r188_ms_wrapper:app",
                 pythonpath=wrapper_dir)
    srv = _Server(**kwargs)
    try:
        srv.start()
        # ---- 1. CONTROL (no cancel): an OPEN GATE ALLOWS stage 2 -----------
        _MultiStageServer.gate_armed.set()
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "control no cancel"})
        assert status == 202, body
        control_tid = body["task_id"]
        assert _wait_for(lambda: any(
            r["key"] == f"{control_tid}:gate"
            for r in _MultiStageServer.requests["gate"]), timeout=30), \
            "control: never reached the gate"
        _MultiStageServer.gate_release.set()  # open the gate — NO cancel sent
        assert _wait_for(lambda: any(
            r["key"] == f"{control_tid}:b"
            for r in _MultiStageServer.requests["stage-b"]), timeout=30), \
            "control: an open gate did NOT allow stage 2 — the barrier " \
            "cannot distinguish anything in the main scenario"
        assert _wait_for(lambda: any(
            r["task_id"] == control_tid and r["status"] == "completed"
            for r in _row_rows()), timeout=30), \
            "control: the released task never completed"
        # ---- 2. MAIN: cancel a task provably BETWEEN the stages -------------
        # the positive control first: a sleepy task that will still be
        # mid-execution (row claimed/running with a live lease) when the
        # coordinator is killed — the fresh process MUST recover it
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "builder", "prompt": "sleepy"})
        assert status == 202, body
        sleepy_tid = body["task_id"]
        assert _wait_for(lambda: next(
            (r["status"] for r in _row_rows() if r["task_id"] == sleepy_tid),
            None) == "running", timeout=30), \
            "sleepy positive control never started running"
        _MultiStageServer.gate_release.clear()  # re-arm BOTH: the release
        _MultiStageServer.gate_armed.set()      # event must be unset again,
        # or the main task's gate entry would pass straight through
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "cancel at checkpoint"})
        assert status == 202, body
        tid = body["task_id"]
        # the premise: stage A completed BY the coordinator, blocked at the
        # gate, stage 2 not started, row running with the claim fence
        assert _wait_for(lambda: any(
            r["key"] == f"{tid}:a" for r in _MultiStageServer.requests["stage-a"])
            and any(r["key"] == f"{tid}:gate"
                    for r in _MultiStageServer.requests["gate"]), timeout=30), \
            "the run never reached the gate between the stages"
        assert not any(r["key"] == f"{tid}:b"
                       for r in _MultiStageServer.requests["stage-b"]), \
            "premise broken: stage 2 already started"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "running", \
            f"premise broken: the row is {row['status']!r}, not running"
        from datetime import datetime, timezone
        assert row.get("claim_token"), \
            "premise broken: no claim token on the running row"
        assert datetime.fromisoformat(row["lease_expires_at"]) \
            > datetime.now(timezone.utc), \
            "premise broken: the lease already expired"
        # CANCEL while the coordinator is ALIVE (the gate stays closed until
        # the next step — the stop must be the cancellation, not the barrier)
        status, body = _api(srv.port, "POST", f"/api/tasks/{tid}/cancel", None)
        assert status == 200, f"cancel answered {status}, contract says 200"
        assert body.get("status") == "failed" and body.get("cancelled") is True, body
        # NOW open the gate — the coordinator is still alive: if the
        # cancellation failed to stop the run, stage 2 would start here
        _MultiStageServer.gate_release.set()
        # the client-side witness: the run's coroutine was cancelled AT the
        # gate (recorded from inside the cancelling process)
        assert _wait_for(lambda: tid in _MultiStageServer.aborted, timeout=30), \
            "no client-side cancellation witness: the run's coroutine was " \
            "not provably ended by the cancellation"
        assert not any(r["key"] == f"{tid}:b"
                       for r in _MultiStageServer.requests["stage-b"]), \
            "R-188 cancellation violation: stage 2 STARTED after the cancel " \
            "(gate open, coordinator alive) — the task did not stop at the " \
            "checkpoint boundary"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "cancelled", \
            f"stored status is {row['status']!r}, not cancelled"
        status, mem = _api(srv.port, "GET", f"/api/tasks/{tid}")
        assert status == 200 and mem["status"] == "failed" \
            and mem["result"]["result"]["error"] == "cancelled"
        # ---- 3. NO RESURRECTION across a real recovery cycle ----------------
        # kill the coordinator: the sleepy positive control is mid-execution
        # (row claimed/running, live lease) — the fresh process MUST recover
        # it; the cancelled row must stay cancelled through that very cycle
        srv.kill()
        # POST-MORTEM on the positive control (independent copy, read from
        # the fake backend after the death): it must STILL be mid-execution
        # AT THE KILL — non-terminal row with the claim fence. Without this,
        # the later `completed` could have landed in the FIRST process and
        # no recovery cycle would be proven at all (evidence-deficiency fix,
        # review round: the sleepy row's state at the kill was never asserted).
        # Only the dead process and the fresh process below can execute the
        # task; the dead one cannot, so the completion observed below can
        # only be the fresh process's own recovery.
        row_sleepy = next(r for r in _row_rows() if r["task_id"] == sleepy_tid)
        assert row_sleepy["status"] in ("claimed", "running"), \
            (f"positive-control premise broken at the kill: the sleepy row "
             f"is {row_sleepy['status']!r}, not mid-execution — its later "
             f"completion would not prove a recovery cycle")
        assert row_sleepy.get("claim_token"), \
            "positive-control premise broken at the kill: no claim fence"
        # and the cancelled row must still be cancelled at the same moment
        row_cancelled = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row_cancelled["status"] == "cancelled", \
            "the cancelled row did not stay cancelled across the kill itself"
        stage_a_before = len([r for r in _MultiStageServer.requests["stage-a"]
                             if r["key"] == f"{tid}:a"])
        srv2 = _Server(**kwargs)
        try:
            srv2.start()  # liveness witness (health-checked)
            # the observation window: the positive control's FULL recovery
            # cycle (lease expiry -> reaper -> re-dispatch -> re-run ->
            # completed). The reaper is proven alive here: it recovered the
            # sleepy task; anything recoverable gets recovered in this window
            assert _wait_for(lambda: any(
                r["task_id"] == sleepy_tid and r["status"] == "completed"
                for r in _row_rows()), timeout=90), \
                "positive control: the fresh process never recovered the " \
                "mid-execution task — the recovery machinery is not proven " \
                "alive, so the non-resurrection below proves nothing"
            # the cancelled row: no resurrection in that very window
            row = next(r for r in _row_rows() if r["task_id"] == tid)
            assert row["status"] == "cancelled", \
                f"RESURRECTION: the cancelled row became {row['status']!r}"
            stage_a_after = len([r for r in _MultiStageServer.requests["stage-a"]
                                if r["key"] == f"{tid}:a"])
            assert stage_a_after == stage_a_before, \
                "RESURRECTION: stage A was redelivered for the cancelled task"
            assert not any(r["key"] == f"{tid}:b"
                           for r in _MultiStageServer.requests["stage-b"]), \
                "RESURRECTION: stage 2 started for the cancelled task"
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200 and mem["status"] == "failed" \
                and mem["result"] is not None \
                and mem["result"].get("result", {}).get("error") == "cancelled", \
                f"the cancelled task's public representation after recovery lost " \
                f"the cancellation error (result={mem.get('result')!r})"
        finally:
            srv2.stop()
    finally:
        srv.stop()


def test_r188_full_acceptance_multistage_running_task_recovers(
        fake_backend, tmp_path):
    """R-188 FULL acceptance scenario (criteria a + b, recovery time measured):
    a MULTI-STAGE task whose execution BEGAN — stage A completed BY THE
    COORDINATOR (its result is back in the killed process), the coordinator
    provably waiting at the gate BEFORE stage 2 — is SIGKILLed; the next
    process must recover the task to `completed` (criterion a: no loss),
    execute stage 2 itself, with every stage's effect applied EXACTLY ONCE
    (criterion b: no duplication across the re-run), and the recovery time
    (fresh-process start -> completion observed) is measured and bounded.
    The result must be readable through the fresh process's API.

    Re-running the task FROM THE BEGINNING is the accepted recovery
    semantics for criterion (a) (documented): no mid-stage resume is claimed;
    the re-run's stage A is redelivered, and its effect is not re-applied
    because each stage carries its own stable idempotency key.

    RED capture (test-only knob, the documented opt-out): with
    R188_FORCE_VOLATILE=1 the subprocess runs AGOS_TASK_QUEUE=0 (the explicit
    volatile opt-out) and the SAME test fails on the no-loss assertion — the
    multi-stage running task is LOST (the row stays non-terminal forever:
    the volatile mode has no recovery path). The committed default is green.
    """
    # the test-only wrapper module: the two-stage agent inside the subprocess
    wrapper_dir = str(tmp_path)
    with open(os.path.join(wrapper_dir, "r188_ms_wrapper.py"), "w") as f:
        f.write(_MULTISTAGE_WRAPPER_PY)
    # the two-stage work service (this process — survives the SIGKILL)
    _MultiStageServer._reset()
    work_httpd = _MultiStageServer.serve()
    work_url = f"http://127.0.0.1:{work_httpd.server_address[1]}"
    env_extra = {
        "R188_EFFECT_URL": work_url,
        # a longer lease gives the kill a comfortable window AFTER stage A
        # completes (the row must not expire its own lease while process A is
        # still alive — its own reaper would recover it)
        "AGOS_TASK_LEASE_S": "8",
    }
    if os.environ.get("R188_FORCE_VOLATILE") == "1":
        env_extra["AGOS_TASK_QUEUE"] = "0"  # the documented explicit opt-out
    kwargs = dict(env_extra=env_extra, module="r188_ms_wrapper:app",
                 pythonpath=wrapper_dir)
    srv = _Server(**kwargs)
    try:
        srv.start()
        # arm the gate BEFORE the task exists: the coordinator will block
        # between the stages
        _MultiStageServer.gate_armed.set()
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "multistage recovery"})
        assert status == 202, body
        tid = body["task_id"]
        # the premise: stage A COMPLETED by the coordinator (its request is
        # answered — the effect landed and the result came back to process
        # A) and the coordinator is BLOCKED AT THE GATE (stage 2 not started)
        assert _wait_for(lambda: len(_MultiStageServer.requests["stage-a"]) >= 1
                         and len(_MultiStageServer.requests["gate"]) >= 1,
                         timeout=30), \
            "the run never reached the gate between the stages"
        assert len(_MultiStageServer.requests["stage-b"]) == 0, \
            "premise broken: stage 2 already started before the kill"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "running", \
            f"premise broken: the row is {row['status']!r}, not running"
        forced_volatile = os.environ.get("R188_FORCE_VOLATILE") == "1"
        if not forced_volatile:
            # durable mode only — the claim's storage fence: the running row
            # carries the claim token and a lease still valid at kill time (the
            # lease the fresh process's reaper must wait out). The volatile red
            # run must fail at the NO-LOSS assertion below, never here.
            from datetime import datetime, timezone
            assert row.get("claim_token"), \
                "premise broken: no claim token on the running row"
            lease_at = datetime.fromisoformat(row["lease_expires_at"])
            assert lease_at > datetime.now(timezone.utc), \
                f"premise broken: the lease already expired at kill time " \
                f"({row['lease_expires_at']})"
        # POST-MORTEM: an independent COPY of the row, captured after death
        # (see below) — asserted non-terminal then; the completion can only be
        # the fresh process's own recovery
        # kill the coordinator BETWEEN the stages: stage A's result is in the
        # dead process, stage 2 never started — the transition depends on it
        t_kill = time.monotonic()
        srv.kill()
        # release the gate: the dead client's call fails on its corpse
        _MultiStageServer.gate_release.set()
        frozen = dict(next(r for r in _row_rows() if r["task_id"] == tid))
        assert frozen["status"] not in ("completed", "failed"), \
            f"premise broken: the task already reached {frozen['status']!r} before death"
        if not forced_volatile:
            # the fence survived the death with the row (durable mode only)
            assert frozen.get("claim_token"), \
                "premise broken: the claim fence vanished from the frozen row"
        assert len(_MultiStageServer.requests["stage-b"]) == 0, \
            "premise broken: stage 2 started after the death"
        # a fresh process on the same backend + work service: its reaper waits
        # out the dead process's lease and re-dispatches the row. The recovery
        # clock starts BEFORE the restart request (boot time is part of it).
        srv2 = _Server(**kwargs)
        t0 = time.monotonic()
        try:
            srv2.start()
            # THE assertion of R-188 criterion (a), full scenario — RED under
            # the volatile opt-out: the multi-stage running task is lost
            assert _wait_for(lambda: any(r["task_id"] == tid and
                                         r["status"] == "completed"
                                         for r in _row_rows()), timeout=40), \
                (f"R-188 task loss: a multi-stage task provably in flight "
                 f"(stage A completed by the coordinator, blocked at the gate "
                 f"before stage 2, row frozen {frozen['status']!r}) was killed "
                 f"and the next process never brought it to `completed` — no "
                 f"recovery path exists")
            t_done = time.monotonic()
            # the recovery time, measured (fresh-process start -> completion
            # observed; a bound for THIS test with THESE settings — not an
            # operational guarantee) and the total from the kill (includes
            # the test-imposed downtime)
            recovery_s = t_done - t0
            from_kill_s = t_done - t_kill
            print(f"\nR-188 recovery time: {recovery_s:.1f}s (restart->completed), "
                  f"{from_kill_s:.1f}s (kill->completed, incl. test downtime)")
            assert recovery_s < 30, \
                f"recovery took {recovery_s:.1f}s — beyond this test's bound"
            # the fresh process executed stage 2 itself
            assert len(_MultiStageServer.requests["stage-b"]) >= 1, \
                "the recovery never executed stage 2"
            # criterion (b) on both stages: the re-run redelivers stage A
            # (proven) but re-applies NEITHER stage's effect
            assert len(_MultiStageServer.requests["stage-a"]) >= 2, \
                "the recovery never re-invoked stage A (no redelivery proven)"
            assert _MultiStageServer.applied["stage-a"] == 1, \
                (f"R-188 stage-A duplication: applied "
                 f"{_MultiStageServer.applied['stage-a']} times for one logical task")
            assert _MultiStageServer.applied["stage-b"] == 1, \
                (f"R-188 stage-B duplication: applied "
                 f"{_MultiStageServer.applied['stage-b']} times for one logical task")
            # the stable idempotency keys: the task's identity + the stage,
            # on EVERY delivery of that stage
            assert all(r["key"] == f"{tid}:a"
                       for r in _MultiStageServer.requests["stage-a"]), \
                "stage A does not carry the task's stable identity"
            assert all(r["key"] == f"{tid}:b"
                       for r in _MultiStageServer.requests["stage-b"]), \
                "stage B does not carry the task's stable identity"
            # readable through the fresh process (no loss visible to the caller)
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200 and mem["status"] == "completed" \
                and mem["result"] is not None
        finally:
            srv2.stop()
    finally:
        srv.stop()


def _row_rows():
    return unit._PostgREST.rows["tasks"]


def _wait_for(predicate, timeout=40.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def fake_backend():
    unit._PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    unit._PostgREST.gets = []
    unit._PostgREST.mode[0] = "ok"
    _BarrierPostgREST.armed.clear()
    _BarrierPostgREST.release.clear()
    _BarrierPostgREST.entered.clear()
    _BarrierPostgREST.discarded.clear()
    _BarrierPostgREST.fail_payload[0] = False
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _BarrierPostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["R188_FAKE_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield unit._PostgREST.rows
    # teardown: never leave a blocked handler or an armed barrier behind
    _BarrierPostgREST.release.set()
    _BarrierPostgREST.armed.clear()
    httpd.shutdown()


# --------------------------------------------------------------- unit: the
# activation decision itself (R-188): the durable queue is the DEFAULT when
# storage is configured; AGOS_TASK_QUEUE=0 is the explicit opt-out; without
# storage there is nothing durable to activate.
def test_r188_activation_matrix(monkeypatch):
    import server
    for env_val, storage, expected in [
        (None, True, True),    # default: durable when storage is configured
        ("1", True, True),     # explicit opt-in still works
        ("0", True, False),    # explicit opt-out (volatile, documented limits)
        (None, False, False),  # no storage: nothing durable to activate
        ("1", False, False),   # the flag alone cannot conjure storage
    ]:
        if env_val is None:
            monkeypatch.delenv("AGOS_TASK_QUEUE", raising=False)
        else:
            monkeypatch.setenv("AGOS_TASK_QUEUE", env_val)
        monkeypatch.setattr(server, "supabase_configured", lambda: storage)
        assert server._queue_enabled() is expected, \
            f"AGOS_TASK_QUEUE={env_val!r}, storage={storage} -> {expected}"


def test_r188_default_mode_task_in_flight_survives_process_death(fake_backend):
    """REQUIRED behavior (R-188 criterion A, initial scope): no task loss on
    restart. A task in flight (provably: the process dies blocked inside the
    task's mid-flight storage write, the write is discarded, and the row
    snapshot is captured non-terminal AFTER death) must be brought to
    `completed` with a result by the next process. RED in the volatile mode:
    the row stays non-terminal forever — the default mode has no recovery
    path at all.
    """
    srv = _Server()
    try:
        srv.start()
        # arm the barrier BEFORE creating the task: the insert (POST) passes,
        # the task's first mid-flight PATCH (update/claim) blocks — no race
        # with a task that might otherwise complete first
        _BarrierPostgREST.armed.set()
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "summarise 3 4 5"})
        assert status == 202, body
        tid = body["task_id"]
        # the server is now provably inside the task's mid-flight storage
        # write (the PATCH is blocked inside the fake backend)
        assert _BarrierPostgREST.entered.wait(timeout=20), \
            "the server never reached the mid-flight tasks-table write"
        # kill it mid-flight
        srv.kill()
        # release the barrier: any handler that entered it DISCARDS its
        # write (decided at entry) — a dead process's write must never land
        # (it would fake a recovery the fresh process never performed)
        _BarrierPostgREST.release.set()
        assert _BarrierPostgREST.discarded.wait(timeout=10), \
            "the blocked write was not discarded cleanly (handler incomplete)"
        # disarm: the fresh process's writes must go through untouched
        _BarrierPostgREST.armed.clear()
        _BarrierPostgREST.release.clear()
        # POST-MORTEM: an independent COPY of the row (not a live reference),
        # captured after the death and the confirmed discard — the
        # completion cannot have landed: the only write in flight was
        # discarded and the process is dead
        frozen = dict(next(r for r in _row_rows() if r["task_id"] == tid))
        assert frozen["status"] not in ("completed", "failed"), \
            f"premise broken: the task already reached {frozen['status']!r} before death"
        # a fresh process on the same backend
        srv2 = _Server()
        try:
            srv2.start()
            # THE assertion of R-188 criterion A — RED in the volatile mode:
            # whatever terminal state the row reaches now can only be the
            # fresh process's own recovery
            assert _wait_for(lambda: any(r["task_id"] == tid and
                                         r["status"] == "completed"
                                         for r in _row_rows()), timeout=20), \
                (f"R-188 task loss: the task was provably in flight (row frozen "
                 f"non-terminal, status={frozen['status']!r}) when the process died "
                 f"and the next process never brought it to `completed` — the "
                 f"default (volatile) mode has no recovery; the row is a silent zombie")
            done = next(r for r in _row_rows() if r["task_id"] == tid)
            assert done["result"] is not None
            # readable through the new process (no loss visible to the caller)
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200 and mem["status"] == "completed"
        finally:
            srv2.stop()
    finally:
        srv.stop()


# --------------------------------------------------------------- criterion B:
# side-effect duplication on recovery (the test the module docstring
# deliberately deferred). The at-least-once redelivery MUST NOT re-apply the
# external side effect: the delivery is at-least-once, the APPLICATION of the
# effect must be exactly-once — enforced by a STABLE idempotency key (the
# task's identity, not the claim token or the attempt number) carried on the
# external-agent call.
def test_r188_side_effect_applied_exactly_once_on_recovery(fake_backend):
    """REQUIRED behavior (R-188 criterion B): no side-effect duplication on
    recovery. A durable task whose execution BEGAN (row `running`, lease held)
    and whose external side effect LANDED, then the coordinator is SIGKILLed
    before the run records its success; the next process recovers the row via
    the real reaper + dispatcher and re-runs it (at-least-once redelivery).
    The effect target (this test process — it survives the kill) must see the
    effect applied EXACTLY ONCE, while seeing the second attempt (retry and
    effect-application are proven separately).

    RED today: the re-run re-applies the effect (applied == 2) — the external
    call carries no stable idempotency key, so the effect target cannot know
    the second delivery is a re-delivery of the same logical operation.
    """
    # the side-effect target: an external-agent service in the test process
    _EffectServer._reset()
    effect_httpd = _EffectServer.serve()
    effect_url = f"http://127.0.0.1:{effect_httpd.server_address[1]}/run"
    env_extra = {
        "EXTERNAL_AGENTS": json.dumps(
            [{"name": "r188-effect", "url": effect_url}]),
        # a longer lease gives the kill a comfortable window AFTER the effect
        # lands (the row must not expire its own lease while process A is
        # still alive — its own reaper would recover it)
        "AGOS_TASK_LEASE_S": "8",
    }
    srv = _Server(env_extra=env_extra)
    try:
        srv.start()
        # arm the response barrier BEFORE the task exists: the FIRST external
        # call blocks AFTER applying the effect — the run is mid-flight with
        # the effect landed and the completion NOT stored (the critical window)
        _EffectServer.block.set()
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "external",
                            "prompt": "side effect exactly once"})
        assert status == 202, body
        tid = body["task_id"]
        # wait until the execution provably BEGAN and the effect LANDED:
        # the external call only happens after claim -> running, so this
        # also proves the row is `running` with a lease held by process A
        assert _wait_for(lambda: _EffectServer.applied >= 1, timeout=20), \
            "the run never reached the external side effect"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "running", \
            f"premise broken: the row is {row['status']!r}, not running"
        # kill the coordinator mid-run: the effect has landed, the completion
        # has NOT been stored — the at-least-once window
        srv.kill()
        # release the blocked response: its write fails on the dead client
        _EffectServer.release.set()
        # a fresh process on the same backend + effect target: its reaper
        # waits out the dead process's lease and re-dispatches the row
        srv2 = _Server(env_extra=env_extra)
        try:
            srv2.start()
            assert _wait_for(lambda: any(r["task_id"] == tid and
                                         r["status"] == "completed"
                                         for r in _row_rows()), timeout=40), \
                ("R-188 recovery never completed the re-run task — the "
                 "side-effect scenario is also a task-loss scenario")
            # the recovery re-attempt provably reached the effect target
            assert len(_EffectServer.requests) >= 2, \
                "the recovery never re-invoked the external agent (no second attempt)"
            # both deliveries are the SAME logical task (same prompt)
            assert all(r["prompt"] == "side effect exactly once"
                       for r in _EffectServer.requests)
            # THE assertion of R-188 criterion B — RED today (applied == 2):
            # the effect target must apply the effect EXACTLY ONCE across the
            # re-deliveries of the same logical task
            assert _EffectServer.applied == 1, \
                (f"R-188 side-effect duplication: the effect was applied "
                 f"{_EffectServer.applied} times for one logical task — the "
                 f"at-least-once redelivery re-applied the external side effect")
            # the stable idempotency key: the task's identity on EVERY
            # delivery — not the claim token and not the attempt number (both
            # differ across the two processes)
            assert all(r["key"] == tid for r in _EffectServer.requests), \
                "the external call does not carry the task's stable identity " \
                "as its idempotency key (key={r['key']!r})"
        finally:
            srv2.stop()
    finally:
        srv.stop()
        effect_httpd.shutdown()


# ------------------------------------------------ recipient stability (the
# last open half of criterion B): an at-least-once redelivery must reach the
# SAME external recipient — the routing choice (min tasks_completed, an
# IN-PROCESS stat) is not durable, so a fresh process can naturally pick a
# DIFFERENT recipient that never saw the task's idempotency key and would
# re-apply the effect. The fix: the first dispatch PINS the recipient's
# public identity (name + endpoint binding — never an API key) in the row's
# payload BEFORE the first send, and a redelivery honors the pin.
class _RecipientState:
    def __init__(self):
        self.requests = []        # {"key": str|None, "prompt": str} per call
        self.applied = 0          # effects actually applied
        self.applied_keys = set()
        self.block = threading.Event()    # armed: the FIRST request blocks AFTER applying
        self.release = threading.Event()


class _RecipientServer(BaseHTTPRequestHandler):
    """One external RECIPIENT (R-188 recipient stability). It runs in the
    TEST process, so it survives the coordinator's SIGKILL: its counters are
    independent evidence, never the killed process's memory. Same contract
    as _EffectServer: each call is RECORDED (attempt evidence); the effect
    applies once per idempotency key; a keyless call cannot be deduplicated.
    The response barrier is PER RECIPIENT (independent logs and keys)."""

    state: _RecipientState = None  # per-recipient, set by the factory below

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key")
        prompt = ""
        try:
            prompt = str((body.get("messages") or [{}])[0].get("content", ""))
        except Exception:
            pass
        st = self.state
        st.requests.append({"key": key, "prompt": prompt})
        if not key or key not in st.applied_keys:
            st.applied += 1
            if key:
                st.applied_keys.add(key)
        if st.block.is_set():
            st.block.clear()  # only the FIRST request blocks
            if not st.release.wait(timeout=30):
                raise RuntimeError("R-188 recipient barrier: released nobody within 30s")
        try:
            out = json.dumps({"choices": [
                {"message": {"role": "assistant", "content": "r188 recipient run done"}}
            ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client died (SIGKILL) mid-call — expected here


def _recipient(name: str):
    """Start one independent recipient; returns (state, url)."""
    state = _RecipientState()
    handler = type(f"_Recipient_{name}", (_RecipientServer,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return state, f"http://127.0.0.1:{httpd.server_address[1]}/run"


def test_r188_recipient_stability_across_recovery(fake_backend):
    """REQUIRED behavior (R-188 criterion B, recipient stability): a redelivery
    must reach the SAME external recipient. The routing choice (min
    tasks_completed — an in-process stat) is not durable, so a fresh process
    can naturally pick a DIFFERENT recipient (B) that never saw the task's
    idempotency key and would re-apply the effect.

    RED before the fix: the redelivery went to B (the natural fresh-process
    choice) and B applied the effect again (total applied == 2).
    """
    # two INDEPENDENT recipients: A (the first dispatch's choice) and B (the
    # fresh process's natural choice — its own app log and idempotency keys)
    a_state, a_url = _recipient("A")
    b_state, b_url = _recipient("B")
    # srv1: A first in the list (the tie-break) so the target goes to A
    env1 = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-a", "url": a_url},
                                           {"name": "r188-b", "url": b_url}]),
            "AGOS_TASK_LEASE_S": "8"}
    srv = _Server(env_extra=env1)
    try:
        srv.start()
        # ---- 1. ROUTING CONTROL: prove the project's ACTUAL routing mechanism
        # (min tasks_completed, tie -> first in the list) picks B when B has
        # fewer completed — these controls run in srv1 and complete BEFORE the
        # barrier is armed, so they cannot disturb the target's kill window.
        s, b1 = _api(srv.port, "POST", "/api/tasks",
                    {"specialization": "external", "prompt": "routing control one"})
        assert s == 202, b1
        assert _wait_for(lambda: any(r["task_id"] == b1["task_id"] and
                                     r["status"] == "completed"
                                     for r in _row_rows()), timeout=40)
        assert any(r["key"] == b1["task_id"] for r in a_state.requests), \
            "routing control broken: the first task did not go to A (list tie-break)"
        s, b2 = _api(srv.port, "POST", "/api/tasks",
                    {"specialization": "external", "prompt": "routing control two"})
        assert s == 202, b2
        assert _wait_for(lambda: any(r["task_id"] == b2["task_id"] and
                                     r["status"] == "completed"
                                     for r in _row_rows()), timeout=40)
        assert any(r["key"] == b2["task_id"] for r in b_state.requests), \
            "routing control broken: min(tasks_completed) did not pick B"
        # ---- 2. THE TARGET TASK: dispatched to A via the real path (tie
        # again: both completed one). A's response is blocked AFTER applying
        # the effect — the run is mid-flight with the effect landed and the
        # completion NOT stored (the at-least-once window).
        a_state.block.set()
        s, target = _api(srv.port, "POST", "/api/tasks",
                         {"specialization": "external",
                          "prompt": "recipient stability exactly once"})
        assert s == 202, target
        tid = target["task_id"]
        assert _wait_for(lambda: a_state.applied >= 2, timeout=20), \
            "the run never reached recipient A"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "running", \
            f"premise broken: the row is {row['status']!r}, not running"
        assert row.get("claim_token"), "premise broken: no claim fence"
        assert a_state.applied == 2 and b_state.applied == 1, \
            "premise broken: the control/task applications are off"
        assert not any(r["key"] == tid for r in b_state.requests), \
            "premise broken: the target already reached B"
        # kill the coordinator mid-call: the effect landed at A, the
        # completion is NOT stored — the recovery must re-deliver
        srv.kill()
        frozen = dict(next(r for r in _row_rows() if r["task_id"] == tid))
        assert frozen["status"] not in ("completed", "failed", "cancelled"), \
            f"premise broken: the task already reached {frozen['status']!r} before death"
        assert frozen.get("claim_token"), \
            "premise broken: the claim fence vanished from the frozen row"
        assert a_state.applied == 2  # the target's effect is still applied exactly once
        a_state.release.set()  # let A's blocked handler finish (its client is dead)
        # ---- 3. THE FRESH PROCESS: B FIRST in its list (the natural tie-break
        # picks B — the routing instability is real, not contrived). The
        # redelivery must honor the pinned recipient A instead.
        env2 = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-b", "url": b_url},
                                               {"name": "r188-a", "url": a_url}]),
                "AGOS_TASK_LEASE_S": "8"}
        srv2 = _Server(env_extra=env2)
        try:
            srv2.start()
            # THE assertion of R-188 recipient stability — RED before the
            # fix: the fresh process's natural choice was B, B never saw the
            # key, and the effect was applied again (total 4, not 3)
            assert _wait_for(lambda: any(r["task_id"] == tid and
                                         r["status"] == "completed"
                                         for r in _row_rows()), timeout=40), \
                ("R-188 recipient instability: the recovered task never "
                 "completed — the pinned recipient was not honored")
            assert a_state.applied + b_state.applied == 3, \
                (f"R-188 recipient instability: the effect was applied "
                 f"{a_state.applied} times at A and {b_state.applied} times "
                 f"at B — the redelivery reached a recipient that never saw "
                 f"the task's idempotency key and re-applied the effect")
            # the target NEVER reached B (no silent rerouting)
            assert not any(r["key"] == tid for r in b_state.requests), \
                "R-188 recipient instability: the target task reached B"
            # the redelivery DID re-reach A (with the same key — A honored it)
            assert sum(1 for r in a_state.requests if r["key"] == tid) >= 2, \
                "the recovery never re-delivered to the pinned recipient A"
            # the pin is durable in the row (the public identity only)
            done = next(r for r in _row_rows() if r["task_id"] == tid)
            pin = (done.get("payload") or {}).get("_agos_recipient")
            assert pin == {"name": "r188-a", "endpoint": a_url}, \
                f"the durable pin is wrong or missing: {pin!r}"
            # readable through the new process (no loss visible to the caller)
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200 and mem["status"] == "completed"
        finally:
            srv2.stop()
    finally:
        srv.stop()


def test_r188_pinned_recipient_absent_waits_without_silent_rerouting(fake_backend):
    """REQUIRED behavior (R-188 recipient stability, absence policy): a task
    pinned to recipient A, whose recipient is NOT registered in the fresh
    process (A was removed/disabled), must NOT silently move to B — it waits
    (no attempt consumed, cancellable, observable reason). The wait is
    UNBOUNDED by design: a permanently removed recipient never reroutes."""
    a_state, a_url = _recipient("A2")
    b_state, b_url = _recipient("B2")
    env1 = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-a2", "url": a_url}]),
            "AGOS_TASK_LEASE_S": "8"}
    srv = _Server(env_extra=env1)
    try:
        srv.start()
        a_state.block.set()
        s, target = _api(srv.port, "POST", "/api/tasks",
                         {"specialization": "external",
                          "prompt": "absent recipient waits"})
        assert s == 202, target
        tid = target["task_id"]
        assert _wait_for(lambda: a_state.applied >= 1, timeout=20), \
            "the run never reached recipient A"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] == "running" and row.get("claim_token")
        srv.kill()
        a_state.release.set()
        # the fresh process knows ONLY B — the pinned A is absent there
        env3 = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-b2", "url": b_url}]),
                "AGOS_TASK_LEASE_S": "8"}
        srv2 = _Server(env_extra=env3)
        try:
            srv2.start()
            # observation window: several defer/re-dispatch cycles (the
            # lease expiry + back-off) — the task must stay waiting
            assert _wait_for(lambda: next(
                r for r in _row_rows() if r["task_id"] == tid
            )["status"] in ("queued", "claimed"), timeout=10), \
                "the pinned task did not settle into the waiting state"
            time.sleep(6)  # a bounded observation window (not an eternity claim)
            row2 = next(r for r in _row_rows() if r["task_id"] == tid)
            assert row2["status"] in ("queued", "claimed"), \
                f"the pinned-absent task left the wait: {row2['status']!r}"
            # NO silent rerouting: B never received the target
            assert not any(r["key"] == tid for r in b_state.requests), \
                "R-188 absence policy broken: the task silently moved to B"
            # no attempt consumed by the waiting (start_task never ran again)
            assert int(row2.get("attempts") or 0) == int(row.get("attempts") or 0) + 0, \
                "the wait consumed an execution attempt"
            # observable reason through the new process's API
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200
            assert mem.get("wait_reason") == "recipient_pinned_absent", \
                f"the wait reason is not observable: {mem.get('wait_reason')!r}"
            # the task stays CANCELLABLE while waiting
            status, body = _api(srv2.port, "POST", f"/api/tasks/{tid}/cancel")
            assert status == 200 and body["status"] == "failed" and body["cancelled"], body
            final = next(r for r in _row_rows() if r["task_id"] == tid)
            assert final["status"] == "cancelled"
        finally:
            srv2.stop()
    finally:
        srv.stop()


def test_r188_pin_write_failure_sends_nothing(fake_backend):
    """REQUIRED behavior (R-188 recipient stability, zero-send guarantee): if
    the pin does not durably land (the storage write fails or the claim is
    lost), the run sends NOTHING — an unpinned redelivery could reach a
    different recipient that never saw the idempotency key and re-apply the
    effect. The store failure here is targeted: only the pin write (the only
    tasks-table PATCH carrying a payload) is rejected."""
    a_state, a_url = _recipient("A3")
    env = {"EXTERNAL_AGENTS": json.dumps([{"name": "r188-a3", "url": a_url}]),
           "AGOS_TASK_LEASE_S": "8"}
    srv = _Server(env_extra=env)
    try:
        srv.start()
        _BarrierPostgREST.fail_payload[0] = True  # the pin write fails (500)
        s, target = _api(srv.port, "POST", "/api/tasks",
                         {"specialization": "external",
                          "prompt": "pin write failure sends nothing"})
        assert s == 202, target
        tid = target["task_id"]
        # observation window: several claim -> pin-fail -> defer cycles —
        # the recipient must NEVER be called (zero external sends)
        time.sleep(8)
        assert a_state.requests == [], \
            "R-188 zero-send broken: the recipient was called without a durable pin"
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] in ("queued", "claimed"), \
            f"the unpinnable task left the wait: {row['status']!r}"
        assert int(row.get("attempts") or 0) == 0, \
            "the pin-write failure consumed an execution attempt"
        # the wait reason is observable
        status, mem = _api(srv.port, "GET", f"/api/tasks/{tid}")
        assert status == 200 and mem.get("wait_reason") == "recipient_pin_unsaved", \
            f"the wait reason is not observable: {mem.get('wait_reason')!r}"
        # recovery: the store heals -> the pin lands -> the run proceeds
        _BarrierPostgREST.fail_payload[0] = False
        assert _wait_for(lambda: any(r["task_id"] == tid and
                                    r["status"] == "completed"
                                    for r in _row_rows()), timeout=40), \
            "the task never ran after the pin write recovered"
        assert a_state.applied == 1  # exactly one effect application
    finally:
        _BarrierPostgREST.fail_payload[0] = False
        srv.stop()
