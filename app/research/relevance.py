"""Relevance gate: is this page evidence for THIS study, and for which information?

Why (production run 6a03d324e329, a Saudi market study): the system read an NPR story on cannabis prices, a
Vietnamese article on fuel prices, an arXiv paper on an Arabic dialect model and a story about Russian and Saudi
oil competing in West Africa. Some were marked useful because one study word («السعودي») appeared in them; the
others were marked not useful but still reached the agents through search listings and the reviewer.

Each reading gets, per research facet, one explainable label:
    direct       - about the product (or its category) in the target market, with information for the facet
    supporting   - useful context: the product's category, the market's official statistics, or a topic the facet
                   needs (packaging, shipping, food rules, consumer behaviour) without naming the product
    weak         - mentions the subject in passing, or names it without any information for the facet
    irrelevant   - about another subject or another market
    insufficient - relevant, but the page cannot prove what the facet needs (no figure in its text, a figure that
                   appears only in the title, or data too old for a facet that changes)
Only direct and supporting readings are handed to agents and used to verify figures. The label never replaces
checking the figure itself: a figure still has to appear in the page's text (see app/finmodel.py, _claim_check).

The rules are deterministic, so every decision is reproducible from the stored signals and reasons.
"""
from __future__ import annotations

import re
from datetime import date

VERSION = "1"  # shown in /health, so a deploy can be verified by content
USABLE = ("direct", "supporting")
LABEL_AR = {"direct": "ذات صلة مباشرة", "supporting": "ذات صلة مساندة", "weak": "ضعيفة الصلة",
            "irrelevant": "غير ذات صلة", "insufficient": "غير كافية لإثبات المعلومة"}
RANK = {"direct": 4, "supporting": 3, "insufficient": 2, "weak": 1, "irrelevant": 0}

_DIAC = re.compile(r"[\u064B-\u0652\u0640]")


def norm(s: str) -> str:
    """Lower-case, no diacritics or tatweel, one alef, ة→ه, ى→ي: «معلّبة» and «معلبه» match."""
    s = _DIAC.sub("", (s or "").lower())
    return s.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"}))


def _rx(words) -> re.Pattern:
    return re.compile("|".join(re.escape(norm(w)) for w in words if w), re.I)


# Product categories: a page about the category supports a study of a product in it.
CATEGORIES = [
    ("beverages", r"قهوه|كوفي|شاي|عصير|مشروب|مياه|حليب|لبن|طاقه|coffee|tea|juice|drink|beverage|water|milk|soda|latte",
     ["مشروب", "مشروبات", "beverage", "beverages", "drinks", "ready to drink", "ready-to-drink", "rtd", "جاهز للشرب",
      "قهوه", "coffee", "كافيين", "caffeine", "الاغذيه والمشروبات", "food and beverage", "food & beverage"]),
    ("food", r"غذا|اطعمه|طعام|تمر|حلوي|شوكولا|وجبات|مخبوزات|food|snack|dates|chocolate|bakery|sauce",
     ["اغذيه", "غذائي", "غذائيه", "food", "foods", "fmcg", "سلع استهلاكيه", "حلويات", "snack", "snacks"]),
    ("cosmetics", r"عطر|عطور|تجميل|بشره|perfume|fragrance|cosmetic|skincare",
     ["عطر", "عطور", "perfume", "fragrance", "تجميل", "cosmetics", "beauty", "عنايه بالبشره", "skincare"]),
    ("fashion", r"ملابس|ازياء|عبايات|احذيه|fashion|apparel|clothing|shoes",
     ["ملابس", "ازياء", "fashion", "apparel", "clothing", "عبايات", "footwear"]),
    ("software", r"تطبيق|منصه|برنامج|\bapp\b|platform|saas|software",
     ["تطبيق", "تطبيقات", "app", "apps", "منصه", "منصات", "platform", "software", "saas", "تنزيلات", "downloads"]),
    ("food_service", r"مطعم|مقهي|كافيه|restaurant|cafe|coffee shop",
     ["مطعم", "مطاعم", "مقهي", "مقاهي", "cafe", "cafes", "restaurant", "restaurants", "food service"]),
]

# What each facet needs, beyond its own name: a page about these topics supports the facet without naming the
# product (packaging and shipping prices for costs, food rules for regulation, retail for channels).
SUPPORT = {
    "costs": ["تغليف", "تعبئه", "عبوات", "علب", "packaging", "cans", "bottles", "شحن", "shipping", "freight", "لوجست",
              "logistics", "نقل", "تخزين", "warehouse", "مستودع", "رواتب", "اجور", "wages", "salary", "ايجار", "rent",
              "جمارك", "customs", "رسوم", "fees", "كهرباء", "electricity", "مواد خام", "raw material", "تكلفه الانتاج",
              "production cost", "تكاليف التشغيل", "operating cost"],
    "regulation": ["هيئه الغذاء والدواء", "الغذاء والدواء", "sfda", "saso", "اشتراطات", "بطاقه البيان", "labeling",
                   "labelling", "ترخيص", "تراخيص", "سجل تجاري", "ضريبه القيمه المضافه", "vat", "الضريبه الانتقائيه",
                   "excise", "مواصفات", "standards", "لائحه", "regulation"],
    "channels": ["تجزئه", "retail", "سوبرماركت", "supermarket", "هايبر", "hypermarket", "بقالات", "بقاله", "grocery",
                 "توصيل", "delivery", "تجاره الكترونيه", "e-commerce", "ecommerce", "موزع", "distributor", "توزيع",
                 "distribution", "سلاسل الامداد", "supply chain", "convenience store"],
    "consumers": ["سلوك المستهلك", "consumer behavior", "consumer behaviour", "purchase intention", "نيه الشراء",
                  "المستهلكين", "consumers", "شريحه", "segment", "سكان", "population", "دخل الفرد", "income", "انفاق",
                  "spending", "شباب", "youth"],
    "demand": ["استهلاك", "consumption", "الطلب على", "demand for", "per capita", "للفرد", "مبيعات", "sales volume",
               "حجم المبيعات"],
    "market_size": ["حجم السوق", "حجم سوق", "market size", "market value", "قيمه السوق", "cagr", "معدل النمو السنوي",
                    "مليار", "billion", "مليون", "million"],
    "prices": ["سعر", "اسعار", "price", "prices", "ريال", "sar", "ر.س", "خصم", "discount", "عرض", "offer"],
    "competitors": ["منافس", "منافسين", "competitor", "competitors", "علامه تجاريه", "علامات", "brand", "brands",
                    "حصه سوقيه", "market share", "شركه", "company"],
    "trends": ["اتجاه", "اتجاهات", "trend", "trends", "نمو", "growth", "توقعات", "forecast"],
    "risks": ["مخاطر", "تحديات", "risk", "risks", "challenge", "challenges"],
}
SUPPORT_RX = {k: _rx(v) for k, v in SUPPORT.items()}
# Facets whose information depends on the market: a page about another country does not answer them.
MARKET_FACETS = {"market_size", "demand", "competitors", "prices", "channels", "regulation", "consumers"}
# Facets where a page about the product elsewhere (a global report, a packaging supplier) can still support.
GLOBAL_OK = {"trends", "costs", "technology", "literature", "risks", "market_size"}
NUMERIC = {"market_size", "demand", "prices", "costs", "consumers"}
FRESH = {"market_size", "demand", "prices", "competitors", "trends"}
MAX_AGE = 3

TARGET_PLACES = {
    "SAU": ["السعوديه", "السعودي", "المملكه العربيه السعوديه", "saudi", "ksa", "الرياض", "جده", "الدمام", "مكه",
            "المدينه المنوره", "الخبر", "riyadh", "jeddah", "dammam", "mecca", "makkah", "ريال", "sar", "ر.س"],
    "ARE": ["الامارات", "اماراتي", "uae", "emirates", "دبي", "ابوظبي", "dubai", "abu dhabi", "درهم", "aed"],
    "EGY": ["مصر", "مصري", "egypt", "القاهره", "cairo", "جنيه", "egp"],
    "KWT": ["الكويت", "kuwait", "دينار كويتي", "kwd"],
    "QAT": ["قطر", "qatar", "الدوحه", "doha", "qar"],
    "JOR": ["الاردن", "jordan", "عمّان", "دينار اردني", "jod"],
    "OMN": ["سلطنه عمان", "oman", "مسقط", "muscat", "omr"],
    "BHR": ["البحرين", "bahrain", "المنامه", "bhd"],
}
TARGET_TLD = {"SAU": "sa", "ARE": "ae", "EGY": "eg", "KWT": "kw", "QAT": "qa", "JOR": "jo", "OMN": "om", "BHR": "bh"}
# Other countries, to see that a page is about another market (Vietnam fuel prices, US cannabis prices).
FOREIGN = {
    "vn": ["فيتنام", "vietnam", "هانوي", "hanoi", "dong"], "us": ["الولايات المتحدة", "أمريكا", "united states", "u.s.",
    "america", "american", "دولار امريكي"], "gb": ["بريطانيا", "المملكة المتحدة", "united kingdom", "britain", "uk "],
    "eg": ["مصر", "egypt", "جنيه مصري"], "sy": ["سوريا", "syria", "ليره سوريه"], "ng": ["نيجيريا", "nigeria", "naira"],
    "in": ["الهند", "india", "rupee"], "cn": ["الصين", "china", "yuan"], "ru": ["روسيا", "russia", "ruble"],
    "tr": ["تركيا", "turkey", "turkiye", "lira"], "ir": ["إيران", "iran"], "pk": ["باكستان", "pakistan"],
    "id": ["إندونيسيا", "indonesia"], "jp": ["اليابان", "japan"], "de": ["ألمانيا", "germany"], "fr": ["فرنسا", "france"],
    "ca": ["كندا", "canada"], "au": ["أستراليا", "australia"], "br": ["البرازيل", "brazil"], "mx": ["المكسيك", "mexico"],
    "iq": ["العراق", "iraq"], "lb": ["لبنان", "lebanon"], "ma": ["المغرب", "morocco"], "dz": ["الجزائر", "algeria"],
    "tn": ["تونس", "tunisia"], "ke": ["كينيا", "kenya"], "za": ["جنوب أفريقيا", "south africa"], "gh": ["غانا", "ghana"],
    "ph": ["الفلبين", "philippines"], "th": ["تايلاند", "thailand"], "my": ["ماليزيا", "malaysia"], "kr": ["كوريا", "korea"],
    "ae": ["الإمارات", "uae", "dubai", "دبي"], "kw": ["الكويت", "kuwait"], "qa": ["قطر", "qatar"], "jo": ["الأردن", "jordan"],
    "bh": ["البحرين", "bahrain"], "om": ["سلطنة عمان", "oman"], "sa": ["السعودية", "saudi", "ksa", "الرياض", "riyadh"],
}
_ISO2 = {"SAU": "sa", "ARE": "ae", "EGY": "eg", "KWT": "kw", "QAT": "qa", "JOR": "jo", "OMN": "om", "BHR": "bh"}
_NUM_UNIT = re.compile(r"(\d[\d,.]*)\s*(?:%|٪|ريال|ر\.س|sar|sr\b|دولار|usd|\$|مليون|مليار|billion|million)", re.I)
_STOPW = set(norm(w) for w in """منتج منتجات جديد جديده السوق سوق في من الى على مع جاهز جاهزه فرصه اطلاق دراسه مشروع خدمه
المنافسين منافسين والطلب الطلب طلب اسعار الاسعار السعودي السعوديه المملكه the a an of in for and to ready new product
products market markets saudi arabia opportunity launch launching study project service competitors demand prices""".split())


class Topic:
    """What the study is about, built once per run from the subject, the country and the facets."""

    def __init__(self, subject: dict | None, country: dict | None, facets=(), today: date | None = None):
        s = subject or {}
        ar = [w for w in re.findall(r"[\u0621-\u064A]{3,}", norm(s.get("ar", ""))) if w not in _STOPW]
        en = [w for w in re.findall(r"[a-z]{3,}", norm(s.get("en", ""))) if w not in _STOPW]
        ar = [re.sub(r"^(?:ال|وال|بال)", "", w) for w in ar]
        ar = [w[:-1] if len(w) > 4 and w.endswith("ه") else w for w in ar]  # «معلبه» also matches «معلب»
        self.head = [w for w in (ar[:1] + en[-1:]) if len(w) >= 3]  # Arabic head first, English head last
        self.mods = [w for w in ar[1:] + en[:-1] if len(w) >= 3]
        self.has_ar, self.has_en = bool(ar), bool(en)
        hay = " ".join(ar + en)
        self.categories = [c for c, pat, _ in CATEGORIES if re.search(pat, hay)]
        self.cat_rx = _rx([t for c, _, terms in CATEGORIES if c in self.categories for t in terms]) if self.categories else None
        self.country = country or None
        iso = (country or {}).get("iso")
        self.iso2 = _ISO2.get(iso or "", "")
        self.target_rx = _rx(TARGET_PLACES.get(iso, [])) if iso else None
        self.foreign = {k: _rx(v) for k, v in FOREIGN.items() if k != self.iso2}
        self.facets = list(facets or [])
        self.today = today or date.today()

    @property
    def known(self) -> bool:
        return bool(self.head or self.categories)


def _count(rx: re.Pattern | None, text: str) -> int:
    return len(rx.findall(text)) if rx is not None and text else 0


def _distinct(rx: re.Pattern | None, text: str) -> int:
    """Different terms, not repetitions: «التوزيع» five times in a film interview is still one topic word."""
    return len({m.lower() for m in rx.findall(text)}) if rx is not None and text else 0


def _year(s: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group(0)) if m else None


def signals(topic: Topic, url: str, title: str, text: str, figures=(), offers=(), tables=()) -> dict:
    """Counts the decision is made from (stored with the label so it can be audited)."""
    t, body = norm(title), norm(text)[:40000]
    hay = t + " " + body
    host = re.sub(r"^https?://", "", (url or "").lower()).split("/")[0]
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    head_title = sum(1 for w in topic.head if w in t)
    head_n = sum(hay.count(w) for w in topic.head)
    mod_n = sum(hay.count(w) for w in topic.mods)
    cat_title = _count(topic.cat_rx, t)
    cat_n = _count(topic.cat_rx, hay)
    target = _count(topic.target_rx, hay) + (3 if topic.iso2 and (tld == topic.iso2 or f".{topic.iso2}/" in (url or ""))
                                             else 0)
    foreign = {k: _count(rx, hay) for k, rx in topic.foreign.items()}
    if tld in topic.foreign and tld != topic.iso2:
        foreign[tld] = foreign.get(tld, 0) + 3  # vietnam.vn: the publisher's own country
    foreign = {k: v for k, v in foreign.items() if v}
    letters = re.findall(r"[\u0621-\u064A]|[a-z]", hay[:20000])
    arabic_page = sum(1 for c in letters if c >= "\u0621") >= 0.3 * max(1, len(letters))
    title_nums = [m.group(1).rstrip(".,") for m in _NUM_UNIT.finditer(t)]
    body_flat = body.replace(",", "")
    title_unproven = [n for n in title_nums if n.replace(",", "") not in body_flat]
    return {"head_title": head_title, "head": head_n, "mods": mod_n, "cat_title": cat_title, "category": cat_n,
            "target": target, "foreign": foreign, "figures": len(figures or []), "offers": len(offers or []),
            "tables": len(tables or []), "title_unproven": title_unproven, "chars": len(text or ""),
            "matchable": (topic.has_ar if arabic_page else topic.has_en) or cat_n > 0}


def assess(topic: Topic, facet: str | None, url: str, title: str, text: str, *, figures=(), offers=(), tables=(),
           published: str = "", cls: str = "", method: str = "") -> dict:
    """One facet's verdict for one reading: {label, label_ar, reasons, usable, unproven, signals}."""
    sg = signals(topic, url, title, text, figures, offers, tables)
    hay = norm(title) + " " + norm(text)[:40000]
    reasons, usable, unproven = [], [], []
    f = facet if facet in SUPPORT or facet in NUMERIC else None
    support_n = _distinct(SUPPORT_RX.get(f), hay) if f else max([_distinct(SUPPORT_RX.get(x), hay) for x in topic.facets
                                                                 if x in SUPPORT_RX] or [0])
    content = sg["figures"] + sg["offers"] + sg["tables"]
    market_dep = bool(topic.country) and (f in MARKET_FACETS if f else bool(set(topic.facets) & MARKET_FACETS))
    tgt, fgn = sg["target"], sum(sg["foreign"].values())
    foreign_dom = bool(topic.country) and fgn >= 2 and (tgt == 0 or fgn >= 3 * tgt + 3)
    where = "، ".join(FOREIGN[k][0] for k in sorted(sg["foreign"], key=lambda k: -sg["foreign"][k])[:3])

    if method == "api:worldbank":  # official indicator for a country
        if topic.country and tgt == 0 and fgn:
            return _out("irrelevant", ["مؤشر رسمي لبلد غير البلد المستهدف"], [], [], sg)
        lab = "supporting" if f != "prices" else "weak"
        return _out(lab, ["مؤشر رسمي من البنك الدولي للبلد المستهدف: سياق عام لا يخص المنتج"], ["مؤشر رسمي للبلد"], [], sg)

    strong = sg["head_title"] > 0 or sg["head"] >= 2 or (sg["head"] >= 1 and sg["mods"] >= 1)
    category = sg["category"] >= 2 or sg["cat_title"] > 0
    topical = strong or category
    passing = (sg["head"] + sg["category"]) > 0 and not topical

    if not topic.known or not sg["matchable"]:
        # No product name to compare in this page's language: judge by market and facet only, never "direct".
        why = "لا منتج محدد في الطلب" if not topic.known else "لا اسم للمنتج بلغة الصفحة"
        if (foreign_dom and market_dep) or (tgt == 0 and fgn and topic.country):
            return _out("irrelevant", [f"عن سوق آخر ({where}) ولا تذكر السوق المستهدف"], [], [], sg)
        if market_dep and tgt == 0:
            return _out("weak", [f"{why}، ولا تذكر السوق المستهدف"], [], [], sg)
        if f in SUPPORT and support_n < 2:
            return _out("weak", [f"{why}، ولا معلومة فيها عن المحور"], [], [], sg)
        reasons.append(f"{why}: حُكم عليها بالسوق والمحور فقط")
        label = "supporting"
        return _finish(label, reasons, usable, unproven, sg, f, content, published, topic)

    if foreign_dom and market_dep and not (topical and f in GLOBAL_OK):
        return _out("irrelevant", [f"عن سوق آخر ({where}) ولا تذكر السوق المستهدف"], [], [], sg)
    if not topical and support_n < 2:
        if passing:
            return _out("weak", ["تذكر المنتج أو فئته ذكرًا عابرًا دون أن تكون عنه"], [], [], sg)
        return _out("irrelevant", ["لا تتناول المنتج ولا فئته ولا موضوعًا يحتاجه المحور"], [], [], sg)
    info = support_n + (sg["offers"] if f in (None, "prices", "competitors") else 0)
    if topical and info == 0 and content == 0:
        return _out("weak", ["تذكر الموضوع لكن لا معلومة فيها عن المحور المطلوب"], [], [], sg)

    if strong:
        if market_dep and tgt == 0:
            label, why = "supporting", "عن المنتج نفسه لكنها لا تخص السوق المستهدف"
        else:
            label, why = "direct", "عن المنتج نفسه" + (" في السوق المستهدف" if tgt else "")
    elif topical:
        if market_dep and tgt == 0 and f not in GLOBAL_OK:
            label, why = "weak", "عن فئة المنتج في سوق غير محدد، والمحور يحتاج السوق المستهدف"
        else:
            label, why = "supporting", "عن فئة المنتج" + (" في السوق المستهدف" if tgt else "") + " دون ذكره بالاسم"
    else:  # supporting topic only (packaging, shipping, food rules, retail)
        if tgt == 0 and fgn and topic.country:
            return _out("irrelevant", [f"لا تخص المنتج، وهي عن سوق آخر ({where})"], [], [], sg)
        if market_dep and tgt == 0 and f not in GLOBAL_OK:
            return _out("irrelevant", ["لا تخص المنتج ولا السوق المستهدف"], [], [], sg)
        else:
            label, why = "supporting", "موضوع يحتاجه المحور (" + (facet or "الدراسة") + ") دون ذكر المنتج"
    reasons.append(why)
    return _finish(label, reasons, usable, unproven, sg, f, content, published, topic)


def _finish(label, reasons, usable, unproven, sg, f, content, published, topic) -> dict:
    """Checks that apply to every relevant page: does its text prove figures, and is it fresh enough."""
    if sg["figures"] or sg["offers"]:
        usable.append(f"أرقام في نص الصفحة: {sg['figures']}، عروض أسعار منظمة: {sg['offers']}")
    if sg["tables"]:
        usable.append(f"جداول: {sg['tables']}")

    for n in sg["title_unproven"]:
        unproven.append(f"الرقم «{n}» في العنوان غير موجود في نص الصفحة")
    numeric = (f in NUMERIC) if f else False
    if numeric and content == 0:
        unproven.append("لا رقم في نص الصفحة يثبت المعلومة المطلوبة")
        reasons.append("ذات صلة لكنها لا تحتوي أرقامًا لهذا المحور")
        label = "insufficient"
    elif numeric and sg["title_unproven"] and sg["figures"] == 0 and sg["offers"] == 0:
        reasons.append("الرقم في العنوان فقط")
        label = "insufficient"

    y = _year(published)
    if y and (f in FRESH if f else False):
        age = topic.today.year - y
        if age > MAX_AGE:
            reasons.append(f"قديمة ({y}، قبل {age} سنوات) والمعلومة متغيرة")
            unproven.append(f"لا تثبت القيمة الحالية: بيانات {y}")
            if label == "direct":
                label = "supporting"
            if f == "prices" and age > 2 * MAX_AGE and label in USABLE:
                label = "insufficient"
    return _out(label, reasons, usable, unproven, sg)


def _out(label, reasons, usable, unproven, sg) -> dict:
    return {"label": label, "label_ar": LABEL_AR[label], "reasons": reasons, "usable": usable, "unproven": unproven,
            "signals": sg}


def best(per_facet: dict) -> str:
    return max((v["label"] for v in per_facet.values()), key=lambda x: RANK[x], default="irrelevant")


def summary(readings) -> dict:
    """Counts by label (best label per reading) for stats and the quality gate."""
    out = {k: 0 for k in LABEL_AR}
    for r in readings:
        rel = r.get("relevance")
        if rel:
            out[best(rel)] += 1
    return out
