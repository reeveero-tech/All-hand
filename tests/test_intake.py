"""Intake brief: a request that misses what no team can work without is asked about before any model call.

Regression for production run 6a03d324e329: «منتج جديد» with no product and no budget was planned, researched
and analysed for 66 model replies, and every task was rejected.
"""
import asyncio

from fastapi.testclient import TestClient

from app import intake, main, orchestrator
from app.orchestrator import Orchestrator, Run, drop_assembly
from tests.test_core import FakeLLM, T, plan_of

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}
VAGUE = ("ادرس فرصة إطلاق منتج جديد في السوق السعودي، وابحث عن المنافسين، وحلل الأسعار والطلب، "
         "واكتب خطة تنفيذ وتقريرًا ماليًا، وأعد ملفًا نهائيًا")
FULL = ("ادرس لي فرصة إطلاق منتج جديد في السوق السعودي (مشروب قهوة باردة جاهز معلّب)، وابحث عن المنافسين، "
        "وحلل الأسعار والطلب، واكتب خطة تنفيذ وتقريرًا ماليًا بميزانية 200 ألف ريال، وأعد ملفًا نهائيًا.")


# ---------- the brief itself (pure code) ----------

def test_vague_study_blocks_on_product_and_budget():
    b = intake.brief(VAGUE)
    assert b["kind"] == "business_study" and b["blocking"] == ["subject", "budget"]
    assert b["stated"]["market"] == "السعودي"
    assert all(m["question"] and m["why"] for m in b["missing"])


def test_named_product_and_budget_do_not_block():
    for g in [FULL,
              VAGUE + ". المنتج: قهوة باردة معلبة. الميزانية: 200 ألف ريال",
              "ادرس سوق العطور في الرياض وحلل المنافسين"]:
        assert intake.brief(g)["blocking"] == [], g


def test_ordinary_requests_are_never_held():
    for g in ["مرحبا", "ما عاصمة فرنسا؟", "اكتب دالة بايثون لفرز قائمة",
              "أملك مقهى مختصًا في جدة، تكاليفه الثابتة 35 ألف ريال شهريًا. ادرس وضعه",
              "اكتب خطة تسويق لمشروع المقهى"]:
        assert intake.brief(g)["blocking"] == [], g


def test_feasibility_without_budget_asks_only_for_budget():
    assert intake.brief("ادرس سوق العطور في الرياض واكتب دراسة جدوى")["blocking"] == ["budget"]


def test_english_placeholder_is_detected():
    assert "subject" in intake.brief("study the market for a new product in Saudi Arabia")["blocking"]


def test_answers_are_tagged_as_user_facts_and_assumptions_are_not():
    b = intake.brief(VAGUE)
    goal, rec = intake.apply_answers(VAGUE, b, {"subject": "قهوة باردة معلبة"}, assume=True)
    assert "المنتج أو المشروع: قهوة باردة معلبة" in goal and "(من المستخدم)" in goal
    assert rec == {"answers": {"subject": "قهوة باردة معلبة"}, "assumed": ["budget"], "unanswered": []}
    assert "ولا تنسبها إلى المستخدم" in goal
    _, rec = intake.apply_answers(VAGUE, b, {"subject": "قهوة"}, assume=False)
    assert rec["unanswered"] == ["budget"]


# ---------- the run waits, spends nothing, then works on the answered request ----------

PLAN = plan_of(T("t1", role="analyst"), T("t2", deps=["t1"]))


async def _start(goal, decide, **kw):
    run, llm = Run(goal=goal, llm_public={}, **kw), FakeLLM(PLAN)
    task = asyncio.create_task(Orchestrator(run, llm).execute())
    for _ in range(500):
        if run.awaiting == "clarification":
            break
        await asyncio.sleep(0.01)
    assert run.awaiting == "clarification" and run.status == "running"
    assert llm.usage.calls == 0 and llm.calls == []  # not one model call before the owner answers
    ev = next(e for e in run.events if e["type"] == "clarification_needed")
    assert [q["key"] for q in ev["questions"]][:2] == ["subject", "budget"]
    decide(run)
    await task
    return run, llm


def test_answered_run_plans_with_the_answers():
    def decide(r):
        r.clarification = {"action": "answer", "answers": {"subject": "قهوة باردة معلبة", "budget": "200 ألف ريال"},
                           "assume": False}
        r._decision.set()
    run, llm = asyncio.run(_start(VAGUE, decide))
    assert run.status == "done" and "قهوة باردة معلبة" in run.goal and "200 ألف ريال" in run.goal
    assert run.brief["resolved"]["answers"]["subject"] == "قهوة باردة معلبة"
    assert llm.calls[0].startswith("أنت المنسق") or any(c.startswith("أنت المنسق الرئيسي") for c in llm.calls)


def test_assume_continues_with_labelled_assumptions():
    def decide(r):
        r.clarification = {"action": "answer", "answers": {}, "assume": True}
        r._decision.set()
    run, _ = asyncio.run(_start(VAGUE, decide))
    assert run.status == "done" and run.brief["resolved"]["assumed"] == ["subject", "budget"]
    assert "(افتراض)" in run.goal


def test_no_answer_stops_as_needs_input_with_zero_calls(monkeypatch):
    monkeypatch.setattr(orchestrator, "APPROVAL_TIMEOUT", 0.05)
    run, llm = Run(goal=VAGUE, llm_public={}), FakeLLM(PLAN)
    asyncio.run(Orchestrator(run, llm).execute())
    assert run.status == "needs_input" and llm.usage.calls == 0 and run.tasks == {}
    assert "المنتج أو المشروع" in run.result and run.events[-1]["type"] == "run_finished"


def test_complete_request_is_not_held_and_clarify_false_skips():
    run = Run(goal=FULL, llm_public={})
    asyncio.run(Orchestrator(run, FakeLLM(PLAN)).execute())
    assert run.status == "done" and run.brief["blocking"] == [] and not run.clarification
    run = Run(goal=VAGUE, llm_public={}, clarify=False)
    asyncio.run(Orchestrator(run, FakeLLM(PLAN)).execute())
    assert run.status == "done" and run.brief["blocking"] == ["subject", "budget"]


def test_follow_ups_are_not_re_asked():
    run = Run(goal="غيّر المنتج إلى منتج جديد آخر", llm_public={}, context="سياق المحادثة السابقة")
    asyncio.run(Orchestrator(run, FakeLLM(PLAN)).execute())
    assert run.brief is None and run.status in ("done", "partial")


def test_endpoint_owner_only_and_validation():
    main.RUNS.clear()
    run = Run(goal=VAGUE, llm_public={}, owner=main._owner(A["X-Client-Key"]))
    run.brief = intake.brief(VAGUE)
    main.RUNS[run.id] = run
    c = TestClient(main.app)
    url = f"/api/runs/{run.id}/clarify"
    assert c.post(url, json={"answers": {"subject": "قهوة"}}, headers=A).status_code == 409  # not waiting
    run.awaiting, run._decision = "clarification", asyncio.Event()
    assert c.post(url, json={"assume": True}, headers=B).status_code == 404  # another owner sees nothing
    r = c.post(url, json={"answers": {"subject": "قهوة"}}, headers=A)
    assert r.status_code == 400 and "الميزانية" in r.json()["detail"]  # budget still unanswered
    assert c.post(url, json={"answers": {"subject": "x" * 301, "budget": "1"}}, headers=A).status_code == 400
    r = c.post(url, json={"answers": {"subject": "قهوة باردة", "budget": "200 ألف ريال", "bogus": "x"}}, headers=A)
    assert r.status_code == 200 and run._decision.is_set()
    assert run.clarification["answers"] == {"subject": "قهوة باردة", "budget": "200 ألف ريال"}
    main.RUNS.clear()


# ---------- plan: an "assemble the final file" task is the report builder's job ----------

def _t(id_, title, deps=()):
    return {"id": id_, "title": title, "role": "writer", "depends_on": list(deps)}


def test_drop_assembly_rewires_dependents():
    tasks = [_t("t1", "بحث المنافسين"), _t("t2", "تحليل الأسعار"), _t("t3", "تقرير مالي", ["t1", "t2"]),
             _t("t4", "خطة التنفيذ", ["t3"]), _t("t5", "تجميع الملف النهائي", ["t1", "t2", "t3", "t4"]),
             _t("t6", "مراجعة لغوية", ["t5"])]
    keep, dropped = drop_assembly(tasks)
    assert dropped == ["t5"] and [t["id"] for t in keep] == ["t1", "t2", "t3", "t4", "t6"]
    assert keep[-1]["depends_on"] == ["t1", "t2", "t3", "t4"]
    assert next(t for t in keep if t["id"] == "t3")["title"] == "تقرير مالي"  # a financial report is real work


def test_drop_assembly_keeps_a_plan_that_is_only_assembly():
    tasks = [_t("t1", "جمع البيانات"), _t("t2", "تجميع التقرير النهائي", ["t1"])]
    assert drop_assembly(tasks) == (tasks, [])


def test_planner_assembly_task_never_runs():
    p = plan_of(T("t1", role="analyst"), T("t2", role="writer"),
                {**T("t3", deps=["t1", "t2"]), "title": "تجميع الملف النهائي"})
    run = Run(goal=FULL, llm_public={})
    asyncio.run(Orchestrator(run, FakeLLM(p)).execute())
    assert "t3" not in run.tasks and any(e["type"] == "plan_adjusted" for e in run.events)


def test_cancel_while_waiting_for_answers():
    async def go():
        run, llm = Run(goal=VAGUE, llm_public={}), FakeLLM(PLAN)
        task = asyncio.create_task(Orchestrator(run, llm).execute())
        while run.awaiting != "clarification":
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return run, llm
    run, llm = asyncio.run(go())
    assert run.status == "cancelled" and llm.usage.calls == 0 and run.awaiting is None
