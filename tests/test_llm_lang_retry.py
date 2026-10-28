#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 16): مرشح «إعادة محدودة عند اختلاف اللغة» — أحمر سلوكي أولًا.

كل الاختبارات عبر مسار الاستدعاء الفعلي (``_complete`` الفرع المجاني) مع
التقاط الرسائل عند حد النقل نفسه (``_keyless_post``) — لا اختبار عبر أداة
القياس وحدها. قبل تنفيذ السياسة تفشل هذه الاختبارات: الرد بمكانة اللغة
الخاطئة يعاد كما هو من محاولة واحدة.
"""
from __future__ import annotations

import asyncio


from agent_civilization.core import llm as _llm
from agent_civilization.core import lang_policy


def _post_capture(replies: list[str]):
    """بديل ``_keyless_post`` يسجل الرسائل المُرسَلة فعليًا عند حد النقل
    ويعرض الردود بالترتيب (كل استدعاء HTTP = رد واحد)."""
    calls: list[dict] = []

    def fake_post(system, prompt, max_tokens, timeout):
        idx = len(calls)
        calls.append({"system": system, "prompt": prompt,
                      "max_tokens": max_tokens, "timeout": timeout})
        if idx >= len(replies):
            raise AssertionError(f"unexpected extra provider call #{idx + 1}")
        return {"choices": [{"message": {"content": replies[idx]}}]}

    return fake_post, calls


def _run(monkeypatch, replies, prompt, system="system", retry=True,
         max_wait_s=None):
    """عبر العقد العام ``complete`` لمستكتب مجاني (بديل نقل عند الحد نفسه)."""
    fake_post, calls = _post_capture(replies)
    monkeypatch.setattr(_llm, "_keyless_post", fake_post)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_llm, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_llm, "KEYLESS_CALL_TIMEOUT_S", 5.0)
    consent = _llm.set_public_llm_consent(True)
    token = _llm.set_llm_lang_retry(retry)
    try:
        client = _llm.LLMClient()
        assert client.provider == "keyless"
        out = asyncio.run(client.complete(system, prompt, 100,
                                          max_wait_s=max_wait_s))
    finally:
        _llm.reset_public_llm_consent(consent)
        _llm.reset_llm_lang_retry(token)
    return out, calls


AR_Q = "ما اسم الرئيس التنفيذي لشركة نور البيان؟"        # سؤال عربي
EN_Q = "What is the name of the CEO of Lightline Media?"  # سؤال إنجليزي
AR_OK = "رئيسها التنفيذي هو عبد الرحمن الفهد [S1]."       # رد عربي سليم
EN_OK = "The CEO is Sarah Hazelwood [S1]."               # رد إنجليزي سليم
EN_TO_AR = "الرئيس التنفيذي هو سارة هازلوود [S1]."         # رد عربي لسؤال إنجليزي
AR_TO_EN = "The CEO is Sarah Hazelwood [S1]."             # رد إنجليزي لسؤال عربي


def test_retry_off_returns_mismatched_reply_unchanged(monkeypatch):
    """التعطيل الصريح: الرد باللغة الخاطئة يعود كما هو من محاولة واحدة."""
    out, calls = _run(monkeypatch, [AR_TO_EN], AR_Q, retry=False)
    assert out["ok"] is True
    assert out["content"] == AR_TO_EN
    assert len(calls) == 1


def test_wrong_then_right_two_calls_second_accepted(monkeypatch):
    """السلوك الجوهري: رد خاطئ ثم صحيح ⇒ محاولتان فقط، والثاني هو المقبول."""
    out, calls = _run(monkeypatch, [AR_TO_EN, AR_OK], AR_Q)
    assert out["ok"] is True
    assert out["content"] == AR_OK
    assert len(calls) == 2
    # الرسالة الثانية عند حد النقل تحمل نص التصحيح المُجمَّد
    assert lang_policy.CORRECTION["ar"] in calls[1]["system"]
    assert lang_policy.CORRECTION["ar"] not in calls[0]["system"]
    # السؤال نفسه بلا تعديل (إعادة توليد من المدخل نفسه)
    assert calls[1]["prompt"] == calls[0]["prompt"]
    assert out["lang_retry"]["attempts"] == 2


def test_right_from_start_single_call(monkeypatch):
    """رد صحيح من البداية ⇒ محاولة واحدة فقط (لا إهدار استدعاءات)."""
    out, calls = _run(monkeypatch, [AR_OK], AR_Q)
    assert out["ok"] is True and out["content"] == AR_OK
    assert len(calls) == 1


def test_persistent_mismatch_no_third_attempt(monkeypatch):
    """الرد خاطئ مرتين ⇒ لا محاولة ثالثة، والرد الثاني هو المقبول كما هو
    (لا نجاح مصطنع ولا «أفضل الردّين»)."""
    out, calls = _run(monkeypatch, [AR_TO_EN, AR_TO_EN + " again"], AR_Q)
    assert out["ok"] is True
    assert out["content"] == AR_TO_EN + " again"
    assert len(calls) == 2
    assert out["lang_retry"]["attempts"] == 2


def test_retry_error_falls_back_to_first_reply(monkeypatch):
    """فشل الاستدعاء الثاني (خطأ مزود) ⇒ حفظ الرد الأول (سياسة حتمية، لا
    فقدان جواب صالح بسبب فشل الإعادة)."""

    def failing_post(system, prompt, max_tokens, timeout):
        if "LANGUAGE CORRECTION" not in system:  # المحاولة الأولى فقط
            return {"choices": [{"message": {"content": AR_TO_EN}}]}
        raise OSError("provider down on retry")

    monkeypatch.setattr(_llm, "_keyless_post", failing_post)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_llm, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_llm, "KEYLESS_CALL_TIMEOUT_S", 5.0)
    consent = _llm.set_public_llm_consent(True)
    token = _llm.set_llm_lang_retry(True)
    try:
        client = _llm.LLMClient()
        out = asyncio.run(client.complete("system", AR_Q, 100))
    finally:
        _llm.reset_public_llm_consent(consent)
        _llm.reset_llm_lang_retry(token)
    assert out["ok"] is True
    assert out["content"] == AR_TO_EN
    assert out["lang_retry"]["reason"] == "retry error; first reply kept"


def test_english_question_arabic_reply_retries(monkeypatch):
    """الاتجاه المعاكس (حماية الإنجليزية — درس v3): سؤال إنجليزي برد عربي
    ⇒ إعادة بنص التصحيح الإنجليزي."""
    out, calls = _run(monkeypatch, [EN_TO_AR, EN_OK], EN_Q)
    assert out["content"] == EN_OK
    assert len(calls) == 2
    assert lang_policy.CORRECTION["en"] in calls[1]["system"]


def test_code_json_token_numbers_alone_do_not_trigger_retry(monkeypatch):
    """عناصر الشيفرة/JSON/الرمز/الأرقام/الأسماء اللاتينية وحدها لا تسبب إعادة:
    رد عربي يحتويها كاملة = مطابق (محاولة واحدة)."""
    reply = ("INSUFFICIENT_EVIDENCE — المصادر المتاحة لا تذكر هذه المعلومة.\n\n"
             "```python\ndef count_vowels(s):\n    return sum(c in 'aeiou' for c in s)\n```\n"
             '{"rows": 4, "data": [1, 2, 3]}\nSarah Hazelwood, Taranhou, 1575')
    out, calls = _run(monkeypatch, [reply], AR_Q)
    assert out["content"] == reply
    assert len(calls) == 1


def test_token_only_reply_no_retry(monkeypatch):
    """رد = الرمز فقط (لا نص بعده) ⇒ غير قابل للحكم ⇒ لا إعادة."""
    out, calls = _run(monkeypatch, ["INSUFFICIENT_EVIDENCE"], AR_Q)
    assert out["content"] == "INSUFFICIENT_EVIDENCE"
    assert len(calls) == 1


def test_ambiguous_prompt_no_retry(monkeypatch):
    """مدخل غامض (عربية 20–50%) ⇒ سياسة محددة سلفًا: لا إعادة."""
    mixed = "What is اسم الرئيس التنفيذي the CEO name of the company?"
    out, calls = _run(monkeypatch, [AR_TO_EN], mixed)
    assert len(calls) == 1
    assert out["content"] == AR_TO_EN
    assert lang_policy.target_language(mixed) is None  # داخل النطاق الغامض فعلًا


def test_no_retry_without_budget(monkeypatch):
    """احترام الميزانية الكلية: لا وقت متبقٍ للإعادة ⇒ تُقبل الأولى كما هي
    مع سبب موثق (لا تجاوز للمهلة الكلية)."""
    fake_post, calls = _post_capture([AR_TO_EN, AR_OK])
    monkeypatch.setattr(_llm, "_keyless_post", fake_post)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_llm, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_llm, "KEYLESS_CALL_TIMEOUT_S", 5.0)
    consent = _llm.set_public_llm_consent(True)
    token = _llm.set_llm_lang_retry(True)
    try:
        client = _llm.LLMClient()
        out = asyncio.run(client.complete("system", AR_Q, 100, max_wait_s=0.0))
    finally:
        _llm.reset_public_llm_consent(consent)
        _llm.reset_llm_lang_retry(token)
    assert out["ok"] is True
    assert out["content"] == AR_TO_EN
    assert out["lang_retry"]["reason"] == "no budget for retry"


def test_env_flag_enables_policy(monkeypatch):
    """التفعيل البيئي ``AGOS_LLM_LANG_RETRY=1`` يعمل دون contextvar (مسار
    الإنتاج)، وغيابه يعطّل (الافتراضي الحالي: مرشح تحت الاختبار)."""
    monkeypatch.setenv("AGOS_LLM_LANG_RETRY", "1")
    out, calls = _run(monkeypatch, [AR_TO_EN, AR_OK], AR_Q, retry=None)
    assert out["content"] == AR_OK and len(calls) == 2
    monkeypatch.delenv("AGOS_LLM_LANG_RETRY")
    out2, calls2 = _run(monkeypatch, [AR_TO_EN], AR_Q, retry=None)
    assert out2["content"] == AR_TO_EN and len(calls2) == 1


def test_cancellation_during_retry_propagates(monkeypatch):
    """الإلغاء أثناء الإعادة ينتشر (لا ابتلاع): المهمة الملغاة تُلغى فعلًا."""
    def slow_post(system, prompt, max_tokens, timeout):
        if "LANGUAGE CORRECTION" in system:
            import time as _time
            _time.sleep(5)  # الإعادة بطيئة
            raise AssertionError("should have been cancelled")
        return {"choices": [{"message": {"content": AR_TO_EN}}]}

    monkeypatch.setattr(_llm, "_keyless_post", slow_post)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_llm, "KEYLESS_BUDGET_S", 30.0)
    monkeypatch.setattr(_llm, "KEYLESS_CALL_TIMEOUT_S", 30.0)
    consent = _llm.set_public_llm_consent(True)
    token = _llm.set_llm_lang_retry(True)

    async def scenario():
        client = _llm.LLMClient()
        task = asyncio.create_task(client.complete("system", AR_Q, 100))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "not-cancelled"

    try:
        assert asyncio.run(scenario()) == "cancelled"
    finally:
        _llm.reset_public_llm_consent(consent)
        _llm.reset_llm_lang_retry(token)


def test_target_is_question_language_not_sources(monkeypatch):
    """سؤال عربي مع مصادر إنجليزية طويلة: الهدف يظل لغة السؤال لا لغة المصادر
    (مراجعة مستقلة قبل حكم v8). أحمر قبل تنقيح الكاشف: المصادر اللاتينية
    الطويلة كانت تجعل الهدف en خطأً."""
    from agent_civilization.eval import r169_bench_v5 as b
    case = {"id": "x", "expected_behavior": "answer", "lang": "ar",
            "question": "ما اسم الرئيس التنفيذي لشركة نور البيان؟",
            "sources": [{"id": "S1", "title": "Company profile", "date": "2025-01-01",
                         "kind": "document",
                         "text": "Nour Al-Bayan Corporation is a regional holding company founded in 1987. "
                                 "Its chief executive officer is Abdulrahman Al-Fahd, who joined in 2019 "
                                 "after leading the logistics division. The board meets quarterly."}]}
    msgs = b.grounded_v2_messages(case)
    assert lang_policy.target_language(msgs["user"]) == "ar"
    # والاتجاه المقابل: سؤال إنجليزي مع مصادر عربية طويلة ⇒ en
    case_en = {"id": "y", "expected_behavior": "answer", "lang": "en",
               "question": "Who is the chief executive officer of Nour Al-Bayan?",
               "sources": [{"id": "S1", "title": "ملف الشركة", "date": "2025-01-01", "kind": "document",
                            "text": "شركة نور البيان القابضة تأسست عام 1987 ويعمل بها أكثر من ألف "
                                    "موظف في عدة دول، ورئيسها التنفيذي عبد الرحمن الفهد منذ 2019."}]}
    msgs_en = b.grounded_v2_messages(case_en)
    assert lang_policy.target_language(msgs_en["user"]) == "en"


# ---------------------------------------------------------------------------
# R-170 دفعة 16 — إصلاح CI على Python 3.11 (السجل 317): metadata الإعادة يجب أن
# تعبر حد المهمة الذي ينشئه asyncio.wait_for (3.11) أو create_task (كل الإصدارات).
# أحمر أولًا: قبل الإصلاح كان هذا الاختبار يفشل على كل إصدار (create_task ينسخ
# السياق دائمًا) — لا يعتمد على إصدار Python كي يكشف الخلل في CI ومحليًا.
# ---------------------------------------------------------------------------
def test_lang_retry_metadata_crosses_task_boundary(monkeypatch):
    import asyncio as _aio
    from agent_civilization.core import llm as _l

    replies = ["The answer is not mentioned in the sources. [S1]",
               "الجواب غير مذكور في المصادر. [S1]"]
    calls = []

    def post(system, prompt, max_tokens, timeout):
        calls.append(system)
        return {"choices": [{"message": {"content": replies[len(calls) - 1]}}]}

    monkeypatch.setattr(_l, "_keyless_post", post)
    monkeypatch.setattr(_l, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_l, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_l, "KEYLESS_CALL_TIMEOUT_S", 5.0)

    async def main():
        rt = _l.set_llm_lang_retry(True)
        ct = _l.begin_lang_retry_capture()
        try:
            # حد مهمة صريح — يحاكي wait_for في 3.11 على كل الإصدارات
            out = await _aio.create_task(
                _l._keyless_maybe_lang_retry("sys", "ما هو الجواب؟", 64, 10.0))
            return out, _l.get_last_lang_retry(), _l.get_last_lang_retry()
        finally:
            _l.end_lang_retry_capture(ct)
            _l.reset_llm_lang_retry(rt)

    out, meta, again = _aio.run(main())
    assert len(calls) == 2
    assert out["lang_retry"]["attempts"] == 2
    assert meta is not None and meta["attempts"] == 2
    assert meta["reason"] == "first-reply language mismatch"
    assert again is None  # القراءة تستهلك — لا تسرب إلى الاستدعاء التالي


def test_lang_retry_capture_is_isolated_between_concurrent_tasks(monkeypatch):
    """كل مستدعٍ ينشئ حاويته في سياقه — مهمتان متزامنتان لا تتبادلان metadata."""
    import asyncio as _aio
    from agent_civilization.core import llm as _l

    def post(system, prompt, max_tokens, timeout):
        if "ar-task" in prompt and "CORRECTION" not in system:
            return {"choices": [{"message": {"content": "This is wrong language here."}}]}
        if "ar-task" in prompt:
            return {"choices": [{"message": {"content": "هذا جواب عربي صحيح."}}]}
        return {"choices": [{"message": {"content": "A correct English answer."}}]}

    monkeypatch.setattr(_l, "_keyless_post", post)
    monkeypatch.setattr(_l, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(_l, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(_l, "KEYLESS_CALL_TIMEOUT_S", 5.0)

    async def one(prompt):
        rt = _l.set_llm_lang_retry(True)
        ct = _l.begin_lang_retry_capture()
        try:
            await _aio.wait_for(_l._keyless_maybe_lang_retry("sys", prompt, 64, 10.0), 10)
            return _l.get_last_lang_retry()
        finally:
            _l.end_lang_retry_capture(ct)
            _l.reset_llm_lang_retry(rt)

    async def main():
        return await _aio.gather(one("ما هو الجواب؟ ar-task"), one("What is the answer?"))

    ar_meta, en_meta = _aio.run(main())
    assert ar_meta is not None and ar_meta["attempts"] == 2
    assert en_meta is None
