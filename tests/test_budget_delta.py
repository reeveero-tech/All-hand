"""Budget follow-up: only what depends on the budget is recomputed; research, plan and independent results are kept,
nothing stays on the old budget, the change is recorded, and the reviewer still gates every recomputed result."""
import asyncio
import json

import pytest

from app import assumptions as A
from app import llm as L
from app import main
from app.orchestrator import Orchestrator, Run
from app.research.manager import ResearchManager
from tests.test_core import plan_of
from tests.test_follow_ups import A as KEY, _client, _prev
from tests.test_lifecycle import Model, stored


@pytest.fixture(autouse=True)
def _clear():
    L._HOLDS.clear()
    yield
    L._HOLDS.clear()


def task(i, title, deps=()):
    return {"id": i, "title": title, "role": "writer", "instructions": f"مهمة-{i}: {title}", "acceptance": "ok",
            "depends_on": list(deps), "decompose": False}


PLAN = plan_of(task("a", "تحليل المنافسين والأسعار"), task("b", "الخطة المالية والعائد"),
               task("c", "خطة التنفيذ", deps=["b"]), task("d", "تقدير الطلب"))


class Budgeted(Model):
    """Agents answer with the budget they were given; `stale` makes the finance agent keep the old one once."""

    def __init__(self, plan, stale=0, **kw):
        super().__init__(plan, **kw)
        self.stale = stale

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        sys_ = messages[0]["content"]
        if sys_.startswith("أنت وكيل"):
            if self.quota is not None and self.usage.calls >= self.quota:
                raise L.ModelPaused("HTTP 402", kind="quota_exhausted", status=402, resume_after=1.0)
            body = messages[1]["content"]
            tid = next(w for w in ("a", "b", "c", "d") if f"مهمة-{w}" in body)
            new = "الميزانية الجديدة 300,000" in body
            budget = "300,000" if new else "200,000"
            if tid == "b" and new and self.stale > 0:
                self.stale -= 1
                budget = "200,000"
            out = {"a": "المنافسون: ثلاثة، والسعر المتوسط 45 ريال.", "d": "الطلب المقدر 12,000 وحدة سنويًا.",
                   "b": f"الميزانية {budget} ريال؛ التسويق 40% منها.", "c": f"التنفيذ على مرحلتين ضمن الميزانية {budget} ريال."}[tid]
            self.kinds.append("agent:" + tid)
            self.usage.calls += 1
            return json.dumps({"action": "final", "output": out, "confidence": 0.9}, ensure_ascii=False)
        return await super().chat(messages, temperature, max_tokens)


async def _run(model, goal, prev=None, prior=None):
    run = Run(goal=goal, llm_public={"provider": "fake"}, max_agents=30)
    if prev:
        change = A.budget_change(goal)
        run.follow_up_of, run.delta, run._base = prev["id"], {"from": prev["id"], "change": change, "old": A.prior_budget(prev)}, prev
    if prior:
        run.delta = prior.get("delta")
    await Orchestrator(run, model, prior=prior).execute()
    return run


def first_study():
    m = Budgeted(PLAN)
    run = asyncio.run(_run(m, "ادرس فرصة إطلاق منتج بميزانية 200 ألف ريال واكتب تقريرًا ماليًا"))
    assert run.status == "done"
    prev = stored(run)
    async def emit(*a):
        pass
    prev["research_state"] = {**ResearchManager("السوق", emit=emit, llm=None).to_dict(), "done": True, "rounds": 2,
                              "searches": [{"q": "منافسون"}]}
    return prev


# ---- detection (code, no model) ---------------------------------------------------------------------------------

def test_budget_change_detection_and_amounts():
    assert A.budget_change("غيّر الميزانية إلى 300 ألف ريال، وحدّث النتائج المتأثرة فقط")["new"] == 300000
    assert A.budget_change("عدّل الميزانية من 200 ألف إلى ١٫٥ مليون ريال")["new"] == 1500000
    assert A.budget_change("change the budget to 250k SAR")["new"] == 250000
    assert A.budget_change("ما رأيك في الميزانية؟") is None  # no new value: a normal follow-up
    assert A.budget_change("أضف قسمًا عن الموردين") is None
    assert A.prior_budget({"goal": "دراسة بميزانية 200 ألف ريال"})["value"] == 200000
    assert A.prior_budget({"goal": "دراسة", "tasks": {"b": {"output": "الميزانية 80,000 ريال"}}})["source"] == "assumed_by_study"


def test_affected_follows_the_dag():
    tasks = [task("a", "تحليل المنافسين"), task("b", "الخطة المالية"), task("c", "الجدول", deps=["b"]),
             task("e", "التوصيات", deps=["c"])]
    aff = A.affected(tasks, 200000)
    assert aff["direct"] == ["b"] and aff["all"] == ["b", "c", "e"] and aff["unchanged"] == ["a"]


def test_stale_lines_flag_old_budget_used_as_current_only():
    assert A.stale_lines("الميزانية 200,000 ريال للتسويق", 200000, 300000)
    assert not A.stale_lines("الميزانية السابقة 200,000 ريال، والجديدة 300,000", 200000, 300000)
    assert not A.stale_lines("الميزانية 300,000 ريال", 200000, 300000)


# ---- the follow-up run -------------------------------------------------------------------------------------------

def test_budget_follow_up_recomputes_only_dependent_results():
    prev = first_study()
    m = Budgeted(PLAN)
    run = asyncio.run(_run(m, "غيّر الميزانية إلى 300 ألف ريال، وحدّث النتائج المتأثرة فقط", prev=prev))
    assert run.status == "done", run.verdict
    assert "plan" not in m.kinds  # no new plan
    assert sorted(k for k in m.kinds if k.startswith("agent")) == ["agent:b", "agent:c"]  # only budget-dependent tasks
    assert run.tasks["a"]["reused"] and run.tasks["d"]["reused"]
    assert run.tasks["a"]["output"] == prev["tasks"]["a"]["output"]  # independent results and their evidence are kept
    assert "300,000" in run.tasks["b"]["output"] and "300,000" in run.tasks["c"]["output"]
    assert not A.stale_lines(run.result, 200000, 300000)  # nothing left on the old budget
    assert set(run.delta["recomputed"]) == {"b", "c"} and set(run.delta["reused"]) == {"a", "d"}
    assert set(run.delta["sections_changed"]) == {"task:b", "task:c"}  # diff between the versions is recorded
    assert "ما تغيّر في هذه النسخة" in run.result and "200,000" in run.result and "300,000" in run.result
    ev = [e for e in run.events if e["type"] == "budget_delta"]
    assert ev and ev[0]["affected"] == ["b", "c"]
    assert run.research_state.get("done") and not any(e["type"] == "research_round" for e in run.events)
    assert run.research_state["searches"] == prev["research_state"]["searches"]  # no source searched or opened again
    assert run.files and run.files[0]["verified"]


def test_stale_result_is_rejected_by_the_reviewer_gate_and_redone():
    prev = first_study()
    m = Budgeted(PLAN, stale=1)  # the finance agent first keeps the old budget
    run = asyncio.run(_run(m, "غيّر الميزانية إلى 300 ألف ريال", prev=prev))
    b = run.tasks["b"]
    assert len(b["attempts"]) == 2 and b["attempts"][0]["verdict"]["stale"]  # rejected even though the reviewer passed it
    assert "300,000" in b["output"] and run.status == "done"


def test_budget_follow_up_paused_then_resumed_keeps_the_delta():
    prev = first_study()
    m = Budgeted(PLAN, quota=2)  # b + its review, then c refused
    first = asyncio.run(_run(m, "غيّر الميزانية إلى 300 ألف ريال", prev=prev))
    assert first.status == "paused_quota" and first.delta["recomputed"] == ["b", "c"]
    assert first.tasks["a"]["reused"] and first.tasks["b"]["status"] == "done"
    m2 = Budgeted(PLAN)
    second = asyncio.run(_run(m2, first.goal, prior=stored(first)))
    assert second.status == "done" and [k for k in m2.kinds if k.startswith("agent")] == ["agent:c"]
    assert "plan" not in m2.kinds and "300,000" in second.tasks["c"]["output"]


def test_api_marks_a_budget_follow_up_as_delta(monkeypatch):
    prev = _prev(tasks={"b": {"id": "b", "title": "الخطة المالية", "output": "الميزانية 200 ألف", "status": "done"}},
                 research_state={"done": True})
    c, made = _client(monkeypatch, prev)
    r = c.post("/api/runs", json={"goal": "غيّر الميزانية إلى 300 ألف ريال", "follow_up_of": prev["id"]}, headers=KEY)
    assert r.status_code in (200, 202), r.text
    run = main.RUNS[r.json()["id"]]
    assert run.delta["change"]["new"] == 300000 and run.delta["old"]["value"] == 200000 and run._base["id"] == prev["id"]
    assert run.research_reuse is None
    r = c.post("/api/runs", json={"goal": "أضف قسمًا عن الموردين", "follow_up_of": prev["id"]}, headers=KEY)
    assert main.RUNS[r.json()["id"]].delta is None  # other follow-ups keep the normal path
    main.RUNS.clear()
