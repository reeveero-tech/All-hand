"""R-163 (D-082): restart and worker-death recovery with a REAL server process.

A fake PostgREST (in-process, same class as the unit suite) backs the store;
the server itself runs as a real uvicorn subprocess so a kill -9 is a genuine
process death, not a cancelled coroutine. Two recovery paths are proven:

1. **queued across restart**: a task created via the API, the server killed
   before/while it runs, a fresh process boots and completes it (no silent
   loss).
2. **worker death mid-run**: a row leased by a dead worker (running, expired
   lease, attempt already counted) is recovered by the new process's reaper
   and re-executed — bounded by max_attempts.

Honest limits: the backend is the fake (the storage CI job runs the real
PostgREST live suite); the analyst prompt is local processing (no LLM).
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


class _Server:
    def __init__(self, db, env_extra):
        self.port = _free_port()
        self.env_extra = env_extra
        self.proc = None

    def start(self):
        env = dict(os.environ)
        env.update({
            "AGOS_ENV": "development",
            "AGOS_TASK_QUEUE": "1",
            "AGOS_TASK_LEASE_S": "2",
            "AGOS_TASK_REAP_S": "0.5",
            "AGOS_TASK_DISPATCH_IDLE_S": "0.1",
            "SUPABASE_URL": os.environ["R163_FAKE_URL"],
            "SUPABASE_SERVICE_KEY": "r163-restart-test",
            "AGOS_TENANT_KEYS": json.dumps([{
                "tenant": "restart_tenant",
                "key_sha256": hashlib.sha256(RKEY.encode()).hexdigest(),
                "role": "user"}]),
        })
        env.update(self.env_extra)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--port", str(self.port),
             "--log-level", "warning"],
            cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode()[-2000:]
                raise RuntimeError(f"server died at boot:\n{out}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("server did not become healthy in 60s")

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


RKEY = "r163-restart-" + secrets.token_urlsafe(12)
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
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), unit._PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["R163_FAKE_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield unit._PostgREST.rows
    httpd.shutdown()


def test_r163_restart_recovers_queued_and_dead_worker_tasks(fake_backend):
    # a task whose worker died mid-run (attempt already counted, lease expired)
    fake_backend["tasks"].append(unit._row("dead-worker-1", "restart_tenant", status="running",
                                  attempts=1, claim_token="gone-worker",
                                  lease_expires_at="2020-01-01T00:00:00+00:00",
                                  payload={"numbers": [4, 5, 6]}))
    srv = _Server(fake_backend, {"AGOS_TASK_DISPATCH_IDLE_S": "10"})
    try:
        srv.start()
        # 1) a new task through the real HTTP API — the dispatcher of THIS
        # process scans only every 10s, so the kill below is deterministic:
        # the task is still queued (not yet claimed) when the process dies
        status, body = _api(srv.port, "POST", "/api/tasks",
                           {"specialization": "analyst",
                            "prompt": "R-163 restart: summarise 7 8 9",
                            "idempotency_key": "restart-1"})
        assert status == 202, body
        tid = body["task_id"]
        srv.kill()
        # 2) the dead worker's task is still leased-expired; a fresh process
        # must recover BOTH: the killed task and the dead worker's row
        srv2 = _Server(fake_backend, {"AGOS_TASK_DISPATCH_IDLE_S": "0.1"})
        try:
            srv2.start()
            assert _wait_for(lambda: any(r["task_id"] == tid and r["status"] in ("completed", "failed")
                                         for r in _row_rows()), timeout=60), \
                "the queued task was silently lost across the restart"
            row = next(r for r in _row_rows() if r["task_id"] == tid)
            assert row["attempts"] == 1 and row["result"] is not None
            assert _wait_for(lambda: any(r["task_id"] == "dead-worker-1" and
                                        r["status"] in ("completed", "failed", "dead_letter")
                                        for r in _row_rows()), timeout=60), \
                "the dead worker's task was never recovered"
            dead = next(r for r in _row_rows() if r["task_id"] == "dead-worker-1")
            # recovered (retrying -> queued) and re-executed by the new process
            assert dead["attempts"] == 2 and dead["result"] is not None
            # both are readable from the NEW process (memory rebuilt from rows)
            status, mem = _api(srv2.port, "GET", f"/api/tasks/{tid}")
            assert status == 200 and mem["status"] in ("completed", "failed")
            status, dead_mem = _api(srv2.port, "GET", "/api/tasks/dead-worker-1")
            assert status == 200 and dead_mem["status"] in ("completed", "failed")
            # no duplicate rows were created by the recovery
            assert len([r for r in _row_rows() if r["task_id"] == tid]) == 1
            assert len([r for r in _row_rows() if r["task_id"] == "dead-worker-1"]) == 1
        finally:
            srv2.stop()
    finally:
        srv.stop()


def test_r163_duplicate_key_across_restart_replays_the_same_task(fake_backend):
    srv = _Server(fake_backend, {})
    try:
        srv.start()
        status, body = _api(srv.port, "POST", "/api/tasks",
                            {"specialization": "analyst",
                             "prompt": "R-163 idem across restart",
                             "idempotency_key": "idem-restart"})
        assert status == 202
        tid = body["task_id"]
        assert _wait_for(lambda: any(r["task_id"] == tid and r["status"] in ("completed", "failed")
                                     for r in _row_rows()), timeout=60)
        srv.stop()
        # a fresh process replays the SAME task for the same key: no second run
        srv2 = _Server(fake_backend, {})
        try:
            srv2.start()
            status, body2 = _api(srv2.port, "POST", "/api/tasks",
                                 {"specialization": "analyst",
                                  "prompt": "R-163 idem across restart",
                                  "idempotency_key": "idem-restart"})
            assert status == 202
            assert body2["task_id"] == tid and body2.get("duplicate") is True
            assert len([r for r in _row_rows() if r.get("idempotency_key") == "idem-restart"]) == 1
        finally:
            srv2.stop()
    finally:
        srv.stop()
