"""Memory safety: every item has nature, source, date and confidence; external sources are real;
conflicts replace old facts; spaces are isolated; memory failures are explicit and never lose the run."""
import asyncio
import json

from fastapi.testclient import TestClient

from app import main, memory
from app.orchestrator import Orchestrator, Run
from tests.test_memory_provenance import FakeLLM

SPACE_A = "mem_safety_A_0123456789abcdef"
SPACE_B = "mem_safety_B_0123456789abcdef"
SOURCE = "https://www.stats.gov.sa/ar/news/123"


class SourcedLLM(FakeLLM):
    async def chat(self, messages, *a, **k):
        if messages[0]["content"].startswith("أنت المنسق. اجمع"):
            return f"عدد سكان جدة نحو 4.7 مليون نسمة حسب الهيئة العامة للإحصاء ({SOURCE})."
        return await super().chat(messages, *a, **k)


async def run(goal, extraction, space=SPACE_A, llm_cls=FakeLLM):
    llm = llm_cls(extraction)
    r = Run(goal=goal, llm_public={}, memory_space=space)
    await Orchestrator(r, llm).execute()
    return r, llm


def setup_function():
    memory.STORE._mem.clear()


def _stored(space=SPACE_A):
    return [memory.normalise(dict(i)) for i in memory.STORE._mem.get(memory.space_hash(space), [])]


# ---------- every item: nature, source, date, confidence ----------
async def test_every_saved_item_has_nature_source_date_confidence():
    ex = {"task_summary": "سؤال عن مقهى", "add": [
        {"kind": "project", "nature": "user_stated", "subject": "مقهى", "evidence": "عندي مقهى في جدة",
         "content": "المستخدم يملك مقهى في جدة", "importance": 4}]}
    r, _ = await run("عندي مقهى في جدة، اقترح اسمًا له", ex)
    for it in _stored():
        assert it["nature"] in memory.NATURES
        assert it["run_id"] == r.id and it["source_goal"] and it["created_at"] > 0
        assert 0 <= it["confidence"] <= 1 and it["confidence"] == memory.CONFIDENCE[it["nature"]]


async def test_external_source_kept_only_if_the_url_is_really_in_the_answer():
    ex = {"task_summary": "سكان جدة", "add": [
        {"kind": "finding", "nature": "external_source", "evidence": SOURCE, "subject": "جدة",
         "content": "عدد سكان جدة نحو 4.7 مليون نسمة", "importance": 3},
        {"kind": "finding", "nature": "external_source", "evidence": "https://invented.example/report",
         "content": "متوسط دخل الأسرة في جدة 20 ألف ريال", "importance": 3}]}
    r, llm = await run("كم عدد سكان جدة؟", ex, llm_cls=SourcedLLM)
    by = {i["content"]: i for i in r.memory["saved"]}
    real = by["عدد سكان جدة نحو 4.7 مليون نسمة"]
    assert real["nature"] == "external_source" and real["evidence"] == SOURCE
    fake = by["متوسط دخل الأسرة في جدة 20 ألف ريال"]
    assert fake["nature"] == "task_output" and fake["evidence"] is None  # invented link is dropped
    ctx = memory.render_context(memory.recall("سكان جدة", _stored()))
    assert "المصدر: " + SOURCE in ctx and "حقائق صرّح بها المستخدم" not in ctx


async def test_inferred_is_never_attributed_to_the_user():
    ex = {"task_summary": "x", "add": [
        {"kind": "profile", "nature": "inferred", "content": "المستخدم على الأرجح طالب جامعي", "importance": 3},
        {"kind": "project", "nature": "inferred", "subject": "بحث التخرج", "content": "يعمل على بحث تخرج عن الطاقة",
         "importance": 3}]}
    r, _ = await run("اكتب ملخصًا عن الطاقة الشمسية لبحث", ex)
    saved = [i for i in r.memory["saved"] if i["kind"] != "task"]
    assert [i["kind"] for i in saved] == ["project"]  # a guessed profile is not stored at all
    assert saved[0]["nature"] == "inferred"
    ctx = memory.render_context(memory.recall("بحث التخرج عن الطاقة", _stored()))
    assert "حقائق صرّح" not in ctx and "استنتاجات" in ctx


# ---------- conflicts, deletion ----------
async def test_new_user_statement_replaces_the_conflicting_old_one():
    g1 = "متجري اسمه نفحة، مبيعاتي الشهرية 40 ألف ريال"
    await run(g1, {"task_summary": "x", "add": [
        {"kind": "project", "nature": "user_stated", "subject": "متجر نفحة", "evidence": "مبيعاتي الشهرية 40 ألف ريال",
         "content": "مبيعات متجر نفحة الشهرية 40 ألف ريال", "importance": 5}]})
    old = [i for i in _stored() if i["kind"] == "project"][0]
    g2 = "تحديث: مبيعاتي الشهرية في متجر نفحة صارت 55 ألف ريال"
    await run(g2, {"task_summary": "y", "add": [
        {"kind": "project", "nature": "user_stated", "subject": "متجر نفحة", "evidence": "مبيعاتي الشهرية في متجر نفحة صارت 55 ألف ريال",
         "content": "مبيعات متجر نفحة الشهرية 55 ألف ريال", "importance": 5}], "supersede": [old["id"]]})
    projects = [i["content"] for i in _stored() if i["kind"] == "project"]
    assert projects == ["مبيعات متجر نفحة الشهرية 55 ألف ريال"]
    ctx = memory.render_context(memory.recall("متجر نفحة", _stored()))
    assert "55" in ctx and "40 ألف ريال" not in ctx


def test_deleting_an_item_removes_it_from_api_and_recall():
    asyncio.run(memory.STORE.add(SPACE_A, [{"id": "d1", "kind": "finding", "nature": "user_confirmed", "subject": None,
                                            "content": "سعر القهوة في المقهى 18 ريالًا", "importance": 3,
                                            "created_at": 1.0, "run_id": "r", "evidence": None, "source_goal": "g"}]))
    c, h = TestClient(main.app), {"X-Memory-Space": SPACE_A}
    assert c.delete("/api/memory/d1", headers=h).json()["deleted"] == 1
    assert c.get("/api/memory", headers=h).json()["count"] == 0
    assert memory.recall("سعر القهوة", _stored()) == []


# ---------- isolation ----------
async def test_memory_spaces_are_isolated():
    await run("متجري اسمه نفحة للعطور", {"task_summary": "متجر عطور", "add": [
        {"kind": "project", "nature": "user_stated", "subject": "نفحة", "evidence": "متجري اسمه نفحة للعطور",
         "content": "المستخدم يملك متجر نفحة للعطور", "importance": 5}]}, space=SPACE_A)
    c = TestClient(main.app)
    a = c.get("/api/memory", headers={"X-Memory-Space": SPACE_A}).json()
    b = c.get("/api/memory", headers={"X-Memory-Space": SPACE_B}).json()
    assert a["count"] >= 1 and b["count"] == 0
    target = a["items"][0]["id"]
    assert c.delete(f"/api/memory/{target}", headers={"X-Memory-Space": SPACE_B}).json()["deleted"] == 0
    assert c.patch(f"/api/memory/{target}", json={"content": "اختراق ذاكرة غيري"},
                   headers={"X-Memory-Space": SPACE_B}).status_code == 404
    assert c.delete("/api/memory", headers={"X-Memory-Space": SPACE_B}).json()["deleted"] == 0
    assert c.get("/api/memory", headers={"X-Memory-Space": SPACE_A}).json()["count"] == a["count"]
    r, llm = await run("اكتب خطة تسويق لمشروعي", {"task_summary": "z", "add": []}, space=SPACE_B)
    assert "نفحة" not in llm.planner_inputs[0]


def test_user_cannot_self_assign_strong_natures():
    asyncio.run(memory.STORE.add(SPACE_A, [{"id": "p1", "kind": "finding", "nature": "task_output", "subject": None,
                                            "content": "توصية: افتح فرعًا ثانيًا", "importance": 3, "created_at": 1.0,
                                            "run_id": "r", "evidence": None, "source_goal": "g"}]))
    c, h = TestClient(main.app), {"X-Memory-Space": SPACE_A}
    for bad in ("user_stated", "external_source", "nonsense"):
        assert c.patch("/api/memory/p1", json={"nature": bad}, headers=h).status_code == 400
    assert c.patch("/api/memory/p1", json={"nature": "scenario"}, headers=h).json()["nature"] == "scenario"


# ---------- failures are explicit and never lose the run ----------
async def test_memory_save_failure_keeps_answer_and_is_explicit(monkeypatch):
    async def boom(*a, **k):
        raise memory.MemoryUnavailable("memory add failed: 400 column nature does not exist")
    monkeypatch.setattr(memory.STORE, "add", boom)
    r, _ = await run("سؤال عادي عن التسويق", {"task_summary": "تسويق", "add": []})
    assert r.status == "done" and r.result == "جواب"
    assert r.memory["error"].startswith("save:")
    types = [e["type"] for e in r.events]
    assert "memory_error" in types and "memory_saved" not in types
    assert types.index("memory_error") < types.index("run_finished")


async def test_extraction_failure_is_explicit():
    class BadLLM(FakeLLM):
        async def chat(self, messages, *a, **k):
            if messages[0]["content"].startswith("أنت مسؤول ذاكرة"):
                return "ليس JSON"
            return await super().chat(messages, *a, **k)
    r, _ = await run("سؤال عادي عن التسويق", None, llm_cls=BadLLM)
    assert r.status == "done" and r.memory["error"].startswith("extract:")
    assert any(e["type"] == "memory_error" and e["stage"] == "extract" for e in r.events)


async def test_recall_failure_does_not_stop_the_run(monkeypatch):
    async def boom(*a, **k):
        raise memory.MemoryUnavailable("memory list failed: 503")
    monkeypatch.setattr(memory.STORE, "list", boom)
    r, _ = await run("سؤال عادي عن التسويق", {"task_summary": "تسويق", "add": []})
    assert r.status == "done" and r.result == "جواب" and r.memory["error"]


def test_memory_api_backend_failure_is_a_clear_502(monkeypatch):
    async def boom(*a, **k):
        raise memory.MemoryUnavailable("memory delete failed: 500")
    monkeypatch.setattr(memory.STORE, "delete", boom)
    monkeypatch.setattr(memory.STORE, "clear", boom)
    c, h = TestClient(main.app), {"X-Memory-Space": SPACE_A}
    for r in (c.delete("/api/memory/x1", headers=h), c.delete("/api/memory", headers=h)):
        assert r.status_code == 502 and "تعذر" in r.json()["detail"]


def test_saved_run_never_contains_the_memory_space():
    r = Run(goal="g", llm_public={}, memory_space=SPACE_A)
    assert SPACE_A not in json.dumps(r.full(), ensure_ascii=False)
