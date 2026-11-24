"""Deterministic tests with a scripted fake model (no network)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import llm as llm_mod
from app.llm import LLMConfig, extract_json
from app.orchestrator import Orchestrator, Run
from app.tools import call_tool, fetch_url, run_python


class FakeLLM:
    """Routes on the system prompt; behaviour configurable per test."""

    def __init__(self, plan, verdicts=None, agent=None):
        self.plan, self.verdicts, self.agent = plan, list(verdicts or []), agent
        self.usage = llm_mod.Usage()
        self.calls = []

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        self.usage.calls += 1
        sys = messages[0]["content"]
        self.calls.append(sys[:30])
        if sys.startswith("أنت المنسق الرئيسي"):
            return json.dumps(self.plan, ensure_ascii=False)
        if sys.startswith("أنت مدقق"):
            v = self.verdicts.pop(0) if self.verdicts else {"pass": True, "score": 9, "issues": []}
            return json.dumps(v)
        if sys.startswith("أنت المنسق. اجمع"):
            return "الجواب النهائي المجمع"
        if self.agent:
            return self.agent(messages)
        return json.dumps({"action": "final", "output": "ناتج الوكيل", "confidence": 0.9})

    async def chat_json(self, messages, temperature=0.2, max_tokens=2000):
        return extract_json(await self.chat(messages, temperature, max_tokens))


def plan_of(*tasks, simple=False):
    return {"simple": simple, "summary": "s", "tasks": list(tasks)}


def T(id_, deps=(), role="writer", decompose=False):
    return {"id": id_, "title": id_, "role": role, "instructions": f"do {id_}", "acceptance": "ok",
            "depends_on": list(deps), "decompose": decompose}


async def run_with(fake, goal="هدف", max_agents=50):
    run = Run(goal=goal, llm_public={"provider": "fake"}, max_agents=max_agents)
    await Orchestrator(run, fake).execute()
    return run


def test_extract_json_variants():
    assert extract_json('نص {"a": 1} ذيل') == {"a": 1}
    assert extract_json('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no json")


async def test_parallel_dag_and_synthesis():
    run = await run_with(FakeLLM(plan_of(T("a"), T("b"), T("c", ["a", "b"]))))
    assert run.status == "done"
    assert [run.tasks[k]["status"] for k in "abc"] == ["done"] * 3
    assert run.result == "الجواب النهائي المجمع"
    assert run.agents_spawned == 3
    types = [e["type"] for e in run.events]
    assert types[0] == "run_started" and types[-1] == "run_finished" and "final_check" in types


async def test_verifier_rejection_triggers_retry_with_feedback():
    seen = []

    def agent(messages):
        seen.append(messages[1]["content"])
        return json.dumps({"action": "final", "output": "x"})

    fake = FakeLLM(plan_of(T("a"), T("b")), verdicts=[{"pass": False, "score": 3, "issues": ["ناقص"], "fix": "أكمل"}], agent=agent)
    run = await run_with(fake)
    assert run.status == "done"
    retried = [t for t in run.tasks.values() if len(t["attempts"]) == 2]
    assert len(retried) == 1 and any("ناقص" in s for s in seen)


async def test_honest_failure_and_skip_dependents():
    bad = {"pass": False, "score": 2, "issues": ["خطأ"]}
    fake = FakeLLM(plan_of(T("a"), T("b", ["a"])), verdicts=[bad, bad, bad])
    run = await run_with(fake)
    assert run.tasks["a"]["status"] == "failed" and len(run.tasks["a"]["attempts"]) == 3
    # b still runs on a's best-effort output (flagged unverified) instead of being dropped
    assert run.tasks["b"]["status"] == "done"
    assert run.status == "partial"


async def test_dependent_skipped_when_dependency_has_no_output():
    bad = {"pass": False, "score": 2, "issues": ["خطأ"]}
    fake = FakeLLM(plan_of(T("a"), T("b", ["a"])), verdicts=[bad, bad, bad],
                   agent=lambda m: json.dumps({"action": "final", "output": ""}))
    run = await run_with(fake)
    assert run.tasks["b"]["status"] == "skipped"


async def test_sub_team_delegation():
    plans = [plan_of(T("big", decompose=True), T("small")), plan_of(T("x"), T("y"))]

    class F(FakeLLM):
        async def chat(self, messages, *a, **k):
            if messages[0]["content"].startswith("أنت المنسق الرئيسي"):
                self.usage.calls += 1
                return json.dumps(plans.pop(0))
            return await super().chat(messages, *a, **k)

    run = await run_with(F(None))
    assert {"big.x", "big.y", "small"} <= set(run.tasks)
    assert run.tasks["big"]["status"] == "done" and run.agents_spawned == 3


async def test_agent_budget_is_enforced():
    run = await run_with(FakeLLM(plan_of(T("a"), T("b"), T("c"))), max_agents=2)
    assert sum(t["status"] == "failed" for t in run.tasks.values()) == 1
    assert run.agents_spawned == 2


async def test_agent_tool_loop():
    steps = iter([{"action": "tool", "tool": "run_python", "input": {"code": "print(6*7)"}},
                  {"action": "final", "output": "42"}])
    fake = FakeLLM(plan_of(T("a", role="coder"), T("b")), agent=lambda m: json.dumps(next(steps, {"action": "final", "output": "b"})))
    run = Run(goal="g", llm_public={}, can_exec=True)
    await Orchestrator(run, fake).execute()
    results = [e for e in run.events if e["type"] == "tool_result"]
    assert results and "42" in results[0]["preview"]


async def test_python_sandbox_has_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-value")
    out = await run_python("import os; print(os.environ.get('OPENROUTER_API_KEY'))")
    assert "None" in out and "secret-value" not in out
    assert "exit=0" in await run_python("print(1)")


async def test_tools_guard():
    assert "ممنوعة" in await fetch_url("http://127.0.0.1:8000/")
    assert "غير متاحة" in await call_tool("run_python", {"code": "1"}, {"web_search"})


def test_api_validation_and_platform_gate(monkeypatch):
    from app.main import app
    monkeypatch.setenv("APP_ACCESS_CODE", "owner-code")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    assert len(c.get("/api/roles").json()) >= 8
    h = {"X-Client-Key": "cli_" + "c" * 32}
    r = c.post("/api/runs", json={"goal": "x", "provider": "platform", "access_code": "wrong"}, headers=h)
    assert r.status_code == 400 and "رمز الوصول" in r.json()["detail"]
    r = c.post("/api/runs", json={"goal": "x", "provider": "byok"}, headers=h)
    assert r.status_code == 400 and "BYOK" in r.json()["detail"]
    assert c.get("/").status_code == 200 and "All-hand" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200


def test_resolve_config(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "c")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert llm_mod.resolve_config("platform", None, None, None, "c").api_key == "k"
    assert llm_mod.resolve_config("byok", "mine", "m", None, None).model == "m"
    assert llm_mod.resolve_config("free", None, None, None, None).api_key is None
