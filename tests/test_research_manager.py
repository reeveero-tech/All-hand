"""Research manager, documents, crawler, ledger and quality gate — offline (a fake web, no network).

The fake web replaces tools.fetch_raw, the single network reader under read_url, fetch_url, robots.txt and the
crawler, so everything above it (sniffing, document workers, extraction, ranking, budget, ledger) runs for real.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app import tools
from app.research import access as acc
from app.research import browser, crawler, documents, evidence, manager, quality_gate, registry, specialized
from tests.docfixtures import docx_bytes, pdf_bytes, xlsx_bytes

run = asyncio.run
HTML = "text/html; charset=utf-8"
LONG = " ".join(["The ready to drink coffee category keeps growing in Saudi Arabia across retail and online channels."] * 4)


def page(title: str, body: str, extra: str = "") -> bytes:
    return (f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{body}</p>{extra}"
            f"<p>{LONG}</p></article></body></html>").encode()


class Web:
    """URL -> (status, body, ctype[, headers]). Counts every request. Unknown URLs are 404."""

    def __init__(self, pages: dict, delay: dict | None = None):
        self.pages, self.delay, self.hits = pages, delay or {}, {}

    async def __call__(self, url, timeout=20.0):
        self.hits[url] = self.hits.get(url, 0) + 1
        if url in self.delay:
            await asyncio.sleep(self.delay[url])
        if url.endswith("/robots.txt") and url not in self.pages:
            return {"ok": True, "error": None, "status": 404, "url": url, "ctype": "text/plain", "charset": None,
                    "body": b"", "truncated": False, "headers": {}}
        got = self.pages.get(url)
        if got == "timeout":
            return {"ok": False, "error": "خطأ في الجلب: ReadTimeout timed out", "status": None, "url": url, "ctype": "",
                    "charset": None, "body": b"", "truncated": False, "headers": {}}
        if got == "private":
            return {"ok": False, "error": "خطأ: العناوين الداخلية/الخاصة ممنوعة.", "status": None, "url": url,
                    "ctype": "", "charset": None, "body": b"", "truncated": False, "headers": {}}
        if got is None:
            got = (404, b"<html><body>Not found</body></html>", HTML)
        status, body, ctype = got[:3]
        headers = got[3] if len(got) > 3 else {}
        return {"ok": True, "error": None, "status": status, "url": url, "ctype": ctype.split(";")[0],
                "charset": "utf-8" if "html" in ctype or "text" in ctype else None, "body": body, "truncated": False,
                "headers": headers}


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(manager, "ENABLED", True)
    monkeypatch.setattr(acc, "DEFAULT_INTERVAL", 0.0)

    async def no_main(raw, charset="utf-8"):  # Trafilatura runs in its own process; one test covers it for real
        return None
    monkeypatch.setattr(documents, "main_text", no_main)
    return monkeypatch


def install(monkeypatch, web: Web, search: dict | None = None, index: dict | None = None, wb: str | None = None):
    monkeypatch.setattr(tools, "fetch_raw", web)

    async def fake_search(q):
        for key, res in (search or {}).items():
            if key in q:
                return res, "Tavily", ""
        return [], "", ""
    monkeypatch.setattr(tools, "search_results", fake_search)

    async def fake_fan(names, q, timeout=30):
        out = {}
        for n in names:
            out[n] = [dict(x) for x in (index or {}).get(n, []) if not x.get("_q") or x["_q"] in q]
        return out
    monkeypatch.setattr(registry, "fan_out", fake_fan)

    async def fake_wb(spec):
        if wb is None:
            return "لا بيانات للمؤشر."
        iso, ind = spec.split()
        return f"{tools.READ_MARK}https://data.worldbank.org/indicator/{ind}?locations=SA]\nالبنك الدولي — Population (Saudi Arabia):\n- 2024: 35,300,280\nالمصدر: x"
    monkeypatch.setattr(tools, "world_bank", fake_wb)


def mgr(goal="ادرس أسعار القهوة الباردة في السعودية", events=None, **budget):
    async def emit(t, d):
        if events is not None:
            events.append({"type": t, **d})
    b = manager.Budget(**{**{"max_searches": 20, "max_opens": 20, "max_seconds": 60, "max_rounds": 2, "concurrency": 3}, **budget})
    return manager.ResearchManager(goal, emit=emit, budget=b, today=date(2026, 11, 26))


# ------------------------------------------------------------------ facets
def test_facets_follow_the_goal_not_a_fixed_list():
    a = manager.choose_facets("ادرس فرصة إطلاق منتج جديد في السوق السعودي، وابحث عن المنافسين، وحلل الأسعار والطلب، واكتب خطة تنفيذ وتقريرًا ماليًا")
    assert {"competitors", "prices", "demand"} <= {k for k, v in a.items() if v == "critical"}
    assert {"regulation", "channels", "costs"} <= set(a)  # implied by launch / financial plan
    b = manager.choose_facets("ما أفضل مكتبة برمجيات مفتوحة المصدر لقراءة ملفات PDF؟")
    assert "technology" in b and "prices" not in b and "competitors" not in b
    c = manager.choose_facets("لخص الدراسات العلمية عن الصيام المتقطع")
    assert "literature" in c and "market_size" not in c
    assert manager.choose_facets("مرحبا") == {"overview": "critical"}


def test_subject_and_country_from_the_goal():
    g = "ادرس لي فرصة إطلاق منتج جديد في السوق السعودي. المنتج المستخدم في الاختبار: قهوة باردة معلبة، والميزانية 200 ألف ريال."
    assert manager.extract_subject(g) == "قهوة باردة معلبة"
    assert manager.detect_country(g)["iso"] == "SAU"


# ------------------------------------------------------------------ discovery + automatic opening
def test_crossref_results_are_opened_automatically_full_text_then_abstract(on):
    pdf = pdf_bytes(["Coffee consumption in Saudi Arabia rose 12% in 2024 to 3.2 kg per capita"],
                    [["Year", "kg per capita"], ["2023", "2.9"], ["2024", "3.2"]])
    web = Web({"https://publisher.example/full/paper1.pdf": (200, pdf, "application/pdf"),
               "https://doi.org/10.1/paper2": (403, b"<html>Forbidden</html>", HTML)})
    idx = {"Crossref": [
        {"title": "Coffee demand in KSA", "url": "https://doi.org/10.1/paper1", "snippet": "MDPI — 2024", "doi": "10.1/paper1",
         "published": "2024-05-01", "source_type": "academic", "fulltext": ["https://publisher.example/full/paper1.pdf"],
         "abstract": "We study coffee demand."},
        {"title": "Coffee consumers in Riyadh", "url": "https://doi.org/10.1/paper2", "snippet": "Elsevier — 2025", "doi": "10.1/paper2",
         "published": "2025-01-01", "source_type": "academic", "fulltext": [],
         "abstract": "A survey of 800 coffee consumers in Riyadh found that 41% drink ready to drink coffee weekly."}]}
    install(on, web, index=idx)
    on.setattr(specialized, "available", lambda kind: ["Crossref"] if kind == "academic" else [])
    ev = []
    m = mgr("لخص الدراسات العلمية عن استهلاك القهوة في السعودية", ev)
    st = run(m.run())
    reads = {r["url"]: r for r in st["readings"].values()}
    full = reads["https://publisher.example/full/paper1.pdf"]
    assert full["kind"] == "pdf" and full["completeness"] == "full" and full["tables"] and full["useful"]
    assert web.hits["https://publisher.example/full/paper1.pdf"] == 1
    summ = reads["https://doi.org/10.1/paper2"]
    assert summ["completeness"] == "summary" and "41%" in summ["excerpt"]  # the abstract, labelled as such
    assert st["failed"].get(registry.normalize_url("https://doi.org/10.1/paper2")) is None
    pack = m.pack("الدراسات")
    assert "ملخص فقط — ليس نص الدراسة" in pack and "[E" in pack
    led = evidence.build(ev, "")
    assert led["summary_only"] == 1


def test_arxiv_pdf_is_opened(on):
    pdf = pdf_bytes(["Neural roast profiling improves yield by 7% in 2025"])
    web = Web({"https://arxiv.org/pdf/2206.01841v1": (200, pdf, "application/pdf")})
    idx = {"arXiv": [{"title": "Coffee Roast Intelligence", "url": "https://arxiv.org/abs/2206.01841v1", "published": "2025-06-03",
                      "snippet": "2025", "source_type": "academic", "abstract": "x", "fulltext": ["https://arxiv.org/pdf/2206.01841v1"]}]}
    install(on, web, index=idx)
    on.setattr(specialized, "available", lambda kind: ["arXiv"] if kind == "academic" else [])
    st = run(mgr("لخص الدراسات العلمية عن تحميص القهوة").run())
    r = next(iter(st["readings"].values()))
    assert r["url"] == "https://arxiv.org/pdf/2206.01841v1" and r["method"].startswith("httpx+pypdf") and r["useful"]


def test_official_pages_rank_first_for_regulation_and_a_table_page_is_extracted(on):
    gov = page("اشتراطات المشروبات", "تشترط الهيئة العامة للغذاء والدواء تسجيل منتجات القهوة الباردة قبل البيع في السعودية.",
               "<table><tr><th>الرسوم</th><th>ريال</th></tr><tr><td>تسجيل منتج</td><td>2,000 ريال</td></tr></table>")
    blog = page("رأي في القهوة", "اشتراطات القهوة الباردة سهلة في السعودية كما يقول المدونون.")
    web = Web({"https://www.sfda.gov.sa/ar/regs": (200, gov, HTML), "https://blog.example/coffee": (200, blog, HTML)})
    res = [{"title": "رأي في اشتراطات القهوة الباردة", "url": "https://blog.example/coffee", "snippet": "اشتراطات"},
           {"title": "اشتراطات تسجيل منتجات القهوة الباردة", "url": "https://www.sfda.gov.sa/ar/regs", "snippet": "هيئة الغذاء"}]
    install(on, web, search={"": res})
    m = mgr("ما اشتراطات وتراخيص بيع القهوة الباردة في السعودية؟", budget=None) if False else mgr("ما اشتراطات وتراخيص بيع القهوة الباردة في السعودية؟")
    m.state["facets"] = {"regulation": "critical"}
    m.state["subject"] = {"ar": "القهوة الباردة", "en": ""}
    m.state["country"] = manager.detect_country("السعودية")
    m.state["queries"] = {"regulation": ["اشتراطات القهوة الباردة"]}
    run(m._search("regulation", "اشتراطات القهوة الباردة", ("web",)))
    picked = m.pick("regulation", 2)
    assert picked[0]["url"] == "https://www.sfda.gov.sa/ar/regs"  # official first, though the engine ranked it second
    rd = run(m.open_candidate(picked[0], "regulation"))
    assert rd["class"] == "official" and rd["tables"] and "2,000" in json.dumps(rd["tables"], ensure_ascii=False)


def test_commercial_page_offers_become_price_evidence(on):
    ld = json.dumps({"@context": "https://schema.org", "@type": "Product", "name": "Cold brew can 240ml",
                     "offers": {"@type": "Offer", "price": "8.50", "priceCurrency": "SAR"}})
    shop = page("قهوة باردة معلبة 240 مل", "سعر القهوة الباردة المعلبة 8.50 ريال للعبوة.",
                f'<script type="application/ld+json">{ld}</script>')
    web = Web({"https://www.noon.com/saudi-ar/cold-brew/p/1": (200, shop, HTML)})
    install(on, web, search={"سعر": [{"title": "قهوة باردة معلبة سعر", "url": "https://www.noon.com/saudi-ar/cold-brew/p/1",
                                       "snippet": "8.50 ريال"}]})
    st = run(mgr("ما سعر القهوة الباردة المعلبة في السعودية؟", max_rounds=1).run())
    r = next(r for r in st["readings"].values() if "noon" in r["url"])
    assert r["class"] == "commercial" and r["offers"][0]["price"] == "8.50" and r["useful"]
    assert any("8.50 ريال" in f for f in r["figures"])


# ------------------------------------------------------------------ documents through the reader
def _read(on, url, status, body, ctype):
    on.setattr(tools, "fetch_raw", Web({url: (status, body, ctype)}))
    return run(tools.read_url(url))


def test_pdf_table_extraction_through_the_reader(on):
    pdf = pdf_bytes(["Saudi RTD coffee market 2025: SAR 1.2 billion"], [["Brand", "Price SAR"], ["Starbucks", "9.5"], ["Barns", "7"]])
    rd = _read(on, "https://x.example/r.pdf", 200, pdf, "application/pdf")
    assert rd["failure"] is None and rd["kind"] == "pdf" and rd["completeness"] == "full"
    assert ["Starbucks", "9.5"] in rd["tables"][0] and "1.2 billion" in rd["text"]


def test_excel_and_word_are_read(on):
    rd = _read(on, "https://x.example/p.xlsx", 200, xlsx_bytes([["Brand", "Price SAR"], ["Barns", 7], ["Starbucks", 9.5]]),
               "application/octet-stream")
    assert rd["kind"] == "xlsx" and rd["tables"][0] == [["Brand", "Price SAR"], ["Barns", "7"], ["Starbucks", "9.5"]]
    rd = _read(on, "https://x.example/r.docx", 200, docx_bytes(["The market grew 12% in 2025."], [["Year", "Growth"], ["2025", "12%"]]),
               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert rd["kind"] == "docx" and "12%" in rd["text"] and rd["tables"][0][1] == ["2025", "12%"]


def test_corrupt_and_scanned_documents_are_failures_not_readings(on):
    rd = _read(on, "https://x.example/bad.pdf", 200, b"%PDF-1.4\n" + b"\x00garbage" * 50, "application/pdf")
    assert rd["failure"] == "unreadable_document" and not rd["text"]
    rd = _read(on, "https://x.example/scan.pdf", 200, pdf_bytes([], image_only=True), "application/pdf")
    assert rd["failure"] == "needs_ocr" and rd["ocr_pages"] == [1]
    txt = run(tools.fetch_url("https://x.example/scan.pdf"))
    assert "[يحتاج OCR" in txt and evidence.classify_failure(txt) == "needs_ocr"


def test_trafilatura_main_text_for_html(monkeypatch):
    if not documents.available().get("html"):
        pytest.skip("trafilatura not installed")
    nav = "<nav>" + " ".join(f"<a href='/c{i}'>Category {i}</a>" for i in range(40)) + "</nav>"
    body = f"<html><body>{nav}<article><h1>Coffee</h1><p>{LONG}</p><p>Prices rose 9% in 2025.</p></article></body></html>".encode()
    monkeypatch.setattr(tools, "fetch_raw", Web({"https://n.example/a": (200, body, HTML)}))
    rd = run(tools.read_url("https://n.example/a"))
    assert rd["method"] == "httpx+trafilatura" and "Category 7" not in rd["text"] and "9%" in rd["text"]
    assert "https://n.example/c3" in rd["links"]


# ------------------------------------------------------------------ failures, budget, duplicates, resume
@pytest.mark.parametrize("pages,url,failure", [
    ({"https://a.example/x": (403, b"<html>no</html>", HTML)}, "https://a.example/x", "blocked"),
    ({}, "https://a.example/missing", "not_found"),
    ({"https://a.example/x": (429, b"slow down", "text/plain", {"retry-after": "120"})}, "https://a.example/x", "rate_limited"),
    ({"https://a.example/x": "timeout"}, "https://a.example/x", "timeout"),
    ({"https://a.example/x": "private"}, "https://a.example/x", "unsafe_address"),
])
def test_http_failures_are_recorded_with_their_reason(on, pages, url, failure):
    web = Web(pages)
    install(on, web)
    ev = []
    m = mgr(events=ev)
    rd = run(m.open_url(url, "prices"))
    assert rd["failure"] == failure
    opened = [e for e in ev if e["type"] == "evidence" and e["stage"] == "opened"]
    assert opened and opened[0]["failure"] == failure
    rec = evidence.build(ev, "")["records"][0]
    assert rec["stage"] in ("discovered", "opened") and rec["failure"] == failure
    if failure == "rate_limited":  # the host is held for its Retry-After; the next request is refused locally
        assert run(m.policy.check("https://a.example/other")) == "host_on_hold"


def test_403_twice_stops_the_host_and_another_source_is_used(on):
    web = Web({"https://shop.example/a": (403, b"x", HTML), "https://shop.example/b": (403, b"x", HTML),
               "https://other.example/p": (200, page("سعر القهوة الباردة", "سعر القهوة الباردة 9 ريال في السعودية"), HTML)})
    install(on, web)
    m = mgr()
    run(m.open_url("https://shop.example/a", "prices"))
    run(m.open_url("https://shop.example/b", "prices"))
    assert not m.policy.allowed_host("https://shop.example/c")
    assert run(m.open_url("https://shop.example/c", "prices"))["failure"] == "host_refused_access"
    assert "https://shop.example/c" not in web.hits  # not asked again
    assert run(m.open_url("https://other.example/p", "prices"))["useful"]


def test_a_url_is_opened_once_even_when_asked_concurrently(on):
    web = Web({"https://a.example/p": (200, page("سعر", "سعر القهوة الباردة 9 ريال"), HTML)}, delay={"https://a.example/p": 0.05})
    install(on, web)
    m = mgr()

    async def many():
        return await asyncio.gather(*[m.open_url("https://a.example/p", "prices") for _ in range(5)],
                                    m.agent_tool("fetch_url", {"url": "https://a.example/p/"}, {"fetch_url"}, "t1"))
    got = run(many())
    assert web.hits["https://a.example/p"] == 1 and len({g["id"] for g in got[:5]}) == 1
    text, read = got[5]
    assert text.startswith("HTTP 200 — https://a.example/p") and read == ["https://a.example/p"]


def test_one_source_raising_does_not_stop_research(on):
    good = page("سعر القهوة الباردة", "سعر القهوة الباردة 9 ريال في السعودية")
    web = Web({"https://good.example/p": (200, good, HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة", "url": "https://boom.example/p", "snippet": "سعر"},
                                  {"title": "سعر القهوة الباردة 9 ريال", "url": "https://good.example/p", "snippet": "سعر"}]})
    real = tools.read_url

    async def flaky(url, *a, **k):
        if "boom" in url:
            raise RuntimeError("parser exploded")
        return await real(url, *a, **k)
    on.setattr(tools, "read_url", flaky)
    st = run(mgr(max_rounds=1).run())
    assert st["done"] and any("good.example" in r["url"] for r in st["readings"].values())
    assert any(f["failure"] == "network_error" for f in st["failed"].values())


def test_budget_limits_opens_searches_and_time(on):
    res = [{"title": f"سعر القهوة الباردة {i}", "url": f"https://s{i}.example/p", "snippet": "سعر 9 ريال"} for i in range(10)]
    web = Web({f"https://s{i}.example/p": (200, page("سعر", f"سعر القهوة الباردة {i} ريال"), HTML) for i in range(10)})
    install(on, web, search={"": res})
    m = mgr(max_opens=3, max_searches=2, per_facet_opens=10)
    st = run(m.run())
    assert st["used"]["opens"] <= 3 and st["used"]["searches"] <= 2
    assert sum(1 for u in web.hits if not u.endswith("robots.txt")) <= 3
    t = [0.0]
    m2 = manager.ResearchManager("أسعار القهوة الباردة", budget=manager.Budget(max_seconds=5), clock=lambda: t[0])
    t[0] = 10.0
    assert m2.out_of_time()
    st2 = run(m2.run())
    assert st2["rounds"] == 0 and st2["used"]["searches"] == 0


def test_open_timeout_is_enforced(on):
    web = Web({"https://slow.example/p": (200, page("x", "y"), HTML)}, delay={"https://slow.example/p": 2})
    install(on, web)
    m = mgr()
    m.budget.open_timeout = 0.2
    assert run(m.open_url("https://slow.example/p", "prices"))["failure"] == "timeout"


def test_resume_after_interruption_does_not_reopen(on):
    web = Web({"https://a.example/p": (200, page("سعر القهوة الباردة", "سعر القهوة الباردة 9 ريال في السعودية"), HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة", "url": "https://a.example/p", "snippet": "9 ريال"}]})
    m = mgr(max_rounds=1)
    run(m.run())
    saved = json.loads(json.dumps(m.to_dict()))  # what the store keeps
    before = web.hits["https://a.example/p"]
    m2 = manager.ResearchManager(m.goal, budget=manager.Budget(max_rounds=2), state=saved)
    assert "https://a.example/p" in m2.read_urls()
    run(m2.open_url("https://a.example/p", "prices"))
    run(m2.run())
    assert web.hits["https://a.example/p"] == before


def test_follow_up_reuses_previous_evidence(on):
    web = Web({})
    install(on, web)
    prev = {"readings": {"https://a.example/p": {"id": "E1", "url": "https://a.example/p", "facets": ["prices"], "useful": True,
                                                  "domain": "a.example", "figures": ["9 ريال"], "class": "commercial"}},
            "subject": {"ar": "قهوة باردة", "en": ""}}
    m = manager.ResearchManager("غيّر الميزانية إلى 120 ألف ريال", reuse=prev, budget=manager.Budget(max_rounds=1, max_searches=0))
    r = m.state["readings"]["https://a.example/p"]
    assert r["from_previous"] and m.state["reused_from"] == {"readings": 1}
    assert "من الدراسة السابقة" in m.pack("الأسعار")


# ------------------------------------------------------------------ ledger and citations
def test_every_stage_is_logged_and_only_read_sources_are_cited(on):
    good = page("سعر القهوة الباردة", "سعر القهوة الباردة 9 ريال في السعودية")
    web = Web({"https://good.example/p": (200, good, HTML), "https://dead.example/p": (404, b"x", HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة 9 ريال", "url": "https://good.example/p", "snippet": "سعر"},
                                  {"title": "سعر القهوة الباردة", "url": "https://dead.example/p", "snippet": "سعر"},
                                  {"title": "خبر", "url": "https://news.google.com/rss/articles/abc", "snippet": "x", "headline_only": True}]})
    ev = []
    m = mgr(events=ev, max_rounds=1)
    run(m.run())
    answer = ("السعر 9 ريال (المصدر: https://good.example/p) وحسب https://dead.example/p و https://news.google.com/rss/articles/abc "
              "و https://invented.example/x")
    led = evidence.build(ev, answer)
    c = led["counts"]
    assert c["discovered"] >= 3 and c["opened"] >= 2 and c["extracted"] >= 1 and c["relevant"] >= 1 and c["cited"] == 1
    st = {r["url"]: r["stage"] for r in led["records"]}
    assert st["https://good.example/p"] == "cited"
    assert st["https://dead.example/p"] == "opened" and st["https://news.google.com/rss/articles/abc"] == "discovered"
    assert set(led["uncited_links_in_answer"]) >= {"https://dead.example/p", "https://invented.example/x"}
    marked, unread = manager.annotate_unread(answer, quality_gate.read_set(m.state))
    assert "https://good.example/p) " in marked and "https://invented.example/x [رابط لم يقرأه النظام]" in marked
    assert len(unread) == 3


def test_a_link_an_agent_merely_mentions_is_never_promoted():
    events = [{"type": "tool_result", "tool": "web_search", "found": [{"url": "https://x.example/a", "title": "t"}], "read": []},
              {"type": "tool_result", "tool": "fetch_url", "page": {"url": "https://x.example/b", "status": 200, "failure": "empty_content"}}]
    led = evidence.build(events, "انظر https://x.example/a و https://x.example/b")
    st = {r["url"]: r["stage"] for r in led["records"]}
    assert st == {"https://x.example/a": "discovered", "https://x.example/b": "opened"}
    assert led["counts"]["cited"] == 0
    assert evidence.classify_failure("HTTP 200 — https://x.example/b\n[الصفحة: جداول: 0 | بصمة: ab]\n\n   ") == "empty_content"


# ------------------------------------------------------------------ quality gate
def _state(useful_n, facets=None, figures=True, official=False, published="2026-01-01"):
    readings = {}
    for i in range(useful_n):
        readings[f"https://d{i}.example/p"] = {"id": f"E{i + 1}", "url": f"https://d{i}.example/p", "domain": f"d{i}.example",
                                               "facets": ["prices", "competitors"], "useful": True, "figures": ["9 ريال"] if figures else [],
                                               "class": "official" if official and i == 0 else "commercial", "published": published,
                                               "completeness": "full"}
    st = {"facets": facets or {"prices": "critical", "competitors": "critical"}, "country": None, "readings": readings, "conflicts": []}
    m = manager.ResearchManager.__new__(manager.ResearchManager)
    m.state = st
    st["status"] = {f: m.facet_status(f) for f in st["facets"]}
    return st


def test_gate_thresholds_depend_on_the_request():
    p1 = quality_gate.profile({"facets": {"prices": "critical"}})
    p2 = quality_gate.profile({"facets": {k: "critical" for k in ("prices", "demand", "competitors", "market_size", "costs", "channels")}})
    assert p1["min_readable"] == 3 and p2["min_readable"] == 9 and p2["min_domains"] > p1["min_domains"]


def test_gate_says_partial_with_gaps_when_evidence_is_missing():
    g = quality_gate.evaluate(_state(1), {}, "السعر 9 ريال (المصدر: https://d0.example/p)")
    assert g["verdict"] in ("partial", "insufficient") and g["gaps"]
    g = quality_gate.evaluate(_state(4), {}, "السعر 9 ريال (المصدر: https://d0.example/p)", today=date(2026, 11, 26))
    assert g["verdict"] == "sufficient", g["gaps"]
    g = quality_gate.evaluate(_state(4), {"uncited_links_in_answer": ["https://x"]}, "السعر 9 ريال بلا مصدر\nالتكلفة 5 ريال", today=date(2026, 11, 26))
    assert g["verdict"] == "partial" and not g["checks"]["claims_with_sources"]["ok"] and not g["checks"]["no_unread_links"]["ok"]
    g = quality_gate.evaluate(_state(4, figures=False), {}, "لا أرقام", today=date(2026, 11, 26))
    assert not g["checks"]["numeric:prices"]["ok"] and g["verdict"] == "partial"
    g = quality_gate.evaluate(_state(4, published="2018-01-01"), {}, "x", today=date(2026, 11, 26))
    assert not g["checks"]["freshness"]["ok"]


def test_no_prices_found_means_no_numbers_and_a_listed_gap(on):
    install(on, Web({}))
    m = mgr("حلل أسعار القهوة الباردة في السعودية", max_rounds=2)
    st = run(m.run())
    assert st["status"]["prices"]["status"] == "no_results" and not st["readings"]
    pack = m.pack("الأسعار")
    assert "لا تخمّنه" in pack and "الأسعار: لم يجد البحث نتائج" in pack
    g = quality_gate.evaluate(st, {}, "لا أسعار موثقة")
    assert g["verdict"] == "insufficient"


def test_conflicting_market_sizes_are_flagged(on):
    a = page("حجم سوق القهوة", "حجم سوق القهوة الباردة في السعودية 1.2 مليار ريال في 2025")
    b = page("Saudi coffee market size", "The Saudi ready to drink coffee market size reached 900 million USD in 2025")
    web = Web({"https://a.example/m": (200, a, HTML), "https://b.example/m": (200, b, HTML)})
    install(on, web)
    m = mgr("ما حجم سوق القهوة الباردة في السعودية؟")
    run(m.open_url("https://a.example/m", "market_size"))
    run(m.open_url("https://b.example/m", "market_size"))
    m.state["facets"] = {"market_size": "critical"}
    m._detect_conflicts()
    assert m.state["conflicts"] and m.state["conflicts"][0]["ratio"] > 2  # 1.2bn SAR vs 3.375bn SAR


# ------------------------------------------------------------------ crawler
def test_crawler_sitemap_depth_no_revisit_robots_and_resume(on):
    robots = b"User-agent: *\nDisallow: /private\nSitemap: https://site.example/sitemap.xml\n"
    sm = (b"<urlset><url><loc>https://site.example/a</loc></url><url><loc>https://site.example/private/x</loc></url>"
          b"<url><loc>https://elsewhere.example/z</loc></url></urlset>")
    a = b"<html><body><a href='/b'>b</a><a href='/b#top'>b</a><a href='/private/y'>p</a><a href='https://elsewhere.example/'>o</a>" + LONG.encode() + b"</body></html>"
    bpage = b"<html><body><a href='/a'>a</a><a href='/c'>c</a>" + LONG.encode() + b"</body></html>"
    web = Web({"https://site.example/robots.txt": (200, robots, "text/plain"), "https://site.example/sitemap.xml": (200, sm, "application/xml"),
               "https://site.example/a": (200, a, HTML), "https://site.example/b": (200, bpage, HTML),
               "https://site.example/c": (200, bpage, HTML)})
    on.setattr(tools, "fetch_raw", web)
    pol = acc.Access(fetch=web, min_interval=0)
    cr = crawler.Crawler(tools.read_url, web, policy=pol, max_pages=2, max_depth=2, rpm=100)
    urls = run(cr.sitemap_urls("https://site.example/"))
    assert urls == ["https://site.example/a", "https://site.example/private/x"]
    st = crawler.CrawlState(seeds=["https://site.example/a"])
    st = run(cr.crawl(st))
    assert [p["url"] for p in st.pages] == ["https://site.example/a", "https://site.example/b"]
    assert not st.done and st.frontier  # stopped at the page cap with work left
    saved = json.loads(json.dumps(st.to_dict()))
    cr2 = crawler.Crawler(tools.read_url, web, policy=pol, max_pages=5, max_depth=2, rpm=100)
    st2 = run(cr2.crawl(crawler.CrawlState.from_dict(saved)))
    assert [p["url"] for p in st2.pages][-1] == "https://site.example/c"
    assert any(x["reason"] == "robots_disallowed" and "/private/" in x["url"] for x in st2.refused)
    assert web.hits["https://site.example/a"] == 1 and web.hits["https://site.example/b"] == 1  # never revisited
    assert "https://elsewhere.example/" not in web.hits


def test_crawler_rate_limit_and_access_walls(on):
    login = b"<html><body><form><input type='password' name='p'></form></body></html>"
    web = Web({"https://w.example/1": (200, login, HTML)})
    on.setattr(tools, "fetch_raw", web)
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    t = [0.0]
    pol = acc.Access(fetch=web, min_interval=0, clock=lambda: t[0], sleep=fake_sleep)
    cr = crawler.Crawler(tools.read_url, web, policy=pol, rpm=1, clock=lambda: t[0], sleep=fake_sleep, max_pages=3)
    st = run(cr.crawl(crawler.CrawlState(seeds=["https://w.example/1", "https://w.example/2"])))
    assert st.pages[0]["failure"] == "access_wall"
    assert not pol.allowed_host("https://w.example/2")  # a login page stops the host; nothing tries to pass it
    assert any(x["reason"] == "host_refused_access" for x in st.refused)
    assert crawler.parse_feed("<rss><channel><item><title>T</title><link>https://n.example/1</link><pubDate>Mon, 01 Sep 2025</pubDate></item></channel></rss>")[0]["url"] == "https://n.example/1"


def test_crawl_delay_too_long_is_respected_not_ignored(on):
    web = Web({"https://slow.example/robots.txt": (200, b"User-agent: *\nCrawl-delay: 60\n", "text/plain")})
    pol = acc.Access(fetch=web)
    assert run(pol.check("https://slow.example/x", crawl=True)) == "crawl_delay_too_long"


# ------------------------------------------------------------------ browser worker interface
def test_browser_worker_is_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("BROWSER_WORKER_URL", raising=False)
    assert not browser.status()["enabled"]
    r = run(browser.BrowserClient(url="", secret="").render(browser.BrowserRequest("https://x.example")))
    assert r.failure == "worker_unavailable"


def test_browser_worker_contract_with_mock(monkeypatch):
    monkeypatch.setattr(tools, "_resolve_public", lambda h: None if h == "internal.example" else "93.184.216.34")
    secret = "test-secret-not-real"
    pages = {"https://js.example/p": {"text": "Rendered price 9 SAR token=abcd1234efgh"},
             "https://login.example/p": {"gate": "login"},
             "https://proj.example/p": {"scope": "proj-a", "text": "ok"}}
    cl = browser.BrowserClient("https://worker.example", secret, transport=browser.mock_worker(pages, secret))
    r = run(cl.render(browser.BrowserRequest("https://js.example/p")))
    assert r.ok and "9 SAR" in r.text and "abcd1234efgh" not in r.text  # secrets redacted
    r = run(cl.render(browser.BrowserRequest("https://login.example/p")))
    assert not r.ok and r.needs_human == "login" and r.failure == "login_required"
    r = run(cl.render(browser.BrowserRequest("https://login.example/p", use_authorized_session=True)))
    assert r.failure == "needs_human_approval"  # no session without the owner's own login and approval
    r = run(cl.render(browser.BrowserRequest("https://proj.example/p", session_scope="proj-b", use_authorized_session=True, approval_id="ap1")))
    assert r.failure == "session_scope_mismatch"
    r = run(cl.render(browser.BrowserRequest("http://internal.example/")))
    assert r.failure == "unsafe_address"
    bad = browser.BrowserClient("https://worker.example", "wrong", transport=browser.mock_worker(pages, secret))
    assert run(bad.render(browser.BrowserRequest("https://js.example/p"))).failure == "worker_http_401"
    assert not browser.verify_signature(secret, b"{}", 1000, browser.sign(secret, b"{}", 1000), now=5000)  # replay refused


# ------------------------------------------------------------------ orchestrator integration
def test_orchestrator_runs_research_before_agents_and_gates_the_result(on):
    from app import orchestrator as orch
    from app.orchestrator import Orchestrator, Run

    good = page("سعر القهوة الباردة المعلبة", "سعر القهوة الباردة المعلبة 9 ريال في السعودية لدى المتاجر")
    web = Web({"https://shop.example/p": (200, good, HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة المعلبة 9 ريال", "url": "https://shop.example/p", "snippet": "9 ريال"}]})
    seen = {}

    class LLM:
        cfg = type("C", (), {"provider": "test"})()
        usage = type("U", (), {"as_dict": lambda self: {}})()

        async def chat_json(self, messages, *a, **k):
            sys_ = messages[0]["content"]
            if "مخطط بحث" in sys_:
                return {"subject_en": "canned cold brew coffee", "queries": {}}
            if "أنت وكيل متخصص" in sys_:
                seen.setdefault("ctx", messages[1]["content"])
                return {"action": "final", "output": "السعر 9 ريال (المصدر: https://shop.example/p)", "confidence": 0.8,
                        "sources": ["https://shop.example/p"]}
            if "tasks" in sys_ or "خطة" in sys_[:400]:
                return {"summary": "s", "simple": False, "tasks": [
                    {"id": "t1", "title": "أسعار القهوة الباردة", "role": "researcher", "instructions": "ابحث عن الأسعار",
                     "acceptance": "أسعار بمصادر", "depends_on": []},
                    {"id": "t2", "title": "تقرير", "role": "writer", "instructions": "اكتب", "acceptance": "تقرير", "depends_on": ["t1"]}]}
            return {"pass": True, "score": 9, "issues": [], "claims": [], "items": []}

        async def chat(self, messages, *a, **k):
            return "السعر 9 ريال (المصدر: https://shop.example/p). انظر أيضًا https://unread.example/x"

    r = Run(goal="حلل أسعار القهوة الباردة المعلبة في السعودية", llm_public={})
    run(Orchestrator(r, LLM()).execute())
    types = [e["type"] for e in r.events]
    assert types.index("research_done") < types.index("agent_spawned")  # research first, then agents
    assert "أدلة قرأها النظام فعلًا" in seen["ctx"] and "https://shop.example/p" in seen["ctx"]
    assert "https://unread.example/x [رابط لم يقرأه النظام]" in r.result
    assert r.research_gate and r.research_gate["verdict"] != "sufficient"  # one shop page is not enough for prices
    assert r.status == "partial" and any("أدلة البحث غير كافية" in i for i in r.verdict["issues"])
    assert r.research["pages_read"] >= 1 and r.full()["research_state"]["readings"]


def test_evidence_ids_become_links_and_the_reviewer_sees_the_evidence(on):
    """An agent citing "(E1)" gets the real link, and the reviewer gets the figures the system read, so a
    documented number is not rejected as unsupported (production run 3de655c9f2f3, attempt 1: 3/10)."""
    from app.orchestrator import Orchestrator, Run

    good = page("سعر القهوة الباردة المعلبة", "سعر القهوة الباردة المعلبة 9 ريال في السعودية لدى المتاجر")
    web = Web({"https://shop.example/p": (200, good, HTML)})
    install(on, web, search={"": [{"title": "سعر القهوة الباردة المعلبة 9 ريال", "url": "https://shop.example/p", "snippet": "9 ريال"}]})
    reviewed = []

    class LLM:
        cfg = type("C", (), {"provider": "test"})()
        usage = type("U", (), {"as_dict": lambda self: {}})()

        async def chat_json(self, messages, *a, **k):
            sys_ = messages[0]["content"]
            if "مخطط بحث" in sys_:
                return {"subject_en": "canned cold brew coffee", "queries": {}}
            if "أنت وكيل متخصص" in sys_:
                return {"action": "final", "output": "السعر 9 ريال (E1)، والحجم غير متاح (E99).", "confidence": 0.8, "sources": []}
            if "tasks" in sys_ or "خطة" in sys_[:400]:
                return {"summary": "s", "simple": False, "tasks": [
                    {"id": "t1", "title": "أسعار القهوة الباردة", "role": "researcher", "instructions": "ابحث عن الأسعار",
                     "acceptance": "أسعار بمصادر", "depends_on": []},
                    {"id": "t2", "title": "تقرير", "role": "writer", "instructions": "اكتب", "acceptance": "تقرير", "depends_on": ["t1"]}]}
            if "الناتج:" in messages[-1]["content"]:
                reviewed.append(messages[-1]["content"])
            return {"pass": True, "score": 9, "issues": [], "claims": [], "items": []}

        async def chat(self, messages, *a, **k):
            return "السعر 9 ريال [E1]."

    r = Run(goal="حلل أسعار القهوة الباردة المعلبة في السعودية", llm_public={})
    o = Orchestrator(r, LLM())
    run(o.execute())
    t1 = r.tasks["t1"] if isinstance(r.tasks, dict) else next(t for t in r.tasks if t["id"] == "t1")
    assert "[E1](https://shop.example/p)" in t1["output"] and "(E99)" in t1["output"]  # unknown ids are left alone
    first = reviewed[0]
    assert "أدلة قرأها النظام بنفسه" in first and "[E1] https://shop.example/p" in first and "9 ريال" in first
    assert "[E1](https://shop.example/p)" in r.result and "[رابط لم يقرأه النظام]" not in r.result


def test_verifier_evidence_puts_cited_evidence_first_and_respects_the_limit():
    m = mgr()
    for i in range(1, 6):
        m.state["readings"][f"https://s{i}.example/"] = {
            "id": f"E{i}", "url": f"https://s{i}.example/", "domain": f"s{i}.example", "facets": ["prices"], "useful": True,
            "figures": [f"{i}0 ريال للعبوة"], "excerpt": "x " * 200, "completeness": "full", "kind": "html"}
    out = m.verifier_evidence("نعتمد على https://s5.example/ و E3", "الأسعار", max_chars=700)
    lines = [ln for ln in out.splitlines() if ln.startswith("[E")]
    assert lines[0].startswith("[E3]") and lines[1].startswith("[E5]") and len(out) <= 700
    assert m.link_ids("انظر E2 و[E4](https://s4.example/) وE7") == "انظر [E2](https://s2.example/) و[E4](https://s4.example/) وE7"


def test_claim_check_tells_the_reviewer_which_names_and_numbers_the_source_contains():
    m = mgr()
    url = "https://www.kenresearch.com/saudi-arabia-cold-brew-coffee-market"
    m.state["readings"][url] = {"id": "E17", "url": url, "domain": "kenresearch.com", "facets": ["competitors"], "useful": True,
                                "figures": [], "excerpt": "", "completeness": "full", "kind": "html"}
    m._texts[manager.normalize_url(url)] = "Key players include Coffee Planet and Almarai. The market reached USD 15 million in 2024."
    out = m.verifier_evidence("| Coffee Planet | [E17](%s) |\n| Death Wish | 15 مليون دولار [E17](%s) |" % (url, url), "", 3000)
    assert "موجود في نص المصدر: Coffee Planet، 15" in out
    assert "غير موجود في نص المصدر المستخرج: Death Wish" in out


def test_an_unparsed_final_reply_hands_in_its_output_text():
    from app.agents import _unwrap_final
    raw = '{"action":"final","output":"# عنوان\\n\\n| أ | ب |\\n\\"نص\\" \\u0645","confidence":0.7}'
    assert _unwrap_final(raw) == '# عنوان\n\n| أ | ب |\n"نص" م'
    assert _unwrap_final('{"action":"final","output":"مقطوع في المنتصف') == "مقطوع في المنتصف"
    assert _unwrap_final("نص عادي") == "نص عادي"


def test_headline_only_results_are_reported_as_such_not_as_budget(on):
    """Production run 84cca6feac24: Tavily's keyless cap was reached and Google News links could not be resolved
    from the server, so a facet had only headlines. That is not a budget problem and is said precisely."""
    web = Web({})
    gn = "https://news.google.com/rss/articles/CBMiabc?oc=5"
    install(on, web, search={"": [{"title": "Cold brew prices - Example News", "url": gn, "snippet": "Example — headline only"}]})
    m = mgr("ما أسعار القهوة الباردة المعلبة في السعودية؟", max_rounds=1)
    st = run(m.run())
    assert m.facet_status("prices")["status"] == "headlines_only"
    assert any("عناوين أخبار" in g for g in m.gaps()) and not st["readings"].get(gn)


def test_reviewer_is_told_which_gaps_the_system_documented(on):
    from app.orchestrator import Orchestrator, Run
    install(on, Web({}), search={"": []})
    reviewed = []

    class LLM:
        cfg = type("C", (), {"provider": "test"})()
        usage = type("U", (), {"as_dict": lambda self: {}})()

        async def chat_json(self, messages, *a, **k):
            sys_ = messages[0]["content"]
            if "مخطط بحث" in sys_:
                return {"subject_en": "canned cold brew coffee", "queries": {}}
            if "أنت وكيل متخصص" in sys_:
                return {"action": "final", "output": "السعر: غير متاح في الأدلة المقروءة.", "confidence": 0.5, "sources": []}
            if "tasks" in sys_ or "خطة" in sys_[:400]:
                return {"summary": "s", "simple": False, "tasks": [
                    {"id": "t1", "title": "أسعار القهوة الباردة", "role": "researcher", "instructions": "ابحث عن الأسعار",
                     "acceptance": "أسعار بمصادر", "depends_on": []},
                    {"id": "t2", "title": "تقرير", "role": "writer", "instructions": "اكتب", "acceptance": "تقرير", "depends_on": ["t1"]}]}
            if "الناتج:" in messages[-1]["content"]:
                reviewed.append(messages[-1]["content"])
            return {"pass": True, "score": 9, "issues": [], "claims": [], "items": []}

        async def chat(self, messages, *a, **k):
            return "السعر غير متاح."

    r = Run(goal="حلل أسعار القهوة الباردة المعلبة في السعودية", llm_public={})
    run(Orchestrator(r, LLM()).execute())
    assert "نواقص بحث وثّقها النظام نفسه" in reviewed[0] and "الأسعار" in reviewed[0]
    assert r.status == "partial"
