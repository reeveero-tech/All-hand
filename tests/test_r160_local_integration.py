"""R-160 LOCAL INTEGRATION: the real app under real uvicorn on 127.0.0.1 over
TCP (tests/support/r160_uvicorn_app.py). No internet, no cloud service, no
secret (a throw-away key whose SHA-256 is configured via AGOS_TENANT_KEYS, as
in a deployment). This is NOT a live/production check.

Covers the parts of the contracts that only a real server/socket shows:
SSE frames on the wire, disconnect -> slot released, reconnect -> no replay,
client abort of a slow request (observed: the server finishes and records it),
202 task + immediate disconnect, duplicate requests.
"""
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

try:  # the locked dev deps ship httpx2 (Starlette's test client uses it the same way)
    import httpx2 as httpx
except ImportError:  # pragma: no cover — an environment with the original httpx
    import httpx
import pytest

from agent_civilization.core import contracts as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY = "r160-local-test-key-not-a-secret"
H = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def live_server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = dict(os.environ, AGOS_ENV="test", AGOS_LLM_KEYLESS="0", AGOS_SSE_HEARTBEAT="1",
               R160_SLOW_S="2", AGOS_RATE_LIMIT_REQUESTS="1000",
               AGOS_TENANT_KEYS=json.dumps([{"tenant": "t1", "role": "user",
                                             "key_sha256": hashlib.sha256(KEY.encode()).hexdigest()}]))
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_KEY", "OPENROUTER_API_KEY", "AGOS_API_KEY"):
        env.pop(k, None)
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "support" / "r160_uvicorn_app.py"), str(port)],
                            env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                if httpx.get(base + "/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("uvicorn did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _gauge(base):
    text = httpx.get(base + "/metrics", headers=H, timeout=5).text
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == "agos_sse_connections":          # exact (not ..._opened_total)
            return float(value)
    return 0.0


def _frames(resp, until, limit=50):
    out = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            C.SSEFrame.model_validate({"event": ev})
            out.append(ev)
            if until(ev) or len(out) >= limit:
                break
    return out


def _wait(base, tid, tries=200):
    for _ in range(tries):
        r = httpx.get(f"{base}/api/tasks/{tid}", headers=H, timeout=5).json()
        if r.get("status") in ("completed", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError(tid)


def test_sse_on_the_wire_disconnect_and_reconnect(live_server):
    base = live_server
    with httpx.Client(timeout=10) as c:
        with c.stream("GET", base + "/api/events/stream", headers=H) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            tid = httpx.post(base + "/api/tasks", headers=H, timeout=5,
                             json={"specialization": "analyst", "prompt": "analyze 1 2"}).json()["task_id"]
            seen_open = []

            def until(e):
                if e.get("task_id") == tid and e.get("type") == "task_started":
                    seen_open.append(_gauge(base))      # measured while the stream is being read
                return e.get("type") == "task_completed" and e.get("task_id") == tid
            frames = _frames(r, until)
            assert [e["type"] for e in frames if e.get("task_id") == tid] == \
                ["task_created", "task_started", "task_completed"]
            assert seen_open == [1.0]
    # the client closed the socket: the server releases the subscription
    for _ in range(50):
        if _gauge(base) == 0:
            break
        time.sleep(0.1)
    assert _gauge(base) == 0
    # reconnect: only new frames (heartbeat every 1 s here), no replay of tid
    with httpx.Client(timeout=10) as c:
        with c.stream("GET", base + "/api/events/stream", headers=H) as r:
            first = _frames(r, lambda e: True, limit=1)
    assert first == [{"type": "heartbeat"}]


def test_client_abort_of_a_slow_chat_does_not_cancel_the_work(live_server):
    """Observed contract (v1): aborting the HTTP request does not cancel the
    server-side work — the record is completed and retrievable afterwards."""
    base = live_server
    with pytest.raises(httpx.ReadTimeout):
        httpx.post(base + "/api/chat", headers=H, timeout=0.5, json={"message": "R160-SLOW analyze 1 2 3"})
    rec = None
    for _ in range(80):
        tasks = httpx.get(base + "/api/tasks", headers=H, timeout=5).json()["tasks"]
        mine = [t for t in tasks if t.get("prompt") == "R160-SLOW analyze 1 2 3"]
        if mine and mine[0]["status"] in ("completed", "failed"):
            rec = mine[0]
            break
        time.sleep(0.1)
    assert rec is not None and rec["status"] == "completed"
    C.validate("task_record", rec)


def test_task_accepted_then_disconnect_still_completes(live_server):
    base = live_server
    r = httpx.post(base + "/api/tasks", headers=H, timeout=5,
                   json={"specialization": "analyst", "prompt": "R160-SLOW analyze 7 8"})
    assert r.status_code == 202
    tid = C.validate("task_accepted", r.json()).task_id
    rec = _wait(base, tid)                     # a new connection, later
    C.validate("task_record", rec)
    assert rec["status"] == "completed"


def test_duplicate_requests_over_tcp_are_distinct(live_server):
    base = live_server
    body = {"specialization": "analyst", "prompt": "analyze 9 9"}
    ids = [httpx.post(base + "/api/tasks", headers=H, timeout=5, json=body).json()["task_id"] for _ in range(2)]
    assert ids[0] != ids[1]
    for t in ids:
        C.validate("task_record", _wait(base, t))


def test_errors_on_the_wire_follow_the_error_contracts(live_server):
    base = live_server
    r = httpx.get(base + "/api/tasks", timeout=5)
    assert r.status_code == 401
    C.validate("error_detail", r.json())
    r = httpx.post(base + "/api/tasks", headers=H, timeout=5, json={"prompt": "x"})
    assert r.status_code == 422
    C.validate("validation_error", r.json())
    assert "trace_id" not in {k.lower() for k in r.headers}      # R-159: never returned
