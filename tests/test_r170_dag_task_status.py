"""R-170 batch 19 (G-160-3): the /civilization/execute task record must not
claim ``completed`` when the DAG run behind it did not complete.

Before this batch the route wrote ``status="completed"`` for every run that
returned, whatever ``result["status"]`` said (failed, timed_out,
budget_exhausted, cancelled, awaiting_approval) — a forged success visible in
GET /api/tasks[/{id}]. And /resume never updated the record's status at all.

Rule (D-104): record ``completed`` iff the run's aggregate status is exactly
``"completed"``; every other outcome (including a missing status) is
``failed`` — the vocabulary {running, completed, failed} is unchanged (no
contract version bump) and the exact run state stays in ``result.status`` /
GET /civilization/runs/{run_id}.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_civilization import civilization_routes as cr
from agent_civilization.core import contracts as C
from agent_civilization.pipeline import dag

FETCH = "fetch https://example.com status"


@pytest.fixture
def client(tenant_auth):
    import server
    with TestClient(server.app, raise_server_exceptions=False, headers=tenant_auth.headers) as c:
        yield c


def _report(status, run_id="a" * 32):
    return {"primary": "analyst", "reason": "t", "stages": [], "final": "", "elapsed_s": 0.0,
            "run_id": run_id, "status": status, "pending_approvals": [], "budget": {},
            "order": [], "ignored_approvals": []}


def _record(client, task_id):
    rec = client.get(f"/api/tasks/{task_id}").json()
    C.validate("execute_record", rec)
    return rec


@pytest.mark.parametrize("run_status,expected", [
    ("completed", "completed"),
    ("failed", "failed"),
    ("timed_out", "failed"),
    ("budget_exhausted", "failed"),
    ("cancelled", "failed"),
    ("awaiting_approval", "failed"),
])
def test_execute_record_status_follows_the_run(client, monkeypatch, run_status, expected):
    async def fake(plan, civ, **kw):
        return _report(run_status)

    monkeypatch.setattr(cr, "execute_plan", fake)
    r = client.post("/civilization/execute", json={"message": "analyze numbers 4 8 15"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == run_status            # response unchanged
    rec = _record(client, r.json()["task_id"])
    assert rec["status"] == expected, rec
    assert rec["result"]["status"] == run_status       # exact state preserved
    assert rec.get("finished_at") is not None


def test_execute_record_without_a_run_status_is_not_completed(client, monkeypatch):
    async def fake(plan, civ, **kw):
        return {"run_id": "b" * 32}                    # no aggregate status = no proof

    monkeypatch.setattr(cr, "execute_plan", fake)
    r = client.post("/civilization/execute", json={"message": "analyze numbers 4 8 15"})
    assert r.status_code == 200, r.text
    assert _record(client, r.json()["task_id"])["status"] == "failed"


def test_real_awaiting_approval_run_is_not_recorded_completed_then_resume_completes(client):
    body = client.post("/civilization/execute", json={"message": FETCH}).json()
    assert body["status"] == "awaiting_approval", body
    rec = _record(client, body["task_id"])
    assert rec["status"] != "completed", rec
    assert rec["result"]["status"] == "awaiting_approval"
    res = client.post(f"/civilization/runs/{body['run_id']}/resume",
                      json={"approve_stages": ["execute", "probe"]})
    assert res.status_code == 200 and res.json()["status"] == "completed", res.text
    rec = _record(client, body["task_id"])
    assert rec["status"] == "completed" and rec["result"]["status"] == "completed"


def test_resume_that_does_not_complete_leaves_the_record_failed(client, monkeypatch):
    body = client.post("/civilization/execute", json={"message": FETCH}).json()
    assert body["status"] == "awaiting_approval", body

    async def fake_resume(run_id, tenant_id, civ, approvals=None):
        return _report("failed", run_id)

    monkeypatch.setattr(dag, "resume_run", fake_resume)
    res = client.post(f"/civilization/runs/{body['run_id']}/resume", json={"approve_stages": ["execute"]})
    assert res.status_code == 200 and res.json()["status"] == "failed"
    rec = _record(client, body["task_id"])
    assert rec["status"] == "failed" and rec["result"]["status"] == "failed"

