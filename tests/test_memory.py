import json
import time

from fastapi.testclient import TestClient

from app import memory
from app.llm import Usage, extract_json
from app.orchestrator import Orchestrator, Run

SPACE = "mem_test_space_0123456789abcdef"


def item(content, kind="finding", importance=3, age_days=0, id_=None):
    return {"id": id_ or content[:8], "kind": kind, "content": content, "run_id": "r",
            "importance": importance, "created_at": time.time() - age_days * 86400}


def test_arabic_normalisation_matches_variants():
    assert memory.tokens("المقهى") == memory.tokens("مقهى")
    assert memory.tokens("القهوة")[0] == memory.tokens("قهوه")[0]
    assert memory.tokens("أسعار")[0] == memory.tokens("اسعار")[0]


def test_rank_prefers_relevant_item():
    items = [item("سعر كوب القهوة المختصة في جدة بين 15 و30 ريال"), item("المستخدم يفضل لغة Python للبرمجة"),
             item("تكاليف المتجر الإلكتروني الشهرية")]
    top = memory.rank("كم سعر القهوة في المقاهي المختصة بجدة؟", items)[0][1]
    assert "القهوة" in top["content"]


def test_recall_includes_recent_tasks_and_profile():
    items = [item("طلب دراسة مقهى في جدة؛ نقطة التعادل 1521-4375 كوب", kind="task", id_="t1"),
             item("المستخدم صاحب عمل في جدة", kind="profile", importance=5, id_="p1"),
             item("معلومة عن الطقس في لندن", id_="f1")]
    got = {i["id"] for i in memory.recall("أكمل الخطة التسويقية", items)}
    assert {"t1", "p1"} <= got and "f1" not in got


def test_render_context_respects_budget():
    items = [item("س" * 300, id_=str(i)) for i in range(50)]
    assert len(memory.render_context(items)) <= memory.CONTEXT_BUDGET + 400


def test_secrets_are_never_stored():
    assert memory._clean("مفتاح المستخدم sk-or-v1-abcdefghijklmnop") is None
    assert memory._clean("token sbp_fcd1dfdeb2121d349d4da863fe3d") is None
    assert memory._clean("كلمة المرور هي 1234") is None
    assert memory._clean("ميزانية المقهى 200 ألف ريال") == "ميزانية المقهى 200 ألف ريال"


def test_valid_space():
    assert memory.valid_space(SPACE)
    assert not memory.valid_space("short") and not memory.valid_space("x" * 25 + "/../") and not memory.valid_space(None)


class MemLLM:
    """Fake model: records planner input, returns a fixed extraction."""

    def __init__(self, extraction):
        self.extraction, self.usage, self.planner_inputs = extraction, Usage(), []

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
            return json.dumps(self.extraction(messages), ensure_ascii=False)
        if sys.startswith("أنت المنسق. اجمع"):
            return "جواب"
        return json.dumps({"action": "final", "output": "ناتج"})

    async def chat_json(self, messages, *a, **k):
        return extract_json(await self.chat(messages))


async def test_memory_round_trip_through_orchestrator():
    memory.STORE._mem.clear()
    first = MemLLM(lambda m: {"task_summary": "دراسة مقهى مختص في جدة بتكاليف ثابتة 35 ألف ريال",
                              "add": [{"kind": "project", "content": "مشروع مقهى مختص في جدة، التكاليف الثابتة 35 ألف ريال شهريًا", "importance": 5},
                                      {"kind": "profile", "content": "المستخدم صاحب عمل في جدة", "importance": 4},
                                      {"kind": "finding", "content": "secret sk-or-v1-abcdefghijklmnopqrst", "importance": 2}]})
    r1 = Run(goal="ادرس مقهى مختص في جدة", llm_public={}, memory_space=SPACE)
    await Orchestrator(r1, first).execute()
    assert len(r1.memory["saved"]) == 3  # summary + 2 (secret dropped)
    assert any(e["type"] == "memory_saved" for e in r1.events)

    second = MemLLM(lambda m: {"task_summary": "خطة تسويق للمقهى", "add": [], "supersede": []})
    r2 = Run(goal="اكتب خطة تسويق لمشروع المقهى", llm_public={}, memory_space=SPACE)
    await Orchestrator(r2, second).execute()
    assert "35 ألف" in second.planner_inputs[0]  # recalled context reached the planner
    assert r2.memory["recalled"]


async def test_supersede_replaces_outdated_item():
    memory.STORE._mem.clear()
    await memory.STORE.add(SPACE, [item("ميزانية المقهى 200 ألف ريال", kind="project", id_="old1")])
    existing = await memory.STORE.list(SPACE)
    fake = MemLLM(lambda m: {"task_summary": "تحديث الميزانية",
                             "add": [{"kind": "project", "content": "ميزانية المقهى صارت 300 ألف ريال", "importance": 5}],
                             "supersede": ["old1", "not-a-real-id"]})
    res = await memory.extract_and_save(fake, SPACE, "r2", "الميزانية صارت 300 ألف", "تم", existing)
    contents = [i["content"] for i in await memory.STORE.list(SPACE)]
    assert res["superseded"] == ["old1"]
    assert "ميزانية المقهى 200 ألف ريال" not in contents and any("300" in c for c in contents)


async def test_no_memory_space_means_no_memory():
    memory.STORE._mem.clear()
    fake = MemLLM(lambda m: {"task_summary": "x", "add": []})
    r = Run(goal="g", llm_public={})
    await Orchestrator(r, fake).execute()
    assert r.memory["enabled"] is False and not memory.STORE._mem


def test_memory_api():
    from app.main import app
    memory.STORE._mem.clear()
    c = TestClient(app)
    h = {"X-Memory-Space": SPACE}
    assert c.get("/api/memory").status_code == 400
    assert c.get("/api/memory", headers=h).json()["count"] == 0
    import asyncio
    asyncio.run(memory.STORE.add(SPACE, [item("عنصر للاختبار", id_="i1"), item("عنصر ثان", id_="i2")]))
    assert c.get("/api/memory", headers=h).json()["count"] == 2
    assert c.delete("/api/memory/i1", headers=h).json()["deleted"] == 1
    assert c.get("/api/memory", headers={"X-Memory-Space": "other_space_0123456789abcdef"}).json()["count"] == 0
    assert c.delete("/api/memory", headers=h).json()["deleted"] == 1
    assert c.post("/api/runs", json={"goal": "x", "memory_space": "bad"}).status_code == 400


def test_attached_prefixes_and_suffixes_match():
    assert memory.tokens("لمشروعي") == memory.tokens("مشروع")
    assert memory.tokens("بجدة") == memory.tokens("جدة")
    assert memory.tokens("لنقطة")[0] == memory.tokens("نقطة")[0]


def test_followup_recalls_project_facts():
    items = [item("مشروع مقهى في الروضة بجدة، التكاليف الثابتة 35 ألف ريال", kind="project", importance=5, id_="p"),
             item("حساب نقطة التعادل للمقهى", kind="task", id_="t")]
    got = {i["id"] for i in memory.recall("اكتب خطة تسويق للشهر الأول", items)}
    assert got == {"p", "t"}
