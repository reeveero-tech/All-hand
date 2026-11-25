"""A failed task keeps its best attempt, not its last; the verifier knows which pages were really read;
the report separates pages read from links merely cited."""
import asyncio
import json

from app import export
from app.orchestrator import pick_best
from tests.test_core import FakeLLM, T, plan_of, run_with


def test_pick_best_prefers_score_then_reading_then_earliest():
    a = lambda s, r=0: {"output": "x", "read": ["u"] * r, "verdict": {"score": s}}
    assert pick_best([a(3, 5), a(3, 0), a(3, 0)]) == 0
    assert pick_best([a(3), a(5), a(4)]) == 1
    assert pick_best([a(3, 0), a(3, 2)]) == 1
    assert pick_best([{"error": "x"}]) is None


def test_failed_task_keeps_the_better_first_attempt():
    outputs = iter(["بيانات حقيقية من أمازون", "حصة ستاربكس 12% (مختلقة)"])
    verdicts = [{"pass": False, "score": 4, "issues": ["ناقص"]}, {"pass": False, "score": 2, "issues": ["مختلق"]},
                {"pass": False, "score": 1, "issues": ["x"]}]
    fake = FakeLLM(plan_of(T("a", role="writer"), T("b", deps=["a"])), verdicts=verdicts,
                   agent=lambda m: json.dumps({"action": "final", "output": next(outputs, "ثالثة"), "confidence": 0.5}))
    run = asyncio.run(run_with(fake))
    a = run.tasks["a"]
    assert a["status"] == "failed" and a["best_attempt"] == 1 and a["output"] == "بيانات حقيقية من أمازون"


def test_verifier_is_told_what_was_read():
    seen = []

    class Spy(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت مدقق"):
                seen.append(messages[-1]["content"])
            return await super().chat(messages, temperature, max_tokens)
    asyncio.run(run_with(Spy(plan_of(T("r", role="researcher"), T("w")))))
    assert any("صفحات فتحها الوكيل فعلًا" in m for m in seen)  # researcher
    assert sum("صفحات فتحها الوكيل فعلًا" in m for m in seen) == 1  # not the writer


def test_report_separates_read_from_cited():
    run = {"goal": "g", "tasks": {"t1": {"id": "t1", "title": "بحث", "status": "failed", "best_attempt": 1, "output": "o",
           "attempts": [{"read": ["https://amazon.sa/p"], "sources": ["https://amazon.sa/p", "https://fake.example"], "verdict": {"score": 3}},
                        {"read": [], "sources": ["https://invented.example"], "verdict": {"score": 3}}]}}}
    md = export.to_markdown(run)
    assert "صفحات فتحها الوكلاء فعلًا" in md and "1. https://amazon.sa/p" in md
    assert "- https://fake.example" in md and "invented.example" not in md  # links of an attempt that was not used
