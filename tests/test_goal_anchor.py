"""Every agent and every per-task verifier sees the user's original request, so tasks can't drift off-topic
(found on production: a plan writer with no dependencies wrote about a 'shopping app' for a canned-coffee study)."""
import asyncio
import json

from app import orchestrator
from app.llm import extract_json
from tests.test_core import FakeLLM, T, plan_of, run_with

GOAL = "ادرس إطلاق قهوة مثلجة معلبة في السعودية بميزانية 200 ألف"


def test_agents_and_verifiers_see_the_goal():
    seen = {"agent": [], "verifier": []}

    class Spy(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            sys = messages[0]["content"]
            if sys.startswith("أنت مدقق"):
                seen["verifier"].append(messages[-1]["content"])
            elif not sys.startswith("أنت المنسق"):
                seen["agent"].append(messages[1]["content"])
            return await super().chat(messages, temperature, max_tokens)

    asyncio.run(run_with(Spy(plan_of(T("a", role="researcher"), T("b"))), goal=GOAL))
    assert len(seen["agent"]) == 2 and all(GOAL in m for m in seen["agent"])
    per_task = [m for m in seen["verifier"] if m.startswith("طلب المستخدم الأصلي")]
    assert len(per_task) == 2 and all(GOAL in m for m in per_task)


def test_anchor_is_bounded():
    a = orchestrator.anchor("س" * (orchestrator.GOAL_CHARS + 50))
    assert len(a) == orchestrator.GOAL_CHARS + 1 and a.endswith("…")


def test_planner_is_told_about_dependencies():
    assert "يجب أن تذكرها في depends_on" in orchestrator.PLANNER
