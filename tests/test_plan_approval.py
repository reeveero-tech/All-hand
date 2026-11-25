"""Plan approval: no agent works before the owner approves; drops and notes are honoured; reject and
silence run nothing."""
import asyncio
import time

from fastapi.testclient import TestClient

from app import main, orchestrator
from app.orchestrator import Orchestrator, Run
from tests.test_core import FakeLLM, T, plan_of

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}
PLAN = plan_of(T("t1", role="analyst"), T("t2", deps=["t1"]), T("t3", deps=["t1"]))


class Spy(FakeLLM):
    def __init__(self):
        super().__init__(PLAN)
        self.agent_msgs = []

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        if not messages[0]["content"].startswith(("أنت المنسق", "أنت مدقق")):
            self.agent_msgs.append(messages[1]["content"])
        return await super().chat(messages, temperature, max_tokens)


async def _start(decide):
    run, llm = Run(goal="هدف", llm_public={}, approve_plan=True), Spy()
    task = asyncio.create_task(Orchestrator(run, llm).execute())
    while run.awaiting != "plan":
        await asyncio.sleep(0.01)
    assert llm.agent_msgs == [] and run.status == "running"  # nobody works before the decision
    assert any(e["type"] == "plan_awaiting_approval" for e in run.events)
    decide(run)
    await task
    return run, llm


def test_approve_with_drop_and_note():
    def decide(r):
        r.plan_decision = {"action": "approve", "dropped": ["t3"], "note": "التزم بالرياض فقط"}
        r._decision.set()
    run, llm = asyncio.run(_start(decide))
    assert run.status == "done" and set(run.tasks) == {"t1", "t2"} and len(llm.agent_msgs) == 2
    assert all("التزم بالرياض فقط" in m for m in llm.agent_msgs)


def test_reject_runs_nothing():
    def decide(r):
        r.plan_decision = {"action": "reject"}
        r._decision.set()
    run, llm = asyncio.run(_start(decide))
    assert run.status == "cancelled" and llm.agent_msgs == [] and "رفضتَ الخطة" in run.result


def test_no_answer_times_out_without_running(monkeypatch):
    monkeypatch.setattr(orchestrator, "APPROVAL_TIMEOUT", 0.05)
    run, llm = Run(goal="هدف", llm_public={}, approve_plan=True), Spy()
    asyncio.run(Orchestrator(run, llm).execute())
    assert run.status == "cancelled" and run.plan_decision["action"] == "timeout" and llm.agent_msgs == []


def test_simple_requests_are_not_held():
    run = Run(goal="مرحبا", llm_public={}, approve_plan=True)
    asyncio.run(Orchestrator(run, FakeLLM(plan_of(T("a", role="assistant"), simple=True))).execute())
    assert run.status == "done" and run.plan_decision is None


def test_endpoint_owner_only_and_validation():
    main.RUNS.clear()
    run = Run(goal="هدف", llm_public={}, approve_plan=True, owner=main._owner(A["X-Client-Key"]))
    run.tasks = {"t1": {"id": "t1", "status": "pending", "parent": None}}
    main.RUNS[run.id] = run
    c = TestClient(main.app)
    url = f"/api/runs/{run.id}/plan"
    assert c.post(url, json={"action": "approve"}, headers=A).status_code == 409  # not waiting yet
    run.awaiting, run._decision = "plan", asyncio.Event()
    assert c.post(url, json={"action": "approve"}, headers=B).status_code == 404
    assert c.post(url, json={"action": "maybe"}, headers=A).status_code == 422
    assert c.post(url, json={"action": "approve", "drop": ["t1"]}, headers=A).status_code == 400
    ok = c.post(url, json={"action": "approve", "drop": ["zz"], "note": " ركّز "}, headers=A)
    assert ok.status_code == 200 and run.plan_decision["dropped"] == [] and run.plan_decision["note"] == "ركّز"
    assert run._decision.is_set()
    assert c.post(url, json={"action": "reject"}, headers=A).status_code == 409  # already decided
