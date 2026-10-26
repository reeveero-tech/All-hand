"""R-188 (D-102): task loss on restart in the DEFAULT (volatile) mode.

R-163 built the durable queue (AGOS_TASK_QUEUE=1) and proved survival with
it — but it is OPT-IN. The default mode keeps task state in RAM (TASKS) and
only mirrors status into storage; on a process death mid-task the storage
row is never brought to a terminal state by the next process: no TASKS
entry, no dispatcher for it, no reaper. That silent zombie IS the task loss
R-188 exists to close.

The red test below reproduces the loss with a deterministic, test-controlled
barrier: the fake PostgREST is armed to BLOCK the first tasks-table PATCH
(update/claim — inserts are POSTs and pass through). The server is then
provably inside the task's mid-flight storage write when the test kills it
(SIGKILL), the row is captured frozen non-terminal (post-mortem, before the
fresh process starts — a completion cannot have landed after death), and a
fresh process on the same backend must bring the task to `completed` with a
result. RED in the volatile mode: the row stays non-terminal forever.

Deliberately separated from side-effect duplication (advisor, R-188
scoping): this test proves NO LOSS only — it does not assert attempt counts
or single execution (recovery may re-run; whether side effects may duplicate
is a separate, later test).

Honest limits: the backend is the fake PostgREST (the storage CI job runs
the real one); the analyst prompt is numeric ("3 4 5") so the dispatcher's
analyst path is the pure-stdlib statistics branch (real_dispatcher._analyst
— no LLM, no network); the recovery time is not measured here (later R-188
item); this test does not prove that rows stuck BEFORE the activation are
recovered (they carry no lease data — documented as a limit).
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

    While `armed` is set, any PATCH to the tasks table blocks until
    `release` is set (the handler signals `entered` first). Inserts are
    POSTs and are never blocked, so task creation itself is unaffected.
    """

    armed = threading.Event()
    release = threading.Event()
    entered = threading.Event()

    def do_PATCH(self):  # noqa: N802
        table, _ = self._table_q()
        if table == "tasks" and self.armed.is_set():
            self.entered.set()
            self.release.wait(timeout=30)
        try:
            super().do_PATCH()
        except BrokenPipeError:
            pass  # the client died (SIGKILL) mid-write — expected here


class _Server:
    """A real uvicorn subprocess — kill -9 is a genuine process death.

    The DEFAULT configuration: AGOS_TASK_QUEUE is NOT set (no R-163 opt-in).
    """

    def __init__(self):
        self.port = _free_port()
        self.proc = None

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
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _BarrierPostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["R188_FAKE_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield unit._PostgREST.rows
    _BarrierPostgREST.armed.clear()
    _BarrierPostgREST.release.set()
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
    """REQUIRED behavior (R-188 criterion A): no task loss on restart.

    A task in flight (provably: the process dies blocked inside the task's
    mid-flight storage write, and the row is captured frozen non-terminal
    AFTER death) must be brought to `completed` with a result by the next
    process. RED in the volatile mode: the row stays non-terminal forever —
    the default mode has no recovery path at all.
    """
    srv = _Server()
    try:
        srv.start()
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "summarise 3 4 5"})
        assert status == 202, body
        tid = body["task_id"]
        # arm the barrier: the next tasks-table PATCH (the mid-flight
        # update/claim of THIS task) blocks inside the fake backend
        _BarrierPostgREST.armed.set()
        # the server is now provably inside the task's storage write
        assert _BarrierPostgREST.entered.wait(timeout=20), \
            "the server never reached the mid-flight tasks-table write"
        # kill it mid-flight
        srv.kill()
        # free the fake backend for the fresh process
        _BarrierPostgREST.armed.clear()
        _BarrierPostgREST.release.set()
        # POST-MORTEM: the row is frozen non-terminal after death — the
        # completion cannot have landed after the process died
        row = next(r for r in _row_rows() if r["task_id"] == tid)
        assert row["status"] not in ("completed", "failed"), \
            f"premise broken: the task already reached {row['status']!r} before death"
        # a fresh process on the same backend
        srv2 = _Server()
        try:
            srv2.start()
            # THE assertion of R-188 criterion A — RED in the volatile mode
            assert _wait_for(lambda: any(r["task_id"] == tid and
                                         r["status"] == "completed"
                                         for r in _row_rows()), timeout=20), \
                (f"R-188 task loss: the task was provably in flight (row frozen "
                 f"non-terminal, status={row['status']!r}) when the process died "
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
