"""Relevance gate (app/research/relevance.py) and its wiring into the research manager — offline.

The ten cases the B2 directive requires, cases that guard against over-strictness, an end-to-end check that
rejected pages never reach agents, the reviewer or the financial model, and a replay of the pages production run
6a03d324e329 read (NPR cannabis prices, Vietnamese fuel prices, an Arabic dialect model paper, Russian oil).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from app import finmodel
from app.research import manager, quality_gate
from app.research import relevance as rel
from tests.test_research_manager import HTML, Web, install, mgr, on  # noqa: F401  (fixture)

run = asyncio.run
TODAY = date(2026, 11, 27)
SAU = {"ar": "السعودية", "en": "Saudi Arabia", "iso": "SAU"}
FACETS = ["market_size", "demand", "competitors", "prices", "channels", "costs", "regulation", "consumers"]
COFFEE = rel.Topic({"ar": "قهوة باردة معلبة", "en": "canned cold coffee"}, SAU, FACETS, TODAY)


def judge(facet, url, title, text, **kw):
    return rel.assess(COFFEE, facet, url, title, text, **kw)


# ------------------------------------------------------------------ the ten required cases
def test_saudi_page_selling_the_product_is_direct():
    v = judge("prices", "https://www.noon.com/saudi-ar/cold-brew-can", "قهوة باردة معلبة 240 مل",
              "قهوة باردة معلبة جاهزة للشرب، علبة 240 مل. السعر 7.95 ريال شامل الضريبة. التوصيل داخل الرياض وجدة.",
              offers=[{"name": "قهوة باردة 240 مل", "price": 7.95, "currency": "SAR"}], figures=["السعر 7.95 ريال"])
    assert v["label"] == "direct" and v["reasons"] and v["usable"]


def test_real_competitor_in_the_target_market_is_accepted():
    v = judge("competitors", "https://www.almarai.com/ar/products/coffee", "المراعي — قهوة جاهزة",
              "تقدم المراعي مشروبات القهوة الباردة الجاهزة في المملكة العربية السعودية بنكهات لاتيه وموكا، "
              "وتباع في محلات التجزئة في الرياض وجدة والدمام. علامة تجارية سعودية رائدة في الألبان والعصائر.")
    assert v["label"] in rel.USABLE


def test_official_sector_report_is_accepted_when_it_serves_the_study():
    v = judge("market_size", "https://www.stats.gov.sa/ar/food-beverage-2025", "تقرير قطاع الأغذية والمشروبات 2025",
              "الهيئة العامة للإحصاء: بلغ حجم سوق المشروبات في السعودية 22 مليار ريال في 2025، بمعدل نمو سنوي 6%. "
              "وتشكل المشروبات الجاهزة للشرب حصة متزايدة من مبيعات التجزئة.",
              figures=["حجم سوق المشروبات في السعودية 22 مليار ريال", "معدل نمو سنوي 6%"], published="2025-09-01")
    assert v["label"] in rel.USABLE


def test_packaging_and_shipping_source_supports_costs_without_naming_the_product():
    v = judge("costs", "https://www.packaging-supplier.example/aluminium-cans", "Aluminium beverage cans — wholesale",
              "Aluminium cans 250 ml for beverages. Price per 1,000 cans: 420 USD. Shipping by sea freight to Jeddah "
              "takes 18 days; packaging minimum order 50,000 units.",
              figures=["Price per 1,000 cans: 420 USD"])
    assert v["label"] == "supporting", v


def test_cannabis_prices_news_is_rejected():
    v = judge("prices", "https://www.npr.org/2026/11/25/cannabis-marijuana-prices",
              "In a time of rising costs, one Thanksgiving commodity is cheaper: cannabis",
              "Cannabis prices at retailers in Colorado and across the United States fell 20% this year as discounts "
              "spread before Thanksgiving. American growers say the price per pound dropped to $800.",
              figures=["fell 20%", "$800"])
    assert v["label"] == "irrelevant"


def test_vietnam_fuel_prices_are_rejected():
    v = judge("prices", "https://www.vietnam.vn/ar/gia-xang-dau", "على الرغم من انخفاض أسعار الوقود، لا يزال المستهلكون ينتظرون",
              "انخفضت أسعار الوقود في فيتنام هذا الأسبوع، لكن المستهلكين في هانوي ما زالوا ينتظرون انخفاض أسعار السلع. "
              "وقالت وزارة الصناعة الفيتنامية إن سعر البنزين 20 ألف دونغ.", figures=["سعر البنزين 20 ألف دونغ"])
    assert v["label"] == "irrelevant" and "فيتنام" in v["reasons"][0]


def test_general_article_with_similar_words_but_nothing_usable_is_rejected():
    v = judge("prices", "https://lifestyle.example/coffee-morning", "قهوة الصباح الباردة: عادات جميلة",
              "يحب كثيرون شرب القهوة في الصباح، والقهوة الباردة خيار منعش في الصيف. نصائح لتحضيرها في البيت بمكونات بسيطة.")
    assert v["label"] not in rel.USABLE


def test_good_source_that_does_not_name_the_product_is_not_auto_rejected():
    v = judge("channels", "https://www.argaam.com/ar/article/retail-beverages",
              "نمو مبيعات المشروبات الجاهزة في المتاجر السعودية",
              "ارتفعت مبيعات المشروبات الجاهزة للشرب في السوبرماركت والبقالات في السعودية، وتوسعت قنوات التوصيل "
              "والتجارة الإلكترونية في الرياض بنسبة 15%.", figures=["بنسبة 15%"])
    assert v["label"] == "supporting"


def test_price_in_the_title_but_not_in_the_page_does_not_prove_a_price():
    v = judge("prices", "https://shop.example.sa/cold-coffee", "قهوة باردة معلبة بسعر 6 ريال فقط",
              "قهوة باردة معلبة جاهزة للشرب، متوفرة الآن في متاجرنا في الرياض. اطلبها اليوم مع توصيل سريع.")
    assert v["label"] == "insufficient"
    assert any("«6»" in u for u in v["unproven"]) and not v["usable"]


def test_old_source_for_a_changing_fact_is_flagged_for_freshness():
    v = judge("market_size", "https://www.report.example/saudi-rtd-coffee-2018",
              "Saudi Arabia ready to drink coffee market 2018",
              "The Saudi Arabia ready to drink coffee market was valued at 120 million USD in 2018 and Riyadh led sales.",
              figures=["valued at 120 million USD in 2018"], published="2018-05-01")
    assert v["label"] == "supporting"  # still context, no longer "direct"
    assert any("قديمة" in r for r in v["reasons"]) and any("2018" in u for u in v["unproven"])


# ------------------------------------------------------------------ not too strict
@pytest.mark.parametrize("facet,url,title,text,kw", [
    ("costs", "https://www.freight.example/sea-rates", "أسعار الشحن البحري والتخزين في ميناء جدة",
     "تكلفة شحن حاوية 40 قدمًا من الصين إلى ميناء جدة 2,400 دولار، وتكلفة التخزين في المستودع المبرد 35 ريالًا "
     "للمتر يوميًا، مع رسوم جمارك 5%.", {"figures": ["2,400 دولار", "35 ريالًا", "5%"]}),
    ("regulation", "https://www.sfda.gov.sa/ar/regulations/food-labeling", "اشتراطات بطاقة البيان للمنتجات الغذائية",
     "تشترط الهيئة العامة للغذاء والدواء في السعودية أن تحمل بطاقة البيان للأغذية والمشروبات تاريخ الإنتاج والانتهاء، "
     "مع تسجيل المنتج قبل طرحه في الأسواق.", {}),
    ("consumers", "https://journal.example/saudi-youth", "Consumer behavior of Saudi youth toward caffeinated drinks",
     "A survey of 1,200 Saudi consumers in Riyadh found 64% of youth buy caffeinated drinks weekly; purchase intention "
     "rises with convenience.", {"figures": ["64% of youth buy caffeinated drinks weekly"]}),
    ("competitors", "https://www.arabnews.com/rtd-coffee-brands", "Saudi coffee brands bet on canned cold brew",
     "Saudi coffee brands such as Barns and Dr.Cafe launched canned cold brew in supermarkets across the Kingdom; "
     "the company said sales doubled.", {}),
    ("demand", "https://www.spa.gov.sa/coffee-consumption", "استهلاك القهوة في المملكة",
     "بلغ استهلاك القهوة في المملكة 80 ألف طن سنويًا، ويزداد الطلب على القهوة الجاهزة بين الشباب.",
     {"figures": ["80 ألف طن سنويًا"]}),
])
def test_good_sources_are_kept(facet, url, title, text, kw):
    assert judge(facet, url, title, text, **kw)["label"] in rel.USABLE


def test_every_verdict_is_explained_and_auditable():
    v = judge("prices", "https://x.example", "t", "lorem ipsum")
    assert set(v) == {"label", "label_ar", "reasons", "usable", "unproven", "signals"}
    assert v["reasons"] and v["label_ar"] == rel.LABEL_AR[v["label"]]
    assert {"head", "category", "target", "foreign", "figures"} <= set(v["signals"])


def test_no_product_named_means_the_page_is_judged_by_market_and_facet_only():
    t = rel.Topic({"ar": "", "en": ""}, SAU, FACETS, TODAY)
    ok = rel.assess(t, "channels", "https://a.example.sa", "قنوات التجزئة في السعودية",
                    "تتوسع قنوات التجزئة والتجارة الإلكترونية والتوصيل في السعودية.")
    assert ok["label"] == "supporting"
    far = rel.assess(t, "prices", "https://b.example", "أسعار الوقود في فيتنام", "انخفضت أسعار الوقود في فيتنام وهانوي.")
    assert far["label"] == "irrelevant"


def test_subject_is_read_from_the_intake_label_and_commas_are_not_words():
    assert manager.extract_subject("ادرس فرصة إطلاق منتج\n\nمعطيات أكدها المستخدم:\n- المنتج أو المشروع: قهوة باردة معلبة") == "قهوة باردة معلبة"
    assert manager.subject_from_goal("ادرس فرصة إطلاق منتج جديد في السوق السعودي، وابحث عن المنافسين، وحلل الأسعار والطلب")[1] == "guess"
    assert "،" not in manager.extract_subject("ادرس فرصة إطلاق منتج جديد في السوق السعودي، وابحث عن المنافسين، وحلل الأسعار والطلب")


# ------------------------------------------------------------------ wiring: rejected pages never become evidence
GOAL = "ادرس فرصة إطلاق منتج في السوق السعودي وحلل الأسعار. المنتج: قهوة باردة معلبة"


def _two_pages(on):  # noqa: F811
    good = ("<html><head><title>قهوة باردة معلبة 240 مل</title></head><body><article><p>قهوة باردة معلبة جاهزة للشرب. "
            "السعر 7.95 ريال في الرياض. علبة 240 مل متوفرة في السوبرماركت والتوصيل في جدة.</p></article></body></html>").encode()
    bad = ("<html><head><title>Cannabis prices fall</title></head><body><article><p>Cannabis prices in the United States "
           "fell 20% before Thanksgiving; American retailers cut the price per pound to $800. Colorado growers in the U.S. "
           "say discounts will continue.</p></article></body></html>").encode()
    web = Web({"https://shop.example.sa/coffee": (200, good, HTML), "https://news.example.com/cannabis": (200, bad, HTML)})
    install(on, web, search={"": [{"title": "قهوة باردة", "url": "https://shop.example.sa/coffee", "snippet": "7.95 ريال"},
                                  {"title": "Cannabis prices", "url": "https://news.example.com/cannabis", "snippet": "$800"}]})
    m = mgr(GOAL, max_rounds=1)
    m.today = TODAY
    run(m.plan())
    g = run(m.open_url("https://shop.example.sa/coffee", "prices"))
    b = run(m.open_url("https://news.example.com/cannabis", "prices"))
    return m, g, b


def test_rejected_page_is_absent_from_pack_index_evidence_and_the_financial_model(on):  # noqa: F811
    m, g, b = _two_pages(on)
    assert g["useful"] and g["label"] == "direct"
    assert not b["useful"] and b["label"] == "irrelevant" and b["relevance"]["prices"]["reasons"]
    pack, idx = m.pack("الأسعار"), m.index()
    assert g["url"] in pack and b["url"] not in pack and b["url"] not in idx
    assert b not in m.evidence_for("الأسعار")
    assert m.evidence_text(b["id"]) is None and "7.95" in m.evidence_text(g["id"])
    chk = finmodel.Checker(m.evidence_text, GOAL)
    kind, _, problem = chk.tag(800, f"[{b['id']}]")
    assert kind == "assumption" and "رُفض لعدم صلته" in problem
    assert chk.tag(7.95, f"[{g['id']}]")[0] == "evidence"
    st = m.facet_status("prices")
    assert st["useful"] == 1 and st["rejected"] == 1


def test_agents_do_not_get_rejected_text_and_the_reviewer_sees_the_rejection(on):  # noqa: F811
    m, g, b = _two_pages(on)
    txt = m.render_reading(b)
    assert "رفض النظام هذه الصفحة" in txt and "$800" not in txt
    listing = m._listing(["x"], [g, b])
    assert "رفضها دليلًا" in listing and listing.count(b["url"]) == 1 and "$800" not in listing
    rev = m.verifier_evidence(f"السعر 800 دولار [{b['id']}]")
    assert "مرفوض دليلًا" in rev


def test_gate_flags_an_answer_that_cites_a_rejected_page(on):  # noqa: F811
    m, g, b = _two_pages(on)
    m.state["status"] = {f: m.facet_status(f) for f in m.state["facets"]}
    bad = quality_gate.evaluate(m.state, {}, f"السعر 800 دولار [{b['id']}]({b['url']})", today=TODAY)
    assert not bad["checks"]["no_rejected_evidence"]["ok"] and bad["verdict"] != "sufficient"
    ok = quality_gate.evaluate(m.state, {}, f"السعر 7.95 ريال [{g['id']}]({g['url']})", today=TODAY)
    assert ok["checks"]["no_rejected_evidence"]["ok"]
    assert ok["checks"]["relevance"]["value"]["irrelevant"] == 1


def test_no_data_and_insufficient_data_are_different_gaps(on):  # noqa: F811
    title_only = ("<html><head><title>قهوة باردة معلبة بسعر 6 ريال</title></head><body><article><p>قهوة باردة معلبة جاهزة "
                  "للشرب متوفرة الآن في متاجرنا في الرياض. اطلبها اليوم مع توصيل سريع إلى باب البيت في كل مدن المملكة.</p>"
                  "</article></body></html>").encode()
    web = Web({"https://shop.example.sa/t": (200, title_only, HTML)})
    install(on, web)
    m = mgr(GOAL, max_rounds=1)
    m.today = TODAY
    run(m.plan())
    run(m.open_url("https://shop.example.sa/t", "prices"))
    m.state["status"] = {f: m.facet_status(f) for f in m.state["facets"]}
    assert m.state["status"]["prices"]["status"] == "insufficient_evidence"
    others = [s["status"] for f, s in m.state["status"].items() if f != "prices"]
    assert "no_results" in others
    gaps = "\n".join(m.gaps())
    assert "لا تثبت المعلومة" in gaps and "لم يجد البحث نتائج" in gaps


def test_a_facet_added_later_is_judged_for_that_facet(on):  # noqa: F811
    m, g, _ = _two_pages(on)
    run(m.open_url("https://shop.example.sa/coffee", "channels"))
    assert "channels" in g["relevance"] and g["relevance"]["channels"]["label"] in rel.USABLE


# ------------------------------------------------------------------ replay of production run 6a03d324e329
FIX = json.loads((Path(__file__).parent / "fixtures_run_6a03d324e329_readings.json").read_text())


def _replay(subject):
    t = rel.Topic(subject, FIX["country"], list(FIX["facets"]), TODAY)
    out = {}
    for r in FIX["readings"]:
        text = "\n".join([r.get("excerpt") or ""] + list(r.get("figures") or []))
        out[r["id"]] = rel.best({f: rel.assess(t, f, r["url"], r["title"], text, figures=r.get("figures"),
                                              offers=r.get("offers"), published=r.get("published") or "",
                                              method=r.get("method") or "") for f in r["facets"]})
    return out


@pytest.mark.parametrize("subject", [{"ar": "قهوة باردة معلبة", "en": "canned cold coffee"},
                                     {"ar": "", "en": ""}])  # with the scenario's product, and with none named
def test_replay_of_production_run_rejects_what_it_wrongly_used(subject):
    got = _replay(subject)
    # NPR cannabis, Vietnam fuel, Arabic dialect model, e-government paper, Russian/Saudi oil, a security contest,
    # Egyptian food prices, Syrian diesel, a film interview: none may be evidence for a Saudi coffee study.
    for eid in ("E13", "E8", "E11", "E16", "E6", "E7", "E12", "E15", "E10"):
        assert got[eid] not in rel.USABLE, (eid, got[eid])
    # The good ones survive: World Bank population, the Saudi supply-chain article.
    assert got["E3"] in rel.USABLE and got["E9"] in rel.USABLE


def test_readings_saved_before_the_gate_keep_their_flag():
    old = {"url": "https://a.example", "useful": True, "facets": ["prices"]}
    assert manager.usable_for(old, "prices") and not manager.usable_for({**old, "useful": False}, "prices")
