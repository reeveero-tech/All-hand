"""Follow-ups: a new message continues one of *your* finished runs; the planner gets the previous answer."""
import asyncio
import json

from fastapi.testclient import TestClient

from app import main, store
from app.llm import Usage, extract_json
from app.orchestrator import Orchestrator, Run

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}
OA = main._owner(A["X-Client-Key"])


def _prev(**kw):
    return {"id": "f00f00f00f00", "goal": "دراسة سوق القهوة الباردة بميزانية 200 ألف", "owner": OA, "status": "done",
            "result": "الخلاصة: السعر المقترح 9 ريال، والميزانية 200 ألف.", "thread_id": None, "tasks": {}, "events": [], **kw}


def _client(monkeypatch, prev):
    async def load(run_id):
        return dict(prev) if run_id == prev["id"] else None
    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(main, "resolve_config", lambda *a: type("C", (), {"provider": "free", "public": lambda s: {}})())
    made = []

    class Orch:
        def __init__(self, run, llm, persist=None, prior=None):
            made.append(run)
        async def execute(self):
            pass
    monkeypatch.setattr(main, "Orchestrator", Orch)
    monkeypatch.setattr(main, "LLM", lambda cfg: type("L", (), {"close": lambda s: asyncio.sleep(0)})())
    main.RUNS.clear()
    return TestClient(main.app), made


def test_follow_up_carries_previous_answer_and_thread(monkeypatch):
    c, made = _client(monkeypatch, _prev())
    r = c.post("/api/runs", json={"goal": "أعد الخطة بميزانية 120 ألف", "follow_up_of": "f00f00f00f00"}, headers=A)
    assert r.status_code == 202
    run = made[0]
    assert run.goal == "أعد الخطة بميزانية 120 ألف"  # the user's new message, not the old goal
    assert run.follow_up_of == "f00f00f00f00" and run.thread_id == "f00f00f00f00"
    assert "السعر المقترح 9 ريال" in run.context and "دراسة سوق القهوة" in run.context
    assert run.summary()["thread_id"] == "f00f00f00f00"


def test_follow_up_of_follow_up_keeps_thread(monkeypatch):
    c, made = _client(monkeypatch, _prev(thread_id="aaaaaaaaaaaa"))
    c.post("/api/runs", json={"goal": "و؟", "follow_up_of": "f00f00f00f00"}, headers=A)
    assert made[0].thread_id == "aaaaaaaaaaaa"


def test_follow_up_is_owner_only_and_not_while_running(monkeypatch):
    c, made = _client(monkeypatch, _prev())
    assert c.post("/api/runs", json={"goal": "x", "follow_up_of": "f00f00f00f00"}, headers=B).status_code == 404
    assert c.post("/api/runs", json={"goal": "x", "follow_up_of": "nope"}, headers=A).status_code == 404
    c2, made2 = _client(monkeypatch, _prev(status="running", heartbeat_at=9e12))
    assert c2.post("/api/runs", json={"goal": "x", "follow_up_of": "f00f00f00f00"}, headers=A).status_code == 409
    assert made == [] and made2 == []


def test_follow_up_of_scenario_stays_scenario_and_failed_run_is_flagged(monkeypatch):
    c, made = _client(monkeypatch, _prev(scenario=True, status="partial"))
    c.post("/api/runs", json={"goal": "x", "follow_up_of": "f00f00f00f00"}, headers=A)
    assert made[0].scenario is True and "اكتملت جزئيًا" in made[0].context


def test_long_previous_answer_is_truncated():
    ctx = main.follow_up_context(_prev(result="أ" * (main.CONTEXT_CHARS + 500)))
    assert "اختُصرت" in ctx and len(ctx) < main.CONTEXT_CHARS + 400


def test_planner_sees_the_context():
    seen = []

    class LLM:
        usage = Usage()

        async def chat(self, messages, *a, **k):
            if messages[0]["content"].startswith("أنت المنسق الرئيسي"):
                seen.append(messages[-1]["content"])
                return json.dumps({"simple": True, "summary": "s", "tasks": [
                    {"id": "a", "title": "a", "role": "assistant", "instructions": "x", "acceptance": "ok", "depends_on": []}]})
            return json.dumps({"action": "final", "output": "تم"})

        async def chat_json(self, messages, *a, **k):
            return extract_json(await self.chat(messages))
    run = Run(goal="أعد الخطة بميزانية 120 ألف", llm_public={}, context="سياق المحادثة السابقة: السعر 9 ريال")
    asyncio.run(Orchestrator(run, LLM()).execute())
    assert "أعد الخطة" in seen[0] and "السعر 9 ريال" in seen[0]
