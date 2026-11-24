"""Provenance tests: memory must know *who said what*, and must never turn test data into facts about the user.

The first block replays the exact incident from production: a café invented to test memory was stored as
`project`, recalled as "the user's project", and the answer said "مشروعك" and asked for real cash figures.
"""
import asyncio
import json
import time

from fastapi.testclient import TestClient

from app import memory
from app.llm import Usage, extract_json
from app.orchestrator import Orchestrator, Run

SPACE = "mem_provenance_0123456789abcdef"
CAFE_GOAL = ("أملك مشروع مقهى مختص في حي الروضة بجدة، تكاليفه الثابتة 35 ألف ريال شهريًا وسعر الكوب 18 ريالًا "
             "وتكلفته المتغيرة 6 ريالات. احسب نقطة التعادل الشهرية بالأكواب.")
# What a real model returned for that run: it claims everything is the user's own project.
CAFE_EXTRACTION = {
    "task_summary": "حساب نقطة التعادل لمقهى في حي الروضة بجدة",
    "add": [
        {"kind": "project", "nature": "user_stated", "subject": "مقهى الروضة", "evidence": "تكاليفه الثابتة 35 ألف ريال شهريًا",
         "content": "مشروع مقهى في حي الروضة بجدة بتكاليف ثابتة 35000 ريال شهريًا وسعر الكوب 18 ريالًا", "importance": 5},
        {"kind": "finding", "nature": "user_stated", "subject": "مقهى الروضة", "evidence": "احسب نقطة التعادل",
         "content": "نقطة التعادل الشهرية للمقهى 2917 كوبًا", "importance": 4},
        {"kind": "decision", "nature": "user_stated", "subject": "مقهى الروضة", "evidence": "مقهى مختص",
         "content": "تبني عرض اشترِ 2 واحصل على الثالث مجانًا", "importance": 3},
        {"kind": "profile", "nature": "user_stated", "evidence": "أملك مشروع مقهى",
         "content": "المستخدم صاحب مقهى في جدة", "importance": 4},
    ]}
FOLLOW_UP = "اكتب خطة تسويق مختصرة لمشروعي للشهر الأول"
CAFE_FACTS = ("35", "الروضة", "مقهى", "2917")


class FakeLLM:
    def __init__(self, extraction=None):
        self.extraction, self.usage = extraction or {"task_summary": "مهمة", "add": []}, Usage()
        self.planner_inputs, self.synth_inputs = [], []

    async def chat(self, messages, *a, **k):
        sys = messages[0]["content"]
        if sys.startswith("أنت المنسق الرئيسي"):
            self.planner_inputs.append(messages[1]["content"])
            return json.dumps({"simple": False, "summary": "s", "tasks": [
                {"id": "a", "title": "a", "role": "writer", "instructions": "x", "acceptance": "ok", "depends_on": []},
                {"id": "b", "title": "b", "role": "writer", "instructions": "y", "acceptance": "ok", "depends_on": []}]})
        if sys.startswith("أنت مدقق"):
            return json.dumps({"pass": True, "score": 9, "issues": []})
        if sys.startswith("أنت مسؤول ذاكرة"):
            return json.dumps(self.extraction, ensure_ascii=False)
        if sys.startswith("أنت المنسق. اجمع"):
            self.synth_inputs.append((sys, messages[1]["content"]))
            return "جواب"
        return json.dumps({"action": "final", "output": "ناتج"})

    async def chat_json(self, messages, *a, **k):
        return extract_json(await self.chat(messages))


async def run(goal, extraction=None, scenario=False):
    llm = FakeLLM(extraction)
    r = Run(goal=goal, llm_public={}, memory_space=SPACE, scenario=scenario)
    await Orchestrator(r, llm).execute()
    return r, llm


def section(text, title_start):
    """The lines under the context section whose title starts with title_start."""
    for block in text.split("\n\n"):
        if block.startswith("### " + title_start):
            return block
    return ""


def items(space=SPACE):
    return asyncio.run(memory.STORE.list(space)) if False else memory.STORE._mem.get(memory.space_hash(space), [])


def setup_function():
    memory.STORE._mem.clear()


# ---------------- 1. test data never becomes the user's real project ----------------
async def test_cafe_marked_as_test_is_stored_as_scenario_even_if_model_claims_user_stated():
    r, _ = await run(CAFE_GOAL, CAFE_EXTRACTION, scenario=True)
    saved = r.memory["saved"]
    assert saved and all(i["nature"] == "scenario" for i in saved)
    assert not any(i["kind"] == "profile" for i in saved)  # no "the user owns a café"
    assert not any(i["kind"] == "decision" for i in saved)  # no "the user decided"


async def test_follow_up_after_test_cafe_gets_no_cafe_facts_as_user_project():
    await run(CAFE_GOAL, CAFE_EXTRACTION, scenario=True)
    r, llm = await run(FOLLOW_UP)
    planner_input = llm.planner_inputs[0]
    assert r.memory["recalled"] == [] or all(i["nature"] != "scenario" for i in r.memory["recalled"])
    for fact in CAFE_FACTS:
        assert fact not in planner_input, fact
    assert "حقائق صرّح بها المستخدم" not in planner_input


async def test_hypothetical_wording_is_detected_without_the_flag():
    goal = "لنفترض أن لدي مقهى في الروضة بتكاليف 35 ألف ريال، احسب نقطة التعادل"
    r, _ = await run(goal, CAFE_EXTRACTION)
    assert all(i["nature"] == "scenario" for i in r.memory["saved"])


async def test_scenario_is_recalled_only_when_asked_and_always_labelled():
    await run(CAFE_GOAL, CAFE_EXTRACTION, scenario=True)
    r, llm = await run("في سيناريو مقهى الروضة الاختباري، ما أثر رفع السعر إلى 20 ريالًا؟", scenario=True)
    ctx = llm.planner_inputs[0]
    assert r.memory["recalled"] and all(i["nature"] == "scenario" for i in r.memory["recalled"])
    assert "35000" in section(ctx, "بيانات اختبارية")
    assert section(ctx, "حقائق صرّح") == ""


async def test_production_legacy_rows_are_unverified_not_the_users():
    # rows exactly like the 7 in production today: no nature/subject/evidence columns
    legacy = [{"id": "l1", "kind": "project", "content": "مشروع مقهى في حي الروضة بجدة بتكاليف ثابتة 35000 ريال شهريًا",
               "run_id": "x", "importance": 5, "created_at": time.time()},
              {"id": "l2", "kind": "decision", "content": "تبني عروض ترويجية مثل اشترِ 2 واحصل على الثالث مجانًا",
               "run_id": "x", "importance": 3, "created_at": time.time()},
              {"id": "l3", "kind": "task", "content": "حساب نقطة التعادل لمقهى في حي الروضة", "run_id": "x",
               "importance": 3, "created_at": time.time()}]
    memory.STORE._mem[memory.space_hash(SPACE)] = legacy
    r, llm = await run(FOLLOW_UP)
    ctx = llm.planner_inputs[0]
    assert section(ctx, "حقائق صرّح") == ""
    # anything recalled from these rows is labelled "source not confirmed", never "the user's"
    if r.memory["recalled"]:
        assert all(i["nature"] == "unverified" for i in r.memory["recalled"])
        assert "مصدرها غير مؤكد" in ctx


# ---------------- 2. a real project stated by the user is recalled and usable ----------------
REAL_GOAL = "أنا صاحب متجر عطور إلكتروني اسمه نفحة، مبيعاتي الشهرية 40 ألف ريال وهامشي 30%. اقترح طرقًا لزيادة المبيعات"
REAL_EXTRACTION = {"task_summary": "زيادة مبيعات متجر نفحة للعطور", "add": [
    {"kind": "project", "nature": "user_stated", "subject": "متجر نفحة للعطور",
     "evidence": "متجر عطور إلكتروني اسمه نفحة، مبيعاتي الشهرية 40 ألف ريال وهامشي 30%",
     "content": "متجر نفحة للعطور الإلكتروني: مبيعات شهرية 40 ألف ريال وهامش 30%", "importance": 5},
    {"kind": "profile", "nature": "user_stated", "evidence": "أنا صاحب متجر عطور",
     "content": "المستخدم صاحب متجر عطور إلكتروني", "importance": 4}]}


async def test_real_user_project_is_saved_with_evidence_and_recalled_as_user_fact():
    r1, _ = await run(REAL_GOAL, REAL_EXTRACTION)
    proj = [i for i in r1.memory["saved"] if i["kind"] == "project"][0]
    assert proj["nature"] == "user_stated" and "نفحة" in proj["evidence"] and proj["run_id"] == r1.id
    assert proj["source_goal"].startswith("أنا صاحب متجر عطور")
    r2, llm = await run(FOLLOW_UP)
    user_facts = section(llm.planner_inputs[0], "حقائق صرّح")
    assert "40 ألف" in user_facts and "نص المستخدم" in user_facts and r1.id in user_facts


async def test_model_cannot_launder_a_computed_number_as_user_stated():
    extraction = {"task_summary": "x", "add": [
        {"kind": "finding", "nature": "user_stated", "subject": "متجر نفحة للعطور", "evidence": "مبيعاتي الشهرية 40 ألف ريال",
         "content": "صافي ربح متجر نفحة 12000 ريال شهريًا", "importance": 4}]}
    r, _ = await run(REAL_GOAL, extraction)
    f = [i for i in r.memory["saved"] if i["kind"] == "finding"][0]
    assert f["nature"] == "task_output" and f["evidence"] is None  # 12000 is not in what the user wrote


async def test_evidence_must_really_be_in_the_users_words():
    extraction = {"task_summary": "x", "add": [
        {"kind": "project", "nature": "user_stated", "evidence": "متجري في الرياض",
         "content": "متجر نفحة مقره الرياض", "importance": 4}]}
    r, _ = await run(REAL_GOAL, extraction)
    assert [i for i in r.memory["saved"] if i["kind"] == "project"][0]["nature"] == "task_output"


async def test_team_recommendation_is_not_the_users_decision():
    extraction = {"task_summary": "x", "add": [
        {"kind": "decision", "nature": "task_output", "content": "إطلاق برنامج ولاء لعملاء متجر نفحة", "importance": 3}]}
    r, _ = await run(REAL_GOAL, extraction)
    rec = [i for i in r.memory["saved"] if i["kind"] != "task"][0]
    assert rec["kind"] == "finding" and rec["nature"] == "task_output"


# ---------------- 3. assumptions stay assumptions ----------------
async def test_assumptions_stay_assumptions_after_recall():
    extraction = {"task_summary": "x", "add": [
        {"kind": "finding", "nature": "task_output", "subject": "متجر نفحة للعطور",
         "content": "يُتوقع أن يرفع برنامج الولاء المبيعات بنحو 15% لمتجر نفحة", "importance": 4},
        {"kind": "project", "nature": "user_stated", "subject": "متجر نفحة للعطور", "evidence": "أتوقع أن أبيع 500 زجاجة",
         "content": "أتوقع أن أبيع 500 زجاجة في رمضان من متجر نفحة", "importance": 4}]}
    r1, _ = await run("أتوقع أن أبيع 500 زجاجة في رمضان من متجر نفحة. ما الكمية التي أطلبها من المورد؟", extraction)
    saved = [i for i in r1.memory["saved"] if i["kind"] != "task"]
    assert {i["nature"] for i in saved} == {"assumption"}
    assert any(i["evidence"] for i in saved)  # the user's own estimate keeps its quote
    r2, llm = await run("ما خطة متجر نفحة للعطور في رمضان؟")
    ctx = llm.planner_inputs[0]
    assumptions = section(ctx, "افتراضات وتقديرات")
    assert "15%" in assumptions and "500" in assumptions
    assert "15%" not in section(ctx, "حقائق صرّح") and "500" not in section(ctx, "حقائق صرّح")


# ---------------- 4. two projects never mix ----------------
def seed_two_projects():
    memory.STORE._mem[memory.space_hash(SPACE)] = [
        {"id": "c1", "kind": "project", "nature": "user_stated", "subject": "مقهى البحر", "evidence": "مقهى البحر",
         "content": "مقهى البحر: تكاليف ثابتة 20 ألف ريال شهريًا", "run_id": "r1", "importance": 5,
         "created_at": time.time() - 100, "source_goal": None},
        {"id": "p1", "kind": "project", "nature": "user_stated", "subject": "متجر نفحة للعطور", "evidence": "نفحة",
         "content": "متجر نفحة للعطور: تكاليف ثابتة 8 آلاف ريال شهريًا", "run_id": "r2", "importance": 5,
         "created_at": time.time() - 50, "source_goal": None},
        {"id": "c2", "kind": "finding", "nature": "task_output", "subject": "مقهى البحر",
         "content": "نقطة تعادل مقهى البحر 1700 كوب والتكاليف الثابتة هي العامل الأكبر", "run_id": "r1", "importance": 4,
         "created_at": time.time() - 90, "source_goal": None}]


def test_naming_one_project_excludes_the_other():
    seed_two_projects()
    got = memory.recall("كيف أخفض التكاليف الثابتة لمتجر نفحة للعطور؟", memory.STORE._mem[memory.space_hash(SPACE)])
    ids = {i["id"] for i in got}
    assert "p1" in ids and not ids & {"c1", "c2"}


def test_unnamed_my_project_with_two_projects_does_not_guess():
    seed_two_projects()
    got = memory.recall("اكتب خطة تسويق لمشروعي", memory.STORE._mem[memory.space_hash(SPACE)])
    assert not any(i["kind"] == "project" for i in got)
    note = [i for i in got if i["id"] == "_ambiguous"][0]
    assert "مقهى البحر" in note["content"] and "متجر نفحة" in note["content"]
    assert "تنبيه" in memory.render_context(got)


async def test_extraction_cannot_supersede_across_projects_or_from_weaker_source():
    seed_two_projects()
    existing = await memory.STORE.list(SPACE)
    extraction = {"task_summary": "x", "supersede": ["c1", "p1"], "add": [
        {"kind": "project", "nature": "task_output", "subject": "متجر نفحة للعطور",
         "content": "تكاليف متجر نفحة الثابتة قد تكون 10 آلاف", "importance": 3}]}
    res = await memory.extract_and_save(FakeLLM(extraction), SPACE, "r9", "راجع تكاليف متجر نفحة", "تم", existing)
    assert res["superseded"] == []  # weaker task_output can't replace user_stated; and c1 is another project
    assert {"c1", "p1"} <= {i["id"] for i in await memory.STORE.list(SPACE)}


async def test_test_data_cannot_supersede_real_data():
    seed_two_projects()
    existing = await memory.STORE.list(SPACE)
    extraction = {"task_summary": "x", "supersede": ["c1"], "add": [
        {"kind": "project", "nature": "user_stated", "subject": "مقهى البحر", "evidence": "مقهى البحر تكاليفه 50 ألف",
         "content": "مقهى البحر تكاليفه 50 ألف ريال", "importance": 5}]}
    res = await memory.extract_and_save(FakeLLM(extraction), SPACE, "r9", "لنفترض أن مقهى البحر تكاليفه 50 ألف", "تم",
                                        existing)
    assert res["scenario"] and res["superseded"] == []


# ---------------- 5. delete and edit are reflected in recall ----------------
def test_delete_and_edit_reflect_in_recall():
    from app.main import app
    c, h = TestClient(app), {"X-Memory-Space": SPACE}
    memory.STORE._mem[memory.space_hash(SPACE)] = [
        {"id": "e1", "kind": "project", "nature": "task_output", "subject": "متجر نفحة للعطور",
         "content": "متجر نفحة: ميزانية التسويق 5 آلاف ريال", "run_id": "r1", "importance": 4, "created_at": time.time()},
        {"id": "e2", "kind": "finding", "nature": "task_output", "subject": "متجر نفحة للعطور",
         "content": "منافسو متجر نفحة يبيعون عبر سلة", "run_id": "r1", "importance": 3, "created_at": time.time()}]
    goal = "ما ميزانية تسويق متجر نفحة ومنافسوه؟"
    space_items = lambda: asyncio.run(memory.STORE.list(SPACE))  # noqa: E731

    # edit: the user corrects the budget -> new content, now user_confirmed
    r = c.patch("/api/memory/e1", headers=h, json={"content": "متجر نفحة: ميزانية التسويق 9 آلاف ريال"})
    assert r.status_code == 200 and r.json()["nature"] == "user_confirmed"
    got = memory.recall(goal, space_items())
    e1 = [i for i in got if i["id"] == "e1"][0]
    assert "9 آلاف" in e1["content"] and "5 آلاف" not in memory.render_context(got)
    assert "9 آلاف" in section(memory.render_context(got), "حقائق صرّح")

    # mark an item as test data from the panel -> it leaves normal recall
    assert c.patch("/api/memory/e2", headers=h, json={"nature": "scenario"}).json()["nature"] == "scenario"
    assert "e2" not in {i["id"] for i in memory.recall(goal, space_items())}

    # delete -> gone from recall
    assert c.delete("/api/memory/e1", headers=h).json()["deleted"] == 1
    assert "e1" not in {i["id"] for i in memory.recall(goal, space_items())}

    # validation
    assert c.patch("/api/memory/e2", headers=h, json={"nature": "fact"}).status_code == 400
    assert c.patch("/api/memory/nope", headers=h, json={"content": "محتوى جديد كافٍ"}).status_code == 404
    assert c.patch("/api/memory/e2", headers=h, json={"content": "sk-or-v1-abcdefghijklmnopqr"}).status_code == 400


# ---------------- the answer-writing step gets the attribution rules ----------------
async def test_synthesis_is_told_how_to_attribute():
    await run(CAFE_GOAL, CAFE_EXTRACTION, scenario=True)
    _, llm = await run("في سيناريو مقهى الروضة الاختباري، ما أثر رفع السعر؟", scenario=True)
    sys, user = llm.synth_inputs[0]
    assert "لا تطلب من المستخدم بيانات حقيقية" in sys and "«مشروعك»" in sys
    assert "بيانات اختبارية أو سيناريو افتراضي" in user  # labels travel with the facts into the final step


# ---------------- the honest limit, pinned down ----------------
async def test_unflagged_first_person_claim_is_kept_as_the_users_words_and_can_be_reclassified():
    """If someone writes "أملك مقهى..." without marking the run as a test, the system cannot know it is fake.
    What it guarantees: only the user's literal words become user_stated (with the quote and the run id), computed
    numbers and team suggestions do not, and one click ("بيانات اختبار") removes it from normal recall."""
    from app.main import app
    r1, _ = await run(CAFE_GOAL, CAFE_EXTRACTION)
    by_kind = {i["kind"]: i for i in r1.memory["saved"] if i["kind"] != "task"}
    assert by_kind["project"]["nature"] == "user_stated" and by_kind["project"]["run_id"] == r1.id
    assert by_kind["finding"]["nature"] == "task_output"  # 2917 was computed, not said
    assert "decision" not in by_kind  # the promo idea is the team's, not the user's decision

    c, h = TestClient(app), {"X-Memory-Space": SPACE}
    for it in r1.memory["saved"]:
        assert c.patch(f"/api/memory/{it['id']}", headers=h, json={"nature": "scenario"}).status_code == 200
    _, llm = await run(FOLLOW_UP)
    for fact in CAFE_FACTS:
        assert fact not in llm.planner_inputs[0], fact
