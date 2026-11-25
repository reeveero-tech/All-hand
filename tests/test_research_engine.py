"""AGOS Research Engine batch 1: provider registry, breaker, merging, extraction, evidence ledger, connectors."""
import asyncio
import json

import httpx
import pytest

from app import agents, tools
from app.research import evidence, extract, registry, specialized


def run(c):
    return asyncio.run(c)


# --- registry -------------------------------------------------------------------------------------------------

def _prov(name, fn):
    registry.REGISTRY.pop(name, None)
    return registry.register(name, "web", fn)


def test_breaker_opens_after_consecutive_errors_then_half_opens(monkeypatch):
    calls = []

    async def boom(q):
        calls.append(q)
        raise httpx.ConnectError("down")
    p = _prov("T-boom", boom)
    for _ in range(registry.FAILS_TO_OPEN):
        assert run(registry.call(p, "x")) == []
    assert p.state() == "open" and p.stats["errors"] == 3 and "ConnectError" in p.last_error
    with pytest.raises(registry.Skipped):
        run(registry.call(p, "x"))
    assert len(calls) == 3 and p.stats["skipped_open"] == 1  # an open breaker sends no request
    p.open_until = 1.0  # cooldown passed: one trial request (half-open); a failure reopens at once
    assert p.state() == "half_open"
    run(registry.call(p, "x"))
    assert p.state() == "open" and len(calls) == 4
    registry.REGISTRY.pop("T-boom")


def test_empty_answer_is_not_a_failure_and_success_closes():
    async def empty(q):
        return []
    p = _prov("T-empty", empty)
    for _ in range(5):
        run(registry.call(p, "x"))
    assert p.state() == "closed" and p.stats["empty"] == 5 and p.stats["errors"] == 0
    registry.REGISTRY.pop("T-empty")


def test_results_without_http_url_are_dropped_and_tagged():
    async def mixed(q):
        return [{"title": "a", "url": "https://a.example/x"}, {"title": "b", "url": "javascript:alert(1)"}, "junk"]
    p = _prov("T-mixed", mixed)
    out = run(registry.call(p, "x"))
    assert [x["url"] for x in out] == ["https://a.example/x"] and out[0]["provider"] == "T-mixed"
    registry.REGISTRY.pop("T-mixed")


def test_timeout_counts_as_error():
    async def slow(q):
        await asyncio.sleep(1)
    p = _prov("T-slow", slow)
    assert run(registry.call(p, "x", timeout=0.05)) == [] and "TimeoutError" in p.last_error
    registry.REGISTRY.pop("T-slow")


def test_normalize_and_merge_dedupe_across_providers():
    assert registry.normalize_url("http://WWW.Example.com/a/?utm_source=x&b=2&a=1#top") == "https://example.com/a?a=1&b=2"
    a = [{"url": "https://www.x.sa/p?utm_campaign=1", "provider": "A"}, {"url": "https://y.sa/", "provider": "A"}]
    b = [{"url": "https://x.sa/p", "provider": "B"}, {"url": "https://z.sa", "provider": "B"}]
    m = registry.merge([a, b])
    assert [x["url"] for x in m] == ["https://www.x.sa/p?utm_campaign=1", "https://y.sa/", "https://z.sa"]
    assert m[0]["also_found_by"] == ["B"]


def test_fan_out_runs_providers_concurrently_and_isolates_failures():
    async def slow_ok(q):
        await asyncio.sleep(0.2)
        return [{"url": "https://ok.example"}]

    async def bad(q):
        await asyncio.sleep(0.2)
        raise RuntimeError("x")
    _prov("T-a", slow_ok), _prov("T-b", bad)

    async def go():
        t = asyncio.get_running_loop().time()
        got = await registry.fan_out(["T-a", "T-b", "missing"], "q")
        return got, asyncio.get_running_loop().time() - t
    got, took = run(go())
    assert got["T-a"][0]["url"] == "https://ok.example" and got["T-b"] == [] and "missing" not in got
    assert took < 0.35  # parallel, not 0.4 s sequential
    registry.REGISTRY.pop("T-a"), registry.REGISTRY.pop("T-b")


def test_web_search_skips_a_provider_whose_breaker_is_open(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    tav = []

    async def tavily(q):
        tav.append(q)
        raise httpx.ReadTimeout("slow")

    async def ddg(q):
        return [{"title": "t", "url": "https://shop.example/a", "snippet": "s"}]
    monkeypatch.setattr(tools, "_tavily", tavily)
    monkeypatch.setattr(tools, "_ddg", ddg)
    for i in range(5):
        out = run(tools.web_search(f"قهوة {i}"))
        assert out.startswith("المصدر: DuckDuckGo")
    assert len(tav) == registry.FAILS_TO_OPEN  # after three timeouts Tavily is no longer asked
    assert registry.REGISTRY["Tavily"].public()["state"] == "open"


def test_web_search_merges_a_configured_searxng(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def ddg(q):
        return [{"title": "a", "url": "https://a.sa/1", "snippet": "-"}]

    async def sx(q):
        return [{"title": "a", "url": "https://www.a.sa/1/", "snippet": "-"}, {"title": "b", "url": "https://b.sa/2", "snippet": "-"}]
    monkeypatch.setattr(tools, "_ddg", ddg)
    registry.register("SearXNG", "web", sx)
    try:
        out = run(tools.web_search("قهوة مختصة"))
    finally:
        registry.REGISTRY.pop("SearXNG")
    assert out.startswith("المصدر: DuckDuckGo + SearXNG")
    found = tools.found_links(out)
    assert [f["url"] for f in found] == ["https://a.sa/1", "https://b.sa/2"]
    assert found[0]["provider"] == "DuckDuckGo" and found[0]["also"] == ["SearXNG"] and found[1]["provider"] == "SearXNG"


def test_health_lists_providers_without_secrets(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-SECRET123")
    body = TestClient(app).get("/health").json()
    names = {p["name"] for p in body["providers"]}
    assert {"Tavily", "DuckDuckGo", "Google News", "Wikipedia", "Crossref", "arXiv", "GitHub"} <= names
    assert "SECRET123" not in json.dumps(body)


# --- extraction ------------------------------------------------------------------------------------------------

PAGE = """<html lang="ar"><head><title>أسعار القهوة</title>
<meta property="og:site_name" content="متجر المثال">
<meta property="article:published_time" content="2026-10-01T08:00:00+03:00">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Product","name":"قهوة باردة 250 مل","offers":{"@type":"Offer","price":"4.50","priceCurrency":"SAR",
  "availability":"https://schema.org/InStock"}},
 {"@type":"NewsArticle","dateModified":"2026-11-02","publisher":{"@type":"Organization","name":"Example News"}}]}</script>
<script>var x = "<table><tr><td>no</td></tr></table>";</script></head>
<body><table><thead><tr><th>المنتج</th><th>السعر (ريال)</th></tr></thead>
<tbody><tr><td>لاتيه</td><td>4.50</td></tr><tr><td>Cold brew | 330ml</td><td>7.25</td></tr></tbody></table>
<table><tr><td>layout only</td></tr></table></body></html>"""


def test_extract_dates_publisher_offers_and_tables():
    info = extract.extract(PAGE)
    assert info["lang"] == "ar" and info["title"] == "أسعار القهوة"
    assert info["publisher"] == "متجر المثال"  # og:site_name wins over JSON-LD publisher
    assert info["published"].startswith("2026-10-01") and info["modified"] == "2026-11-02"
    assert info["offers"] == [{"name": "قهوة باردة 250 مل", "price": "4.50", "currency": "SAR", "availability": "InStock"}]
    assert {"Product", "NewsArticle"} <= set(info["jsonld_types"])
    assert info["tables"] == [[["المنتج", "السعر (ريال)"], ["لاتيه", "4.50"], ["Cold brew | 330ml", "7.25"]]]
    md = extract.tables_markdown(info["tables"])
    assert "| لاتيه | 4.50 |" in md and "Cold brew / 330ml" in md


def test_extract_survives_broken_html_and_bad_jsonld():
    info = extract.extract('<html><script type="application/ld+json">{bad</script><table><tr><td>a<td>b<tr><td>1<td>2')
    assert info["offers"] == [] and isinstance(info["tables"], list)
    assert extract.extract("")["title"] == ""


def test_time_tag_is_a_date_fallback():
    assert extract.extract('<p><time datetime="2025-03-04">4 مارس</time></p>')["published"] == "2025-03-04"


# --- fetch_url adds page facts, keeps SSRF protection -----------------------------------------------------------

def test_fetch_url_adds_page_facts_and_tables(monkeypatch):
    monkeypatch.setattr("app.tools._resolve_public", lambda h: "93.184.216.34")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=PAGE)
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)
    monkeypatch.setattr(tools.httpx, "AsyncClient", client)
    out = run(tools.fetch_url("https://shop.example/coffee", 20000))
    assert out.startswith("HTTP 200 — https://shop.example/coffee")
    assert "الناشر: متجر المثال" in out and "جداول: 1" in out and "قهوة باردة 250 مل = 4.50 SAR" in out
    meta = tools.fetch_meta(out)
    assert meta["status"] == 200 and meta["failure"] is None and meta["publisher"] == "متجر المثال"
    assert meta["published"].startswith("2026-10-01") and meta["tables"] == "1" and len(meta["hash"]) == 16


def test_fetch_url_still_refuses_internal_addresses():
    out = run(tools.fetch_url("http://169.254.169.254/latest/meta-data/"))
    assert "ممنوعة" in out
    assert tools.fetch_meta(out, "http://169.254.169.254/")["failure"] == "unsafe_address"


@pytest.mark.parametrize("result,cls", [
    ("HTTP 200 — https://a\n\nنص", None), ("HTTP 403 — https://a\n\nForbidden", "blocked"),
    ("HTTP 429 — https://a\n\n", "rate_limited"), ("HTTP 404 — https://a\n\n", "not_found"),
    ("HTTP 503 — https://a\n\n", "server_error"), ("HTTP 200 — https://a\n\n[محتوى غير نصي: application/pdf]", "non_text"),
    ("خطأ في الجلب: ReadTimeout: ", "timeout"), ("خطأ في الجلب: ConnectError: x", "network_error"),
    ("خطأ: تحويلات كثيرة.", "too_many_redirects")])
def test_failure_classes(result, cls):
    assert evidence.classify_failure(result) == cls


# --- evidence ledger ------------------------------------------------------------------------------------------

def test_ledger_keeps_stages_apart_and_only_cites_what_was_read():
    events = [
        {"type": "tool_result", "tool": "web_search", "task": "t1",
         "found": [{"url": "https://a.sa/1", "provider": "Tavily", "also": ["SearXNG"], "title": "A"},
                   {"url": "https://news.google.com/rss/articles/X", "provider": "Google News", "headline_only": True},
                   {"url": "https://c.sa/3", "provider": "Tavily"}],
         "read": ["https://a.sa/1"]},
        {"type": "tool_result", "tool": "fetch_url", "task": "t2",
         "page": {"url": "https://c.sa/3", "status": 403, "failure": "blocked"}},
        {"type": "tool_result", "tool": "fetch_url", "task": "t2",
         "page": {"url": "https://d.sa/4", "status": 200, "failure": None, "published": "2026-01-02",
                  "publisher": "D", "tables": "2", "hash": "abc"}},
    ]
    answer = "السعر 4.5 ريال (https://a.sa/1). الخبر: https://news.google.com/rss/articles/X وأيضًا https://c.sa/3"
    led = evidence.build(events, answer)
    by = {r["url"]: r for r in led["records"]}
    assert by["https://a.sa/1"]["stage"] == "cited" and by["https://a.sa/1"]["found_by"] == ["Tavily", "SearXNG"]
    assert by["https://news.google.com/rss/articles/X"]["stage"] == "discovered"  # a headline is not a reading
    assert by["https://c.sa/3"]["stage"] == "opened" and by["https://c.sa/3"]["failure"] == "blocked"
    assert by["https://d.sa/4"]["stage"] == "relevant" and by["https://d.sa/4"]["tables"] == 2
    assert led["counts"] == {"discovered": 4, "opened": 3, "extracted": 2, "relevant": 2, "cited": 1}
    assert led["failures"] == {"blocked": 1} and led["headline_only"] == 1
    assert set(led["uncited_links_in_answer"]) == {"https://news.google.com/rss/articles/X", "https://c.sa/3"}
    assert led["independent_domains_read"] == 2


def test_agent_records_fetch_failures_and_search_discoveries(monkeypatch):
    async def fake_tool(name, args, allowed):
        if name == "web_search":
            return "المصدر: Tavily\n1. t\n   https://a.example/x\n   s"
        return "HTTP 403 — https://blocked.example/\n\nForbidden"
    steps = iter([{"action": "tool", "tool": "web_search", "input": {"query": "q"}},
                  {"action": "tool", "tool": "fetch_url", "input": {"url": "https://blocked.example/"}},
                  {"action": "final", "output": "done"}])

    class L:
        async def chat_json(self, *a, **k):
            return next(steps)
    events = []

    async def emit(t, d):
        events.append({"type": t, **d})
    monkeypatch.setattr(agents, "call_tool", fake_tool)
    res = run(agents.run_agent(L(), agents.ROLES["researcher"], "مهمة", "", emit, False))
    assert res["read"] == []  # a 403 is not a source
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[0]["found"][0]["url"] == "https://a.example/x"
    assert results[1]["page"] == {"url": "https://blocked.example/", "status": 403, "failure": "blocked"}


def test_evidence_endpoint_is_owner_only(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.orchestrator import Run
    r = Run(goal="g", llm_public={}, id="evid00000001")
    r.owner = main._owner("cli_" + "a" * 32)
    r.events = [{"type": "tool_result", "tool": "fetch_url", "page": {"url": "https://d.sa/4", "status": 200, "failure": None}}]
    r.result = "انظر https://d.sa/4"
    monkeypatch.setitem(main.RUNS, r.id, r)
    c = TestClient(main.app)
    body = c.get(f"/api/runs/{r.id}/evidence", headers={"X-Client-Key": "cli_" + "a" * 32}).json()
    assert body["counts"]["cited"] == 1
    assert c.get(f"/api/runs/{r.id}/evidence", headers={"X-Client-Key": "cli_" + "b" * 32}).status_code == 404


# --- specialized connectors (HTTP mocked; shapes taken from real responses on 2026-11-26) -------------------------

def _mock(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)
    monkeypatch.setattr(module.httpx, "AsyncClient", client)


def test_crossref_parses_doi_publisher_and_date(monkeypatch):
    body = {"message": {"items": [{"DOI": "10.3390/su16177367", "title": ["Unlocking Sustainable Economic Development "
            "in Saudi Arabia through the Coffee Industry"], "container-title": ["Sustainability"],
            "published": {"date-parts": [[2024, 8, 27]]}, "publisher": "MDPI AG",
            "URL": "https://doi.org/10.3390/su16177367", "abstract": "<jats:p>Conducting business</jats:p>"}]}}
    _mock(monkeypatch, specialized, lambda req: httpx.Response(200, json=body))
    out = run(specialized.crossref("saudi coffee"))
    assert out[0]["url"] == "https://doi.org/10.3390/su16177367" and out[0]["published"] == "2024-08-27"
    assert "MDPI AG" in out[0]["snippet"] and "<jats" not in out[0]["snippet"]


def test_arxiv_parses_atom_and_spaces_requests(monkeypatch):
    xml = ('<feed><entry><id>http://arxiv.org/abs/2206.01841v1</id><title>Coffee Roast Intelligence</title>'
           '<published>2022-06-03T22:23:29Z</published><summary>As the coffee industry has grown</summary>'
           '<link href="https://arxiv.org/abs/2206.01841v1" rel="alternate" type="text/html"/></entry></feed>')
    _mock(monkeypatch, specialized, lambda req: httpx.Response(200, text=xml))
    monkeypatch.setitem(specialized._arxiv_gate, "next", 0.0)
    monkeypatch.setitem(specialized._arxiv_gate, "lock", None)
    out = run(specialized.arxiv("coffee"))
    assert out == [{"title": "Coffee Roast Intelligence", "url": "https://arxiv.org/abs/2206.01841v1",
                    "snippet": "2022-06-03 — As the coffee industry has grown", "published": "2022-06-03",
                    "publisher": "arXiv", "source_type": "academic"}]
    assert specialized._arxiv_gate["next"] > 0  # the next request waits three seconds


def test_research_sources_tool_lists_without_reading(monkeypatch):
    async def cr(q):
        return [{"title": "Paper", "url": "https://doi.org/10.1/x", "snippet": "MDPI — 2024"}]

    async def ax(q):
        raise httpx.ConnectError("x")
    monkeypatch.setattr(registry.REGISTRY["Crossref"], "fn", cr)
    monkeypatch.setattr(registry.REGISTRY["arXiv"], "fn", ax)
    out = run(tools.research_sources("academic: coffee Saudi"))
    assert out.startswith("المصدر: Crossref") and "https://doi.org/10.1/x" in out and "لا نصوص قُرئت" in out
    meta = tools.search_meta(out)
    assert meta["read"] == [] and meta["found"][0]["provider"] == "Crossref"
    assert "خطأ" in run(tools.research_sources("nonsense"))
