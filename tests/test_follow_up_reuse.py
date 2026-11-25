"""Found on production (follow-up 2853f18b33fa of 9658eefdff3f): the follow-up re-planned the whole study,
its agents could not see the previous result, and the planner gave calculations to the coder although
code execution is disabled on the server."""
import asyncio

from app import orchestrator as o
from tests.test_core import FakeLLM, T, plan_of


def test_coder_not_offered_or_used_without_exec():
    assert o.NO_EXEC in o.role_list(can_exec=False) and o.NO_EXEC not in o.role_list(can_exec=True)
    tasks = o._sanitize_plan(plan_of(T("a", role="coder")), 0, can_exec=False)
    assert tasks[0]["role"] == "analyst"
    assert o._sanitize_plan(plan_of(T("a", role="coder")), 0, can_exec=True)[0]["role"] == "coder"


def test_follow_up_planner_told_to_reuse_and_agents_see_previous_result():
    seen = {"plan": [], "agent": []}

    class Spy(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            sys = messages[0]["content"]
            if sys.startswith("أنت المنسق الرئيسي"):
                seen["plan"].append(messages[1]["content"])
            elif not sys.startswith(("أنت المنسق", "أنت مدقق", "استخرج")):
                seen["agent"].append(messages[1]["content"])
            return await super().chat(messages, temperature, max_tokens)

    run = o.Run(goal="أعد الخطة بميزانية 120 ألف", llm_public={"provider": "fake"}, context="سياق المحادثة السابقة: خطة بميزانية 200 ألف")
    asyncio.run(o.Orchestrator(run, Spy(plan_of(T("a"), T("b")))).execute())
    assert seen["plan"][0].startswith(o.FOLLOW_UP_PLAN)
    assert all("خطة بميزانية 200 ألف" in m for m in seen["agent"])


def test_fresh_run_planner_has_no_follow_up_prefix():
    seen = []

    class Spy(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت المنسق الرئيسي"):
                seen.append(messages[1]["content"])
            return await super().chat(messages, temperature, max_tokens)

    asyncio.run(o.Orchestrator(o.Run(goal="هدف", llm_public={}), Spy(plan_of(T("a"), T("b")))).execute())
    assert not seen[0].startswith(o.FOLLOW_UP_PLAN)
