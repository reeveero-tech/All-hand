"""Resume: continuing a stopped run reuses its plan and the outputs that already passed verification,
re-runs only what was unfinished, and only the run's owner can resume it."""
import asyncio
import json
import time

from fastapi.testclient import TestClient

from app import main, store
from app.llm import Usage, extract_json
from app.orchestrator import Orchestrator, Run


class CountingLLM:
    """Records which task instructions the agents were asked to do; never plans (resume must not re-plan)."""
    def __init__(self):
        self.usage = Usage()
        self.agent_calls, self.planned = [], 0

    async def chat(self, messages, *a, **k):
        sys = messages[0]["content"]
        if sys.startswith("أنت المنسق الرئيسي"):
            self.planned += 1
            return json.dumps({"simple": False, "summary": "s", "tasks": []})
        if sys.startswith("أنت مدقق"):
            return json.dumps({"pass": True, "score": 9, "issues": []})
        if sys.startswith("أنت المنسق. اجمع"):
            return "جواب نهائي من " + " + ".join(sorted(set(self.agent_calls)) or ["-"])
        user = next(m["content"] for m in messages if m["role"] == "user")
        self.agent_calls.append("t2" if "مهمة-٢" in user else "t3" if "مهمة-٣" in user else "?")
        return json.dumps({"action": "final", "output": "ناتج جديد"})

    async def chat_json(self, messages, *a, **k):
        return extract_json(await self.chat(messages))


def _task(tid, status, output="", deps=()):
    return {"id": tid, "title": tid, "role": "writer", "instructions": {"t1": "مهمة-١", "t2": "مهمة-٢", "t3": "مهمة-٣"}[tid],
            "acceptance": "ok", "depends_on": list(deps), "decompose": False, "status": status, "output": output,
            "attempts": [], "depth": 0, "parent": None}


def _prior(owner):
    return {"id": "abcabcabcabc", "goal": "هدف أصلي", "owner": owner, "status": "running", "created_at": time.time() - 999,
            "heartbeat_at": time.time() - 999, "events": [{"type": "plan", "scope": "root", "simple": False}],
            "tasks": {"t1": _task("t1", "done", "ناتج قديم مُتحقق"), "t2": _task("t2", "running", "نصف ناتج", ["t1"]),
                      "t3": _task("t3", "failed", "", ["t1"])}}


A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}


def test_resume_reuses_verified_and_reruns_the_rest():
    llm = CountingLLM()
    run = Run(goal="هدف أصلي", llm_public={})
    asyncio.run(Orchestrator(run, llm, prior=_prior("x")).execute())
    assert llm.planned == 0  # the stored plan is reused
    assert sorted(llm.agent_calls) == ["t2", "t3"]  # t1 was verified before: not redone
    assert run.tasks["t1"]["output"] == "ناتج قديم مُتحقق" and run.tasks["t1"]["reused"]
    assert run.tasks["t2"]["output"] == "ناتج جديد"  # unverified partial output is never trusted
    assert run.status == "done" and run.resumed == {"from": "abcabcabcabc", "reused": ["t1"]}
    ev = next(e for e in run.events if e["type"] == "resumed")
    assert ev["remaining"] == ["t2", "t3"]


def test_resume_with_unknown_shape_plans_from_scratch():
    llm = CountingLLM()
    prior = _prior("x")
    prior["tasks"]["t1"]["role"] = "no-such-role"
    run = Run(goal="هدف", llm_public={})
    asyncio.run(Orchestrator(run, llm, prior=prior).execute())
    assert llm.planned == 2 and run.resumed is None  # planned from scratch; an empty task list is retried once


def _client(monkeypatch, prior):
    async def load(run_id):
        return dict(prior) if run_id == prior["id"] else None
    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(main, "resolve_config", lambda *a: type("C", (), {"provider": "free", "public": lambda s: {}})())
    started = []

    class Orch:
        def __init__(self, run, llm, persist=None, prior=None):
            started.append((run.goal, prior and prior["id"]))
        async def execute(self):
            pass
    monkeypatch.setattr(main, "Orchestrator", Orch)
    monkeypatch.setattr(main, "LLM", lambda cfg: type("L", (), {"close": lambda s: asyncio.sleep(0)})())
    return TestClient(main.app), started


def test_only_owner_can_resume(monkeypatch):
    main.RUNS.clear()
    c, started = _client(monkeypatch, _prior(main._owner(A["X-Client-Key"])))
    body = {"goal": "تجاهل", "resume_from": "abcabcabcabc"}
    assert c.post("/api/runs", json=body, headers=B).status_code == 404  # foreign: same as missing
    assert c.post("/api/runs", json={**body, "resume_from": "../etc"}, headers=A).status_code == 404
    r = c.post("/api/runs", json=body, headers=A)
    assert r.status_code == 202
    assert started == [("هدف أصلي", "abcabcabcabc")]  # the stored goal is used, not the new text


def test_finished_run_cannot_be_resumed(monkeypatch):
    main.RUNS.clear()
    prior = {**_prior(main._owner(A["X-Client-Key"])), "status": "done"}
    c, started = _client(monkeypatch, prior)
    assert c.post("/api/runs", json={"goal": "x", "resume_from": "abcabcabcabc"}, headers=A).status_code == 409
    assert started == []


def test_cancel_stops_parallel_agents():
    """Cancelling a run must stop every sibling agent, not leave them calling a closed client."""
    calls_after_cancel = []

    class Slow(CountingLLM):
        cancelled = False

        async def chat(self, messages, *a, **k):
            if messages[0]["content"].startswith("أنت المنسق الرئيسي"):
                return json.dumps({"simple": False, "summary": "s", "tasks": [
                    {"id": f"t{i}", "title": "x", "role": "writer", "instructions": "x", "acceptance": "ok",
                     "depends_on": []} for i in (1, 2, 3)]})
            if self.cancelled:
                calls_after_cancel.append(1)
            await asyncio.sleep(0.3)
            return await super().chat(messages)

    async def go():
        llm = Slow()
        run = Run(goal="g", llm_public={})
        task = asyncio.create_task(Orchestrator(run, llm).execute())
        await asyncio.sleep(0.1)
        task.cancel()
        llm.cancelled = True
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(1)  # orphans, if any, would call the model here
        return run
    run = asyncio.run(go())
    assert run.status == "cancelled" and calls_after_cancel == []
