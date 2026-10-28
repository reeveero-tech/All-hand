#!/usr/bin/env python3
"""R-170/16 (لاحق 74): اختبارات ما قبل حكم v8 — توجيهات المراجعة المستقلة.

1) إثبات توصيل الذراع عبر _one_arm_case وصولًا إلى حد النقل: بيانات الإعادة
   الكاملة (attempts/first_reply_sha256) تصل إلى الصف المحفوظ؛ الأذرع الأخرى لا
   تعيد حتى مع العلم البيئي؛ الهدف يظل لغة السؤال.
2) معايير v8 حسابيًا (B1/B2/B3) على بيانات اصطناعية: نتيجة مستوفية تجتاز،
   وأخطاء المزود > 10% ⇒ «غير حاسم» (ثلاثي الحالات).
3) عزل المخرجات: تشغيل/تقييم v8 يكتب في دليل v8 حصرًا — أدلة حكم v7
   التاريخية في دليل v6 لا تُستبدل.
"""
from __future__ import annotations

import asyncio


from agent_civilization.eval import r169_bench_v5 as b5
from agent_civilization.core import lang_policy
from agent_civilization.core import llm as _llm
import scripts.r169_lang_pilot_v6 as pilot

EN_WRONG = "The answer is not mentioned in the sources. [S1]"
AR_OK = "الجواب غير مذكور في المصادر. [S1]"


def _fake_post(replies):
    calls = []

    def post(system, prompt, max_tokens, timeout):
        idx = len(calls)
        calls.append({"system": system, "prompt": prompt})
        assert idx < len(replies), f"unexpected extra provider call #{idx + 1}"
        return {"choices": [{"message": {"content": replies[idx]}}]}

    return post, calls


def _ar_case() -> dict:
    return next(c for c in b5.load_split("dev")
                if c["lang"] == "ar" and c["expected_behavior"] == "answer"
                and c.get("sources"))


def _run_arm(monkeypatch, arm, replies, env_flag="0"):
    post, calls = _fake_post(replies)
    monkeypatch.setattr(_llm, "_keyless_post", post)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.setenv("AGOS_LLM_LANG_RETRY", env_flag)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_llm, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_llm, "KEYLESS_CALL_TIMEOUT_S", 5.0)
    consent = _llm.set_public_llm_consent(True)
    try:
        rec = asyncio.run(pilot._one_arm_case(b5, arm, _ar_case()))
    finally:
        _llm.reset_public_llm_consent(consent)
    return rec, calls


def test_v4r_retry_metadata_reaches_saved_row(monkeypatch):
    """خاطئ ثم صحيح عبر _one_arm_case: إعادة واحدة، والثاني مقبول، وبيانات
    الإعادة الكاملة (attempts/first_reply_sha256/reason) تصل إلى الصف."""
    rec, calls = _run_arm(monkeypatch, "grounded-v4r-lang", [EN_WRONG, AR_OK])
    assert len(calls) == 2
    assert rec["reply"].strip() == AR_OK
    lr = rec["lang_retry"]
    assert lr["policy"] == "bounded-retry-v1" and lr["target"] == "ar"
    assert lr["attempts"] == 2
    assert lr["reason"] == "first-reply language mismatch"
    assert lr["first_reply_sha256"]
    assert lang_policy.target_language(_ar_case()["question"]) == "ar"


def test_other_arms_no_retry_even_with_env_flag(monkeypatch):
    """v4 وv2: نفس الرد المخالف ⇒ لا إعادة لغوية حتى مع تفعيل العلم البيئي —
    الفرق الوحيد بين v4 وv4r هو السياسة (ذراع الضبط)."""
    for arm in ("grounded-v4-lang", "grounded-v2"):
        rec, calls = _run_arm(monkeypatch, arm, [EN_WRONG], env_flag="1")
        assert len(calls) == 1, f"{arm} must not retry"
        assert rec["reply"].strip() == EN_WRONG
        assert "lang_retry" not in rec


def test_v8_score_criteria_qualifying_synthetic(monkeypatch, tmp_path):
    """بيانات اصطناعية مستوفية لـ B1/B2/B3 ⇒ «candidate promising»."""
    _synthetic_score(monkeypatch, tmp_path, v4_ar_wrong=5, v2_ar_wrong=4,
                     provider_errors=(0, 0, 0))
    res = pilot.score(dataset="v8")
    cr = res["criteria"]
    assert cr["B1"] and cr["B2"] and cr["B3"]
    assert cr["decision"] == "candidate promising"


def test_v8_score_inconclusive_by_provider_errors(monkeypatch, tmp_path):
    """أخطاء مزود > 10% ⇒ «غير حاسم» (أولوية على لم يثبت — قرار ثلاثي الحالات)."""
    _synthetic_score(monkeypatch, tmp_path, v4_ar_wrong=5, v2_ar_wrong=4,
                     provider_errors=(3, 3, 8))  # 14 > 0.10*3*46
    res = pilot.score(dataset="v8")
    assert res["criteria"]["inconclusive_by_errors"]
    assert res["criteria"]["decision"] == "inconclusive"


def test_v8_score_b1_fails_when_no_improvement(monkeypatch, tmp_path):
    """v4r بلا تفوق ⇒ B1 فاشل والقرار «not proven»."""
    _synthetic_score(monkeypatch, tmp_path, v4_ar_wrong=0, v2_ar_wrong=0,
                     provider_errors=(0, 0, 0))
    res = pilot.score(dataset="v8")
    assert not res["criteria"]["B1"]
    assert res["criteria"]["decision"] == "not proven"


def test_v8_outputs_are_isolated_from_v6_dir(monkeypatch, tmp_path):
    """تشغيل v8 يكتب في دليل v8 حصرًا — أدلة v7 التاريخية في دليل v6 لا تُمس."""
    assert pilot._out("v8") == pilot.OUT_V8 and pilot.OUT_V8 != pilot.OUT
    assert pilot._out("v7") == pilot.OUT
    # الملفات التاريخية موجودة وتُقارن قبل/بعد تقييم v8
    hist = {p.name: p.read_bytes()
            for p in pilot.OUT.glob("holdout*")}
    assert hist, "the v7 judgment evidence must exist in the v6 dir"
    _synthetic_score(monkeypatch, tmp_path, v4_ar_wrong=5, v2_ar_wrong=4,
                     provider_errors=(0, 0, 0))
    assert pilot.score(dataset="v8")["criteria"]["decision"] == "candidate promising"
    assert {p.name: p.read_bytes() for p in pilot.OUT.glob("holdout*")} == hist


def _synthetic_score(monkeypatch, tmp_path, v4_ar_wrong, v2_ar_wrong,
                     provider_errors):
    """تقييم v8 ببيانات اصطناعية عبر monkeypatch: المخرجات إلى tmp_path."""
    monkeypatch.setattr(pilot, "OUT_V8", tmp_path)
    cases = {c["id"]: c for c in pilot._bench_for("holdout", "v8").load_split("holdout")}
    ar_ids = [i for i, c in cases.items() if c["lang"] == "ar"]
    en_ids = [i for i, c in cases.items() if c["lang"] == "en"]

    def arm_rows(wrong_ar, n_err):
        rows = {}
        for i in cases:
            r = {"language_match": True, "pass": True, "provider_error": None}
            if i in ar_ids and i in wrong_ar:
                r["language_match"] = False
            rows[i] = r
        for i in list(rows)[:n_err]:
            rows[i]["provider_error"] = "synthetic"
        return rows

    v4_wrong = set(ar_ids[:v4_ar_wrong])
    v2_wrong = set(ar_ids[:v2_ar_wrong])
    monkeypatch.setattr(pilot, "_scores",
                        lambda split, arm, tag="", dataset="v7": {
                            "grounded-v2": arm_rows(v2_wrong, provider_errors[0]),
                            "grounded-v4-lang": arm_rows(v4_wrong, provider_errors[1]),
                            "grounded-v4r-lang": arm_rows(set(), provider_errors[2]),
                        }[arm])
    _ = en_ids  # كل الإنجليزية مطابقة في كل الأذرع
