import asyncio

from app import orchestrator as o
from app.export import to_markdown


def test_grade_catches_wrong_conversion():
    c = o.grade_claims([{"text": "200 ألف ريال أي 75 ألف دولار بسعر 3.75", "expr": "200000 / 3.75",
                         "stated": "75,000", "unit": "دولار"}])
    assert c[0]["ok"] is False and c[0]["correct"] == 53333.33
    note = o.checks_note(c)
    assert "53,333.33" in note and "75,000" in note


def test_grade_accepts_correct_and_rejects_code():
    c = o.grade_claims([{"text": "x", "expr": "150000 / 3.75", "stated": 40000, "unit": "دولار"},
                        {"text": "y", "expr": "__import__('os')", "stated": 1},
                        {"text": "z", "expr": "1/0", "stated": 1}, "junk"])
    assert len(c) == 1 and c[0]["ok"] is True
    assert o.checks_note(c) == ""


def test_check_skips_goals_without_numbers():
    class Boom:
        async def chat_json(self, *a, **k):
            raise AssertionError("no LLM call expected")
    assert asyncio.run(o.check_goal_numbers(Boom(), "اكتب قصيدة")) == []


def test_check_uses_model_only_to_transcribe():
    class Fake:
        async def chat_json(self, *a, **k):  # the model even claims a wrong "correct" value; it is ignored
            return {"claims": [{"text": "t", "expr": "200000/3.75", "stated": 75000, "unit": "دولار", "correct": 75000}]}
    c = asyncio.run(o.check_goal_numbers(Fake(), "ميزانية 200 ألف ريال أي 75 ألف دولار"))
    assert c[0]["correct"] == 53333.33 and not c[0]["ok"]


def test_note_reaches_agents_and_export():
    r = o.Run(goal="ميزانية 200000 ريال", llm_public={})
    r.goal_checks = o.grade_claims([{"text": "t", "expr": "200000/3.75", "stated": 75000, "unit": "دولار"}])
    orch = o.Orchestrator(r, llm=None)
    assert "53,333.33" in orch.anchored()
    md = to_markdown({**r.full(), "tasks": {}})
    assert "تدقيق أرقام الطلب" in md and "53,333.33" in md


def test_all_rejected_gives_one_labeled_draft_not_a_dump():
    from tests.test_core import FakeLLM, T, plan_of, run_with
    seen = []

    class Spy(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت المنسق. اجمع"):
                seen.append(messages)
            return await super().chat(messages, temperature, max_tokens)

    bad = {"pass": False, "score": 3, "issues": ["رقم مختلق"], "fix": "احذفه"}
    run = asyncio.run(run_with(Spy(plan_of(T("a"), T("b")), verdicts=[bad] * 6)))
    assert run.status == "failed" and run.result == "الجواب النهائي المجمع"
    assert run.verdict.get("draft") is True
    assert "مسودة غير متحقق منها" in seen[0][0]["content"] and "رقم مختلق" in seen[0][1]["content"]
