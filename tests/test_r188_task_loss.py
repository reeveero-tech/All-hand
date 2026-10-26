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
        try:
            super().do_PATCH()
        except BrokenPipeError:
            pass  # the client died (SIGKILL) mid-write — expected here


class _Server:
    """A real uvicorn subprocess — kill -9 is a genuine process death.

    The DEFAULT configuration: AGOS_TASK_QUEUE is NOT set (no R-163 opt-in).
    """

    def __init__(self, env_extra: dict = None):
        """env_extra: additional env vars for the subprocess (e.g. EXTERNAL_AGENTS
        for the side-effect test, or a longer lease) — merged AFTER the base
        env so a test can override the timing knobs. None keeps the exact
        behavior the existing tests rely on."""
        self.port = _free_port()
        self.proc = None
        self.env_extra = dict(env_extra or {})

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
        import tempfile
        self._log = tempfile.NamedTemporaryFile(
            prefix="r188-server-", suffix=".log", delete=False)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--port", str(self.port),
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
