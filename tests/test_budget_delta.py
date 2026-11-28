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


# ---- production follow-up e55990c2a898 (official scenario, docs/product/ACCEPTANCE_SCENARIO.md) ---------------------
FOLLOW = "عدّل الدراسة على أساس ميزانية جديدة 300 ألف ريال بدل 200 ألف ريال، وأعد حساب ما يتأثر بالميزانية فقط."


@pytest.mark.parametrize("goal,new", [
    (FOLLOW, 300000),  # took 200 ألف (the old value, written last) as the new budget
    ("اجعل الميزانية 300 ألف ريال بدلًا من 200 ألف", 300000),
    ("الميزانية 300 ألف ريال بدلا من الميزانية السابقة 200 ألف", 300000),
    ("change the budget from 200k to 350k SAR", 350000),
    ("عدّل الميزانية من 200 ألف إلى ١٫٥ مليون ريال", 1500000),
])
def test_the_new_budget_is_the_one_marked_new_not_the_last_one(goal, new):
    assert A.budget_change(goal)["new"] == new


# The four root tasks the production planner wrote: every one repeats the budget as context.
REAL = [
    {"id": "t1", "title": "بحث المنافسين في السعودية", "instructions": "ابحث عن أبرز منافسي القهوة الباردة المعلبة في "
     "السعودية، بما يشمل العلامات والمنتجات وقنوات البيع والتموضع. المشروع قهوة باردة معلبة وميزانيته 200,000 ريال؛ "
     "السوق السعودية. استشهد بروابط حديثة، وميّز بين ما تحقق منه وما لم تجده.",
     "output": "الميزانية **200,000 ريال** (من المستخدم). لا أستطيع تقديم أسماء منافسين متحققة.", "depends_on": []},
    {"id": "t2", "title": "تحليل الأسعار والطلب", "instructions": "ابحث عن أسعار القهوة الباردة المعلبة وأسعار المنتجات "
     "البديلة ذات الصلة في السوق السعودية، ومؤشرات الطلب المنشورة علنًا. موضوع الدراسة: إطلاق قهوة باردة معلبة بميزانية "
     "200,000 ريال. أرفق مصادر وتواريخ.", "output": "ميزانية الإطلاق **200,000 ريال**. السعر غير متاح.", "depends_on": []},
    {"id": "t3", "title": "خطة إطلاق قابلة للتنفيذ", "instructions": "ضع خطة إطلاق عملية بميزانية قصوى 200,000 ريال.",
     "output": "", "depends_on": ["t1", "t2"]},
    {"id": "t4", "title": "التقرير المالي للمشروع", "instructions": "أعد تصورًا ماليًا أوليًا ضمن ميزانية 200,000 ريال.",
     "output": "", "depends_on": ["t1", "t2"]},
]


def test_budget_stated_as_context_does_not_make_research_budget_dependent():
    aff = A.affected(REAL, 200000)
    assert aff["direct"] == ["t3", "t4"] and aff["unchanged"] == ["t1", "t2"]


def test_a_task_whose_own_subject_is_the_budget_is_still_affected():
    t = {"id": "x", "title": "تحليل القنوات", "instructions": "وزّع الميزانية على القنوات. احسب العائد لكل قناة.",
         "output": "", "depends_on": []}
    assert A.affected([t], 200000)["direct"] == ["x"]


def test_kept_results_say_the_old_budget_is_the_previous_one():
    out = A.mark_previous(REAL[0]["output"], 200000, 300000, "ريال")
    assert "الميزانية السابقة" in out and "300,000" in out and not A.stale_lines(out, 200000, 300000)
    assert A.mark_previous("لا ميزانية هنا", 200000, 300000) == "لا ميزانية هنا"


class Restating(Budgeted):
    """Like production: the independent task restates the budget it was given as context."""

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        out = await super().chat(messages, temperature, max_tokens)
        if messages[0]["content"].startswith("أنت وكيل") and "مهمة-a" in messages[1]["content"]:
            d = json.loads(out)
            d["output"] += "\nالميزانية 200,000 ريال (من المستخدم)."
            out = json.dumps(d, ensure_ascii=False)
        return out


CTX = plan_of({**task("a", "تحليل المنافسين والأسعار"), "instructions": "مهمة-a: تحليل المنافسين. المشروع بميزانية 200,000 ريال."},
              task("b", "الخطة المالية والعائد"), task("c", "خطة التنفيذ", deps=["b"]), task("d", "تقدير الطلب"))


def test_follow_up_of_a_study_that_repeats_the_budget_recomputes_only_dependent_results():
    m = Restating(CTX)
    run = asyncio.run(_run(m, "ادرس فرصة إطلاق منتج بميزانية 200 ألف ريال واكتب تقريرًا ماليًا"))
    assert run.status == "done"
    prev = stored(run)
    m2 = Restating(CTX)
    run2 = asyncio.run(_run(m2, "عدّل الدراسة على أساس ميزانية جديدة 300 ألف ريال بدل 200 ألف ريال", prev=prev))
    assert run2.delta["change"]["new"] == 300000
    assert sorted(k for k in m2.kinds if k.startswith("agent")) == ["agent:b", "agent:c"]
    assert run2.tasks["a"]["reused"] and "الميزانية السابقة" in run2.tasks["a"]["output"]
    assert not A.stale_lines(run2.tasks["a"]["output"], 200000, 300000)
