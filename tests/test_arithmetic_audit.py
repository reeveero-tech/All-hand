"""Found on production (follow-up 978e636a8163, t3): six budget items summed to '120 000' when they make
110 000, the agent skipped the calculator ('the numbers are simple') and the verifier gave it 8/10."""
import asyncio

from app import orchestrator as o
from tests.test_core import FakeLLM, T, plan_of, run_with

PROD = "- **إجمالي التكاليف** = 12 000 + 30 000 + 15 000 + 20 000 + 25 000 + 8 000 = 120 000 ريال"


def test_catches_the_production_sum():
    e = o.audit_arithmetic(PROD)
    assert e and e[0]["stated"] == 120000 and e[0]["correct"] == 110000


def test_no_false_alarms():
    for ok in ["200 000 ÷ 3.75 = 53 333 دولار", "10 % + 25 % = 35 %", "10 % × 120 000 = 12 000", "5,000 × 12 = 60,000",
               "الأسابيع 5-12 = 8 أسابيع", "120 000 - 20 000 = 100 000", "1 USD = 3.75", "(12 + 8) × 2 = 40",
               "ورد في الطلب 200 000 / 3.75 = 75 000 وهذا خطأ، الصحيح 53 333"]:
        assert o.audit_arithmetic(ok) == [], ok


def test_audit_fails_an_output_the_model_verifier_passed():
    v = o.apply_audit({"pass": True, "score": 8, "issues": [], "fix": ""}, PROD)
    assert v["pass"] is False and v["score"] <= 4 and "110,000" in v["issues"][0]
    assert o.apply_audit({"pass": True, "score": 8, "issues": []}, "لا حسابات") == {"pass": True, "score": 8, "issues": []}


def test_agent_with_wrong_sum_is_retried_with_the_correction():
    seen = []

    def agent(messages):
        seen.append(messages[1]["content"])
        out = PROD if len(seen) == 1 else PROD.replace("120 000", "110 000")
        return '{"action": "final", "output": "%s"}' % out.replace('"', "'")

    run = asyncio.run(run_with(FakeLLM(plan_of(T("a"), T("b")), agent=agent)))
    a = run.tasks["a"]
    assert a["status"] == "done" and len(a["attempts"]) == 2 and a["attempts"][0]["verdict"]["pass"] is False
    assert "110,000" in seen[1]


TABLE = """| بند | مبلغ (ريال) | % |
|------|------|------|
| تخطيط | 12 000 | 10 % |
| تطوير | 30 000 | 25 % |
| اختبار | 15 000 | 12.5 % |
| إطلاق | 20 000 | 16.7 % |
| توزيع | 25 000 | 20.8 % |
| متابعة | 8 000 | 6.7 % |
| **إجمالي** | **120 000** | 100 % |"""


def test_table_total_row_is_checked():
    e = o.audit_tables(TABLE)
    assert [x["correct"] for x in e] == [110000, 91.7]
    assert o.apply_audit({"pass": True, "score": 9, "issues": []}, TABLE)["pass"] is False


def test_table_without_errors_or_with_subtotals_passes():
    ok = "| a | b |\n|---|---|\n| x | 5 |\n| y | 7 |\n| المجموع الفرعي | 12 |\n| z | 1 |\n| w | 2 |\n| الإجمالي | 3 |"
    assert o.audit_tables(ok) == []
    mixed = "| a | b |\n|---|---|\n| x | 18 ريال / 32 ريال |\n| y | 7 |\n| total | 50 |"
    assert o.audit_tables(mixed) == []


def test_total_written_under_a_table():
    """Production follow-up 6e07e45bd57f, t1 (verifier 10/10): rows make 112 800 and 94 %."""
    t = """| المرحلة | النسبة | المبلغ (ريال) |
|---|---|---|
| التحضير | 12 % | 14 400 |
| تطوير | 25 % | 30 000 |
| اختبار | 12 % | 14 400 |
| إطلاق | 17 % | 20 400 |
| توزيع | 21 % | 25 200 |
| متابعة | 7 % | 8 400 |

**المجموع**: 120 000 ريال (100 %)"""
    assert sorted(x["correct"] for x in o.audit_tables(t)) == [94, 112800]
    good = t.replace("14 400 |\n| تطوير", "21 600 |\n| تطوير").replace("12 % | 21 600", "18 % | 21 600")
    assert [x["correct"] for x in o.audit_tables(good)] == []


def test_answer_with_proven_arithmetic_error_is_not_remembered(monkeypatch):
    from app import memory
    saved = []

    class Store:
        async def list(self, space):
            return []
    monkeypatch.setattr(memory, "STORE", Store())

    async def fake_save(*a, **k):
        saved.append(1)
        return {"saved": [], "superseded": []}
    monkeypatch.setattr(memory, "extract_and_save", fake_save)

    class F(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت المنسق. اجمع"):
                return TABLE
            return await super().chat(messages, temperature, max_tokens)

    run = o.Run(goal="هدف", llm_public={}, memory_space="s" * 32)
    asyncio.run(o.Orchestrator(run, F(plan_of(T("a"), T("b")))).execute())
    assert run.verdict.get("arithmetic") and run.status == "partial"
    assert saved == [] and run.memory.get("skipped")
    assert len({i for i in run.verdict["issues"] if "خطأ حسابي" in i}) == len([i for i in run.verdict["issues"] if "خطأ حسابي" in i])
