"""Research reliability and what a follow-up may inherit (production findings of 2026-11-25):
- parallel agents burst DuckDuckGo into a block; searches are now gated, cached, and blocked hits skipped;
- agents ran 69 searches and opened zero pages; search now opens the top pages and records them as read;
- a follow-up inherited 3.79 billion cups a year and returned 604,000 %; figures are now checked for
  plausibility against each other and the population, and a previous answer is classified line by line."""
import asyncio
import json

import httpx

from app import agents, quality, tools
from app.agents import ROLES, run_agent
from app.llm import Usage, extract_json
from app.orchestrator import Orchestrator, Run, research_stats


class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def test_blocked_duckduckgo_is_not_hammered(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(tools, "DDG_INTERVAL", 0)
    hits = []

    async def get(self, url, **k):
        hits.append(url)
        return _Resp(202)

    async def post(self, url, **k):
        hits.append(url)
        return _Resp(202)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async def wiki(q, lang):
        return []
    monkeypatch.setattr(tools, "_wikipedia", wiki)

    async def go():
        return [await tools.web_search(q) for q in ("قهوة باردة", "Almarai coffee", "Barns coffee")]
    outs = asyncio.run(go())
    assert len(hits) == 1  # one refusal closes the gate for both endpoints and later queries
    assert all("يرفض" in o for o in outs)


def test_search_is_cached_and_top_pages_are_read(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(tools, "AUTO_READ", 2)
    calls = []

    async def lite(q):
        calls.append(q)
        return [{"title": "قهوة المراعي", "url": "https://shop.example/a", "snippet": "-"},
                {"title": "ويكي", "url": "https://ar.wikipedia.org/wiki/x", "snippet": "-"},
                {"title": "دليل", "url": "https://bad.example/b", "snippet": "-"}]

    async def fetch(url, max_chars=0):
        if "shop" in url:
            return f"HTTP 200 — {url}\n\nقائمة\nسعر قهوة المراعي الباردة 250 مل هو 4.5 ريال في متاجر السعودية.\nتواصل معنا"
        return "خطأ في الجلب: ConnectError"
    monkeypatch.setattr(tools, "_ddg", lite)
    monkeypatch.setattr(tools, "fetch_url", fetch)
    a = asyncio.run(tools.web_search("سعر قهوة المراعي الباردة"))
    b = asyncio.run(tools.web_search("سعر  قهوة المراعي الباردة"))
    assert len(calls) == 1 and a == b
    meta = tools.search_meta(a)
    assert meta == {"provider": "DuckDuckGo", "results": 3, "read": ["https://shop.example/a"]}
    assert "4.5 ريال" in a and "تواصل معنا" not in a


def test_agent_records_auto_read_and_world_bank_pages(monkeypatch):
    async def fake_tool(name, args, allowed):
        if name == "web_search":
            return "المصدر: DuckDuckGo\n1. t\n   https://a.example\n   s\n\n[قُرئت: https://a.example]\nسعر 5 ريال"
        return "[قُرئت: https://data.worldbank.org/indicator/SP.POP.TOTL?locations=SA]\n- 2025: 36,973,555"
    monkeypatch.setattr(agents, "call_tool", fake_tool)
    script = iter([{"action": "tool", "tool": "web_search", "input": {"query": "q"}},
                   {"action": "tool", "tool": "world_bank", "input": {"query": "SAU SP.POP.TOTL"}},
                   {"action": "final", "output": "x", "sources": []}])
    events = []

    class LLM:
        async def chat_json(self, messages, *a, **k):
            return next(script)

    async def emit(t, d):
        events.append((t, d))
    res = asyncio.run(run_agent(LLM(), ROLES["researcher"], "ابحث", "", emit, False))
    assert res["read"] == ["https://a.example", "https://data.worldbank.org/indicator/SP.POP.TOTL?locations=SA"]
    first = next(d for t, d in events if t == "tool_result")
    assert first["provider"] == "DuckDuckGo" and first["results"] == 1


def test_world_bank_parses_and_marks_source(monkeypatch):
    payload = [{"page": 1}, [{"indicator": {"value": "Population, total"}, "country": {"id": "SA", "value": "Saudi Arabia"},
                              "date": "2025", "value": 36973555}]]

    class R:
        def json(self):
            return payload

    async def get(self, url, **k):
        assert "SAU/indicator/SP.POP.TOTL" in url
        return R()
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    out = asyncio.run(tools.world_bank("SAU SP.POP.TOTL"))
    assert out.startswith("[قُرئت: https://data.worldbank.org/indicator/SP.POP.TOTL?locations=SA]") and "36,973,555" in out
    assert "خطأ" in asyncio.run(tools.world_bank("population"))


# --- plausibility ----------------------------------------------------------------------------------------------

def test_absurd_demand_and_the_return_built_on_it_are_errors():
    fig = {"population": 8_000_000, "market_units_per_year": 3_790_000_000, "market_unit": "كوب", "market_basis": "estimate",
           "roi_pct": 604000, "revenue_year1": 725_000_000, "total_cost_year1": 120_000}
    issues = quality.check_figures(fig)
    checks = {i["check"] for i in issues if i["severity"] == "error"}
    assert "demand_per_capita" in checks and "roi_unsupported" in checks
    assert "1.30 كوب لكل فرد يوميًا" in next(i["reason"] for i in issues if i["check"] == "demand_per_capita")


def test_reasonable_estimate_passes():
    # 37 M people, 8 % buy one cup a week: about 154 M cups; the plan sells 60 k at 9 SAR. Nothing to object to.
    fig = {"population": 36_973_555, "market_units_per_year": 153_810_000, "market_basis": "estimate",
           "planned_units_per_year": 60_000, "price": 9, "unit_cost": 5, "budget": 400_000,
           "revenue_year1": 540_000, "total_cost_year1": 420_000, "roi_pct": 28.6}
    assert quality.check_figures(fig) == []


def test_roi_is_recomputed_and_funding_gap_is_a_warning():
    fig = {"revenue_year1": 300_000, "total_cost_year1": 200_000, "roi_pct": 150,
           "planned_units_per_year": 50_000, "unit_cost": 6, "budget": 120_000}
    issues = quality.check_figures(fig)
    assert {i["check"]: i["severity"] for i in issues} == {"roi_recomputed": "error", "budget_funding": "warning"}


def test_apply_plausibility_fails_only_on_errors():
    v = {"pass": True, "score": 9, "issues": []}
    warn = {"issues": [{"check": "budget_funding", "severity": "warning", "reason": "r", "values": [1]}]}
    err = {"issues": [{"check": "demand_per_capita", "severity": "error", "reason": "r", "values": [1]}]}
    assert quality.apply_plausibility(v, warn)["pass"] is True
    out = quality.apply_plausibility(v, err)
    assert out["pass"] is False and out["score"] == 4.0 and out["plausibility"]


# --- what a follow-up inherits --------------------------------------------------------------------------------

PREV = """## الطلب
- حجم الطلب السنوي: 3.79 مليار كوب (تقدير)
- سكان السعودية 36,973,555 (المصدر: https://data.worldbank.org/indicator/SP.POP.TOTL?locations=SA)
- الميزانية 200,000 ريال
- سعر الكوب 9 ريال بافتراض هامش 40%
- نقطة التعادل = 200000 / 4 = 50,000 كوب
- العائد المتوقع 604,000%
- حصة المنافس الأكبر 35%"""


def test_previous_answer_is_classified_line_by_line():
    led = quality.prior_ledger(PREV, "ادرس إطلاق منتج بميزانية 200 ألف ريال", rejected=[3_790_000_000, 604000])
    tags = {it["line"].split(":")[0].strip("- "): it["tag"] for it in led}
    assert tags["حجم الطلب السنوي"] == "rejected"  # '3.79 مليار' is matched to 3,790,000,000
    assert tags["العائد المتوقع 604,000%"] == "rejected"
    assert tags["سكان السعودية 36,973,555 (المصدر"] == "sourced"
    assert tags["الميزانية 200,000 ريال"] == "user"
    assert tags["سعر الكوب 9 ريال بافتراض هامش 40%"] == "assumption"
    assert tags["نقطة التعادل = 200000 / 4 = 50,000 كوب"] == "calculated"
    assert tags["حصة المنافس الأكبر 35%"] == "uncertain"
    note = quality.render_ledger(led, [])
    assert "مرفوض — ثبت خطؤه" in note and "القيمة الجديدة تحل محل القديمة" in note


class FollowLLM:
    """Plans one writer task; the plausibility extractor reports the inherited absurd demand."""
    def __init__(self):
        self.usage = Usage()
        self.agent_ctx = []

    async def chat(self, messages, *a, **k):
        sys = messages[0]["content"]
        user = next(m["content"] for m in messages if m["role"] == "user")
        if sys.startswith("استخرج من النص الأرقام"):
            if "3.79" in user:
                return json.dumps({"population": 36973555, "market_units_per_year": 3790000000, "market_basis": "estimate",
                                   "roi_pct": 604000})
            return json.dumps({})
        if sys.startswith("أنت محلل أسواق"):
            return json.dumps({"typical_max_per_capita_year": 10, "basis": "تقديرات الفئة"})
        if sys.startswith("استخرج من نص المستخدم"):
            return json.dumps({"claims": []})
        if sys.startswith("أنت المنسق الرئيسي"):
            return json.dumps({"simple": False, "summary": "s", "tasks": [
                {"id": "t1", "title": "خطة", "role": "writer", "instructions": "أعد الخطة بميزانية 120 ألف", "acceptance": "ok"},
                {"id": "t2", "title": "مالية", "role": "writer", "instructions": "أعد المالية", "acceptance": "ok"}]})
        if sys.startswith("أنت مدقق"):
            return json.dumps({"pass": True, "score": 9, "issues": []})
        if sys.startswith("أنت المنسق. اجمع"):
            return "خطة معدلة بميزانية 120 ألف ريال"
        self.agent_ctx.append(user)
        return json.dumps({"action": "final", "output": "خطة معدلة"})

    async def chat_json(self, messages, *a, **k):
        return extract_json(await self.chat(messages))


def test_follow_up_marks_inherited_absurd_figures_rejected():
    llm = FollowLLM()
    run = Run(goal="استخدم نتائج الدراسة السابقة وأعد الخطة بميزانية 120 ألف ريال", llm_public={})
    run.context = "سياق المحادثة السابقة (هذا طلب متابعة للمهمة abc):\nالطلب السابق: ادرس بميزانية 200 ألف ريال\n\nالنتيجة السابقة:\n" + PREV
    run.prior_check = {"from": "abc", "known_rejected": []}
    asyncio.run(Orchestrator(run, llm).execute())
    pc = run.prior_check
    assert 3_790_000_000 in pc["rejected"] and 604000 in pc["rejected"]
    assert any(it["tag"] == "rejected" and "3.79" in it["line"] for it in pc["ledger"])
    assert all("مرفوض — ثبت خطؤه" in c and "3.79 مليار" in c for c in llm.agent_ctx)  # every agent is told
    assert any(e["type"] == "prior_checked" for e in run.events)
    assert run.status == "done"


def test_research_stats_count_what_really_happened():
    run = Run(goal="g", llm_public={})
    run.tasks = {"t1": {"role": "researcher"}}
    run.events = [{"type": "tool_call", "tool": "web_search"}, {"type": "tool_result", "tool": "web_search", "provider": "DuckDuckGo", "results": 6, "read": ["https://a.example/p"]},
                  {"type": "tool_call", "tool": "web_search"}, {"type": "tool_result", "tool": "web_search", "provider": None, "results": 0, "read": []},
                  {"type": "tool_call", "tool": "fetch_url"}, {"type": "tool_result", "tool": "fetch_url", "read": ["https://b.example"]}]
    run.result = "السعر 5 ريال (المصدر: https://a.example/p)\nالحصة 40%\nانظر https://c.example"
    s = research_stats(run)
    assert (s["search_calls"], s["search_ok"], s["results"], s["pages_read"], s["fetch_calls"]) == (2, 1, 6, 2, 1)
    assert s["sources_in_answer"] == 1 and s["links_not_read"] == ["https://c.example"]
    assert s["numeric_lines"] == 2 and s["numeric_lines_untagged"] == 1  # "الحصة 40%" has no source or label


def test_study_with_no_page_read_is_not_done():
    class LLM(FollowLLM):
        async def chat(self, messages, *a, **k):
            if messages[0]["content"].startswith("أنت المنسق الرئيسي"):
                return json.dumps({"simple": False, "summary": "s", "tasks": [
                    {"id": "t1", "title": "بحث", "role": "researcher", "instructions": "ابحث", "acceptance": "ok"},
                    {"id": "t2", "title": "خطة", "role": "writer", "instructions": "اكتب", "acceptance": "ok"}]})
            return await super().chat(messages, *a, **k)
    run = Run(goal="ادرس السوق", llm_public={})
    asyncio.run(Orchestrator(run, LLM()).execute())
    assert run.status == "partial" and "لم تُقرأ أي صفحة" in run.verdict["issues"][0]


def test_demand_judged_in_context_with_the_computed_per_capita_figure():
    fig = {"population": 36_973_555, "market_units_per_year": 3_790_000_000, "market_unit": "كوب", "market_basis": "estimate"}
    assert quality.check_figures(fig) == []  # 0.28 a day is not physically impossible...
    issues = quality.check_figures(fig, demand_judgment={"typical_max_per_capita_year": 10})
    assert issues[0]["check"] == "demand_context" and "102.5 كوب للفرد سنويًا" in issues[0]["reason"]  # ...but it is for one product
    assert quality.check_figures(fig, demand_judgment={"typical_max_per_capita_year": 60}) == []  # within 2x of a generous max


def test_country_is_whole_words_and_most_mentioned():
    assert quality.country_of("المصروفات التشغيلية في السوق السعودي، والسعودية سوق كبير") == "SAU"
    assert quality.country_of("المصروفات والإيرادات") is None


def test_unsourced_demand_behind_an_absurd_return_is_itself_rejected():
    # production follow-up 6e07e45bd57f: no population in the text, demand 3.79 bn cups "estimate",
    # revenue 68.2 bn from a 120 k budget, ROI 604,000 %
    fig = {"market_units_per_year": 3_790_000_000, "market_basis": "estimate", "budget": 120_000, "price": 18,
           "revenue_year1": 68_220_000_000, "total_cost_year1": 112_800, "roi_pct": 604000}
    issues = quality.check_figures(fig)
    checks = {i["check"] for i in issues}
    assert {"revenue_vs_budget", "roi_unsupported"} <= checks
    rejected = [x for i in issues if i["severity"] == "error" for x in i["values"]]
    assert 3_790_000_000 in rejected and 604000 in rejected


def test_country_with_arabic_prefixes():
    assert quality.country_of("الإطلاق بالسعودية وفي السوق السعودي") == "SAU"


def test_known_rejected_comes_from_the_previous_final_check():
    from app.main import known_rejected
    prev = {"verdict": {"arithmetic": [{"expr": "a", "stated": 112800, "correct": 106032}],
                        "plausibility": [{"severity": "error", "values": [604000]}, {"severity": "warning", "values": [7]}]}}
    assert known_rejected(prev) == {"values": [604000.0], "totals": [112800.0]}


def test_wrong_total_rejected_only_where_stated_as_total():
    text = "| توزيع | 20,000 |\n**المجموع**: 20,000 ريال\nإيجار 20,000 ريال"
    tags = [it["tag"] for it in quality.prior_ledger(text, "", [], rejected_totals=[20000])]
    assert tags == ["uncertain", "rejected", "uncertain"]


def test_follow_up_reaudits_previous_tables_and_drops_wrong_memory():
    llm = FollowLLM()
    run = Run(goal="أعد الخطة بميزانية 120 ألف ريال", llm_public={})
    prev = "| بند | ريال |\n|---|---|\n| أ | 50,000 |\n| ب | 62,800 |\n\n**المجموع**: 120,000 ريال"
    run.context = "سياق المحادثة السابقة:\nالطلب السابق: خطة\n\nالنتيجة السابقة:\n" + prev
    run.prior_check = {"from": "abc", "known_rejected": [], "known_rejected_totals": [112800.0]}
    run.memory_space = "s"
    run.memory["recalled"] = []

    async def go():
        o = Orchestrator(run, llm)

        async def recall():
            run.memory["recalled"] = [{"kind": "fact", "nature": "task_result", "content": "مجموع الميزانية 112,800 ريال"},
                                      {"kind": "fact", "nature": "user_stated", "content": "المنتج قهوة باردة"}]
            return []
        o.recall = recall

        async def remember(existing):
            pass
        o.remember = remember
        await o.execute()
    asyncio.run(go())
    assert 120000 in run.prior_check["rejected_totals"]  # 50,000 + 62,800 = 112,800, not 120,000
    assert [i["content"] for i in run.memory["recalled"]] == ["المنتج قهوة باردة"]
    assert any(e["type"] == "memory_excluded" for e in run.events)


def test_export_shows_research_measurements_and_inheritance():
    from app.export import to_markdown
    md = to_markdown({"id": "x", "goal": "g", "status": "partial", "result": "r",
                      "verdict": {"plausibility": [{"severity": "error", "reason": "طلب غير معقول"}]},
                      "research": {"search_calls": 5, "search_ok": 3, "results": 18, "providers": {"DuckDuckGo": 3},
                                   "pages_read": 4, "sources_in_answer": 2, "numeric_lines": 9, "numeric_lines_untagged": 1},
                      "prior_check": {"issues": [], "ledger": [{"line": "l", "tag": "rejected"}]}})
    assert "صفحات قُرئت فعلًا: 4" in md and "طلب غير معقول" in md and "مرفوض — ثبت خطؤه" in md


def test_reuses_rejected_flags_carried_forward_figure():
    from app import quality
    text = "الإيرادات = 3,790,000,000 × 18\nالطلب 3.79 مليار كوب (مرفوض، لا يُستخدم)\nالميزانية 90,000 ريال\nالمجموع 112,800 ريال\nبند التسويق 112,800 ريال"
    lines = quality.reuses_rejected(text, [3.79e9, 604000.0], [112800.0])
    assert any("3,790,000,000" in l for l in lines)
    assert not any("مرفوض" in l for l in lines)
    assert any(l.startswith("المجموع") for l in lines)
    assert not any("التسويق" in l for l in lines)  # same figure, not stated as a total
    assert "الميزانية 90,000 ريال" not in lines


def test_apply_rejected_fails_verdict_and_keeps_clean():
    from app import quality
    ok = {"pass": True, "score": 9, "issues": []}
    assert quality.apply_rejected(ok, []) == ok
    v = quality.apply_rejected(ok, ["revenue = 3790000000 * 18"])
    assert v["pass"] is False and v["score"] <= 4 and v["reused_rejected"]


def test_small_rejected_values_ignored():
    from app import quality
    assert quality.reuses_rejected("السعر 18 ريال", [18.0]) == []


def test_mark_rejected_labels_prior_lines():
    from types import SimpleNamespace
    from app.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)
    o.run = SimpleNamespace(prior_check={"rejected": [3.79e9], "rejected_totals": []})
    out = o.mark_rejected("الطلب السنوي 3.79 مليار كوب\nالسعر 18 ريال")
    assert out.splitlines()[0].startswith("[مرفوض")
    assert out.splitlines()[1] == "السعر 18 ريال"


def test_fit_messages_respects_budget_and_keeps_ends():
    from app.llm import fit_messages
    msgs = [{"role": "system", "content": "S" * 5000}, {"role": "user", "content": "TASK " + "u" * 6000}]
    msgs += [{"role": "assistant" if i % 2 else "user", "content": f"turn{i} " + "x" * 4000} for i in range(8)]
    msgs.append({"role": "user", "content": "LATEST " + "y" * 3000})
    out = fit_messages(msgs, 15000)
    assert sum(len(m["content"]) for m in out) <= 15000
    assert out[0]["role"] == "system" and out[-1]["content"].startswith("LATEST")
    assert out[1]["content"].startswith("TASK")
    small = [{"role": "user", "content": "hi"}]
    assert fit_messages(small, 15000) == small


def test_fit_messages_single_huge_message():
    from app.llm import fit_messages
    out = fit_messages([{"role": "system", "content": "a" * 10000}, {"role": "user", "content": "b" * 30000}], 15000)
    assert sum(len(m["content"]) for m in out) <= 15000


def test_free_chat_sends_within_limit_and_shrinks_on_413(monkeypatch):
    import asyncio, httpx
    from app.llm import LLM, LLMConfig
    sizes = []

    async def post(url, headers=None, json=None):
        n = sum(len(m["content"]) for m in json["messages"])
        sizes.append(n)
        req = httpx.Request("POST", url)
        if len(sizes) == 1:
            return httpx.Response(413, text='{"error":"PAYLOAD_TOO_LARGE"}', request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=req)

    llm = LLM(LLMConfig(provider="free", base_url="https://x/openai", model="m"))
    monkeypatch.setattr(llm._client, "post", post)
    out = asyncio.run(llm.chat([{"role": "system", "content": "s" * 9000}, {"role": "user", "content": "u" * 30000}]))
    assert out == "ok" and sizes[0] <= 15000 and sizes[1] < sizes[0]


def test_planner_retries_when_reply_has_no_tasks():
    import asyncio
    from app.orchestrator import plan
    from app.llm import Usage

    class L:
        usage = Usage()
        def __init__(self):
            self.n = 0
        async def chat_json(self, msgs, temperature=0.2, max_tokens=2000):
            self.n += 1
            if self.n == 1:
                return {"summary": "x"}
            return {"plan": {"summary": "s", "tasks": [{"id": "t1", "title": "بحث", "role": "researcher"}]}}
    llm = L()
    out = asyncio.run(plan(llm, "هدف", 0))
    assert llm.n == 2 and out["tasks"][0]["id"] == "t1"


def test_calculate_joins_multiline_brackets():
    import asyncio
    from app.tools import calculate
    out = asyncio.run(calculate("shares = [\n0.12,\n0.25\n]\ntotal = sum(shares) * 90000"))
    assert "خطأ" not in out and "33,300" in out


def test_critique_of_rejected_figure_is_not_reuse():
    from app import quality
    assert quality.reuses_rejected("الإيرادات السابقة (68 220 000 000 ريال) لا توجد لها أي مصدر موثق.", [68.22e9]) == []
    assert quality.reuses_rejected("حجم الطلب 3.79 مليار كوب (من المستخدم)", [3.79e9])


def test_disputed_but_used_is_still_reuse():
    from app import quality
    assert quality.reuses_rejected("الإيرادات السنوية (المستخدمة في الحساب) = 68 220 000 000 ريال (من طلب سابق غير موثق)", [68.22e9])


def test_looks_truncated():
    from app.llm import looks_truncated
    assert looks_truncated("| العنصر | القيمة |\n|---|---|\n| **")
    assert looks_truncated("نص ```code")
    assert not looks_truncated("## خلاصة\nالجواب مكتمل.")
    assert not looks_truncated("| أ | ب |\n|---|---|\n| 1 | 2 |")


def test_chat_long_continues_on_length(monkeypatch):
    import asyncio
    from app.llm import LLM, LLMConfig
    llm = LLM(LLMConfig(provider="free", base_url="https://x/openai", model="m"))
    replies = [("الجزء الأول | **", "length"), ("بقية الجواب.", "stop")]

    async def chat(messages, temperature=0.3, max_tokens=2000):
        t, f = replies.pop(0)
        llm.last_finish = f
        return t
    monkeypatch.setattr(llm, "chat", chat)
    out = asyncio.run(llm.chat_long([{"role": "user", "content": "x"}]))
    assert out.startswith("الجزء الأول") and out.endswith("بقية الجواب.") and not replies


def test_false_user_tags():
    from app import quality
    ans = "| التحضير | 12 % | 10 800 | (من المستخدم) |\n| الميزانية | 90 000 | (من المستخدم) |\n| المجموع | 90 000 | (محسوب) |"
    bad = quality.false_user_tags(ans, "أعد الخطة بعد تغيير الميزانية إلى 90 ألف ريال")
    assert len(bad) == 1 and "التحضير" in bad[0]
