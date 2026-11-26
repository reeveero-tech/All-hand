"""Lifecycle with the real research manager on a fake web: a stop never makes the run read its sources again."""
import asyncio
import json

import pytest

from app import llm as L
from app.orchestrator import Orchestrator, Run
from tests.test_research_manager import HTML, Web, install, on, page  # noqa: F401  (fixture)

GOOD = page("سعر القهوة الباردة المعلبة", "سعر القهوة الباردة المعلبة 9 ريال في السعودية لدى المتاجر")


@pytest.fixture(autouse=True)
def _clear():
    L._HOLDS.clear()
    yield
    L._HOLDS.clear()


class Model:
    cfg = type("C", (), {"provider": "test"})()

    def __init__(self, refuse=()):
        self.usage, self.refuse, self.kinds = L.Usage(), set(refuse), []

    def _kind(self, sys_):
        if "مخطط بحث" in sys_:
            return "research"
        if sys_.startswith("أنت وكيل"):
            return "agent"
        if sys_.startswith("أنت المنسق الرئيسي"):
            return "plan"
        if sys_.startswith("أنت مدقق"):
            return "review"
        if sys_.startswith("أنت المنسق. اجمع"):
            return "report"
        return "other"

    async def chat_json(self, messages, *a, **k):
        return json.loads(await self.chat(messages))

    async def chat(self, messages, *a, **k):
        kind = self._kind(messages[0]["content"])
        if kind in self.refuse:
            raise L.ModelPaused("HTTP 402 FREE_LIMIT", kind="quota_exhausted", status=402)
        self.kinds.append(kind)
        self.usage.calls += 1
        if kind == "research":
            return json.dumps({"subject_en": "canned cold brew coffee", "queries": {}})
        if kind == "plan":
            return json.dumps({"summary": "s", "simple": False, "tasks": [
                {"id": "t1", "title": "أسعار", "role": "researcher", "instructions": "ابحث عن الأسعار", "acceptance": "أسعار بمصادر",
                 "depends_on": []},
                {"id": "t2", "title": "تقرير", "role": "writer", "instructions": "اكتب", "acceptance": "تقرير", "depends_on": ["t1"]}]})
        if kind == "agent":
            return json.dumps({"action": "final", "output": "السعر 9 ريال (المصدر: https://shop.example/p)", "confidence": 0.8,
                               "sources": ["https://shop.example/p"]})
        if kind == "review":
            return json.dumps({"pass": True, "score": 9, "issues": []})
        if kind == "report":
            return "# تقرير\n## الملخص التنفيذي\nالسعر 9 ريال (المصدر: https://shop.example/p)"
        return json.dumps({"claims": [], "items": []})


def _go(model, prior=None):
    r = Run(goal="حلل أسعار القهوة الباردة المعلبة في السعودية", llm_public={})
    asyncio.run(Orchestrator(r, model, prior=prior).execute())
    return r


def _web(on):
    web = Web({"https://shop.example/p": (200, GOOD, HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة المعلبة 9 ريال", "url": "https://shop.example/p", "snippet": "9 ريال"}]})
    return web


def test_quota_during_research_keeps_evidence_and_resume_does_not_reopen(on):
    web = _web(on)
    first = _go(Model(refuse={"research", "agent"}))  # the quota is gone while research plans its queries
    assert first.status == "paused_quota"
    rs = first.research_state
    assert rs and rs["done"] and rs["readings"]  # research ran on code alone (fallback queries) and kept what it read
    hits = dict(web.hits)
    m2 = Model()
    second = _go(m2, prior=json.loads(json.dumps(first.full(), default=str)))
    assert web.hits == hits  # no source was opened again
    assert "research" not in m2.kinds and "plan" not in m2.kinds
    assert second.research_state["readings"].keys() == rs["readings"].keys()  # evidence kept as is
    assert second.status in ("done", "partial") and second.files and second.files[0]["verified"]


def test_quota_during_analysis_after_research(on):
    web = _web(on)
    first = _go(Model(refuse={"agent"}))
    assert first.status == "paused_quota" and first.research_state["done"]
    assert first.checkpoint["research"]["done"] and first.checkpoint["research"]["readings"] >= 1
    hits = dict(web.hits)
    second = _go(Model(), prior=json.loads(json.dumps(first.full(), default=str)))
    assert web.hits == hits and second.status in ("done", "partial")
    assert any(e["type"] == "resumed" and e["research_reused"] for e in second.events)
