"""G-160-4 (R-170 batch 25, D-111): ``POST /api/chat`` honours an
``idempotency_key`` like ``POST /api/tasks`` does since R-163 (D-082).

Before this batch the field was silently ignored (unknown request fields are
dropped): a client that retried a chat after a timeout ran the agent twice —
two executions, two task ids, two side effects. These tests prove, through the
real route:

* the same (tenant, key) replays the SAME response (same ``task_id``,
  ``routed_to``, ``routing``, ``agent``, ``outcome``) plus ``duplicate: true``,
  with ONE execution — sequentially, and while the first is still running
  (in-flight join);
* without a key nothing changes (v1: two executions, no ``duplicate``);
* the key is per tenant; a first request that fails with an error is not
  cached (a retry executes) and its in-flight duplicate gets 409, not a hang;
* in the durable mode the key and the routing are stored in the row, so a
  replay survives the loss of the process memory (restart);
* a key already used by ``/api/tasks`` is refused by ``/api/chat`` with 409
  and vice versa (the storage unique index is ONE namespace per tenant).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.core import contracts

from tests import test_r163_durable_queue as r163

db = r163.db              # fixtures (the fake PostgREST + two tenants), as R-164 does
tenants = r163.tenants

MESSAGE = "run unit tests"     # keyword router -> test_runner (no LLM in tests)


@pytest.fixture(autouse=True)
def _clean():
    saved = dict(server.TASKS)
    for name in ("_CHAT_RESPONSES", "_CHAT_INFLIGHT"):
        getattr(server, name, {}).clear()
    server._VOLATILE_IDEMPOTENT.clear()
    yield
    for name in ("_CHAT_RESPONSES", "_CHAT_INFLIGHT"):
        getattr(server, name, {}).clear()
    server._VOLATILE_IDEMPOTENT.clear()
    server._stop_queue_loops()
    server.TASKS.clear()
    server.TASKS.update(saved)


class _Exec:
    """Counts executions; optional delay / failure."""

    def __init__(self, delay=0.0, fail=False):
        self.calls = 0
        self.delay = delay
        self.fail = fail

    async def __call__(self, agent, payload):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return {"ok": True, "agent": agent.name, "elapsed_s": 0.01,
                "result": {"text": f"run #{self.calls}"}}


def _chat(c, headers, key=None):
    body = {"message": MESSAGE}
    if key is not None:
        body["idempotency_key"] = key
    return c.post("/api/chat", json=body, headers=headers)


def test_same_key_replays_same_response_with_one_execution(tenants, monkeypatch):
    ex = _Exec()
    monkeypatch.setattr(server, "_execute_bounded", ex)
    before = set(server.TASKS)
    with TestClient(server.app) as c:
        r1 = _chat(c, tenants.headers, "chat-1")
        r2 = _chat(c, tenants.headers, "chat-1")
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    b1, b2 = r1.json(), r2.json()
    assert ex.calls == 1
    assert "duplicate" not in b1                     # a first execution keeps the v1 shape
    assert b2.pop("duplicate") is True
    assert b2 == b1
    contracts.validate("chat_response", r2.json())
    assert [t for t in server.TASKS if t not in before] == [b1["task_id"]]   # one record


def test_without_key_v1_behaviour_is_unchanged(tenants, monkeypatch):
    ex = _Exec()
    monkeypatch.setattr(server, "_execute_bounded", ex)
    with TestClient(server.app) as c:
        b1, b2 = _chat(c, tenants.headers).json(), _chat(c, tenants.headers).json()
    assert ex.calls == 2 and b1["task_id"] != b2["task_id"]
    assert "duplicate" not in b1 and "duplicate" not in b2


def test_key_is_per_tenant(tenants, monkeypatch):
    ex = _Exec()
    monkeypatch.setattr(server, "_execute_bounded", ex)
    with TestClient(server.app) as c:
        a = _chat(c, tenants.headers, "shared").json()
        b = _chat(c, tenants.headers_b, "shared").json()
    assert ex.calls == 2 and a["task_id"] != b["task_id"] and "duplicate" not in b


def test_failed_outcome_is_replayed_not_rerun(tenants, monkeypatch):
    async def failing(agent, payload):
        failing.calls += 1
        return {"ok": False, "agent": agent.name, "elapsed_s": 0.0, "result": {"error": "nope"}}
    failing.calls = 0
    monkeypatch.setattr(server, "_execute_bounded", failing)
    with TestClient(server.app) as c:
        r1, r2 = _chat(c, tenants.headers, "f-1"), _chat(c, tenants.headers, "f-1")
    assert failing.calls == 1
    assert r2.json()["duplicate"] is True and r2.json()["outcome"]["ok"] is False
    assert r2.json()["task_id"] == r1.json()["task_id"]


def test_concurrent_duplicate_joins_the_in_flight_execution(tenants, monkeypatch):
    ex = _Exec(delay=0.6)
    monkeypatch.setattr(server, "_execute_bounded", ex)
    out = {}
    with TestClient(server.app) as c:
        def first():
            out["a"] = _chat(c, tenants.headers, "inflight")
        t = threading.Thread(target=first)
        t.start()
        deadline = time.time() + 5
        while ex.calls == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert ex.calls == 1
        out["b"] = _chat(c, tenants.headers, "inflight")   # while the first runs
        t.join(10)
    a, b = out["a"].json(), out["b"].json()
    assert ex.calls == 1
    assert a["task_id"] == b["task_id"] and b["duplicate"] is True and "duplicate" not in a
    assert not getattr(server, "_CHAT_INFLIGHT", {})        # the in-flight entry is released


def test_in_flight_error_gives_duplicate_409_and_is_not_cached(tenants, monkeypatch):
    ex = _Exec(delay=0.5, fail=True)
    monkeypatch.setattr(server, "_execute_bounded", ex)
    out = {}
    with TestClient(server.app, raise_server_exceptions=False) as c:
        t = threading.Thread(target=lambda: out.setdefault("a", _chat(c, tenants.headers, "err")))
        t.start()
        deadline = time.time() + 5
        while ex.calls == 0 and time.time() < deadline:
            time.sleep(0.01)
        out["b"] = _chat(c, tenants.headers, "err")
        t.join(10)
        assert out["a"].status_code == 500
        assert out["b"].status_code == 409, out["b"].text
        assert "idempotency_key" in out["b"].json()["detail"]
        ex.fail, ex.delay = False, 0.0
        r3 = _chat(c, tenants.headers, "err")                 # a retry executes
    assert r3.status_code == 200 and "duplicate" not in r3.json() and ex.calls == 2


def test_task_and_chat_keys_share_one_namespace_volatile(tenants, monkeypatch):
    monkeypatch.delenv("AGOS_TASK_QUEUE", raising=False)
    monkeypatch.setattr(server, "_execute_bounded", _Exec())
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x",
                                       "idempotency_key": "both"}, headers=tenants.headers)
        assert r.status_code == 202
        r2 = _chat(c, tenants.headers, "both")
        assert r2.status_code == 409, r2.text
        assert _chat(c, tenants.headers, "only-chat").status_code == 200
        r3 = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x",
                                        "idempotency_key": "only-chat"}, headers=tenants.headers)
        assert r3.status_code == 409, r3.text


def test_durable_replay_survives_lost_process_memory(tenants, db, monkeypatch):
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    ex = _Exec()
    monkeypatch.setattr(server, "_execute_bounded", ex)
    with TestClient(server.app) as c:
        b1 = _chat(c, tenants.headers, "durable-1").json()
        rows = [r for r in db["tasks"] if r.get("idempotency_key") == "durable-1"]
        assert len(rows) == 1 and rows[0]["task_id"] == b1["task_id"]
        assert rows[0]["payload"]["_agos_chat"]["routing"] == b1["routing"]
        # simulated restart: the process memory is gone, the row is not
        server._CHAT_RESPONSES.clear()
        server.TASKS.clear()
        r2 = _chat(c, tenants.headers, "durable-1")
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert ex.calls == 1
    assert b2.pop("duplicate") is True and b2 == b1
    assert len([r for r in db["tasks"] if r.get("idempotency_key") == "durable-1"]) == 1


def test_durable_without_key_stores_no_key(tenants, db, monkeypatch):
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    monkeypatch.setattr(server, "_execute_bounded", _Exec())
    with TestClient(server.app) as c:
        b = _chat(c, tenants.headers).json()
    row = next(r for r in db["tasks"] if r["task_id"] == b["task_id"])
    assert row.get("idempotency_key") is None and row.get("payload") is None


def test_durable_task_key_refused_by_chat_and_back(tenants, db, monkeypatch):
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    monkeypatch.setattr(server, "_execute_bounded", _Exec())
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "R-163: summarise 1",
                                       "idempotency_key": "dur-both"}, headers=tenants.headers)
        assert r.status_code == 202
        assert _chat(c, tenants.headers, "dur-both").status_code == 409
        assert _chat(c, tenants.headers, "dur-chat").status_code == 200
        server._CHAT_RESPONSES.clear()                       # force the storage path
        r3 = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "R-163: summarise 2",
                                        "idempotency_key": "dur-chat"}, headers=tenants.headers)
        assert r3.status_code == 409, r3.text


def test_contract_schemas_carry_the_new_fields():
    req = server.ChatRequest.model_json_schema()
    assert "idempotency_key" in req["properties"]
    assert req["properties"]["idempotency_key"]["anyOf"][0]["maxLength"] == 128
    resp = contracts.CONTRACTS["chat_response"][1].model_json_schema()
    assert "duplicate" in resp["properties"] and "duplicate" not in resp.get("required", [])
