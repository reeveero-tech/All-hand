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
