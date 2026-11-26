"""Research manager: the part of a run that finds, chooses, opens and reads sources by itself.

Before this module, a source was read only when a language model remembered to call fetch_url, and nothing
ever opened what Crossref or arXiv listed. Now the run does it as code, before any analysis agent starts:

    goal -> facets (independent axes of the question, chosen from the goal's own words, not a fixed list)
         -> discovery per facet through the existing provider registry (web chain, Crossref, arXiv, GitHub,
            World Bank) -> one candidate list, deduplicated by normalized URL
         -> deterministic ranking (relevance, publisher class for this facet, freshness, figures and tables
            in the snippet, independence, already read, already failed) — search rank is not reliability
         -> opening within a budget (searches, opens, per facet, seconds, concurrency), robots.txt and
            per-host spacing respected, through the same SSRF-safe reader as fetch_url
         -> extraction (excerpt, figures with units, tables, structured offers) and one ledger event per try
         -> facet status (no results / not opened / refused / opened, not useful / useful / sufficient /
            conflicting) -> another round with other queries for facets that are not covered
         -> a pack of read evidence ([E#] ids) handed to the agents, the reviewer and the quality gate.

Agents' web_search, research_sources and fetch_url calls go through the same manager, so a page is opened
once per run, every opening is recorded, and a listing is never mistaken for a reading. A search result is
never evidence by itself; an abstract is evidence of the abstract only ("summary"), never of the full study.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from . import access as acc
from . import registry, specialized
from .registry import normalize_url

ENABLED = os.environ.get("RESEARCH_MANAGER", "1") != "0"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Budget:
    """How much one run may spend on research. Every limit can be changed per deployment (env) or per run."""
    max_searches: int = field(default_factory=lambda: _env_int("RESEARCH_MAX_SEARCHES", 30))
    max_opens: int = field(default_factory=lambda: _env_int("RESEARCH_MAX_OPENS", 24))
    per_facet_opens: int = field(default_factory=lambda: _env_int("RESEARCH_PER_FACET_OPENS", 3))
    max_agent_opens: int = field(default_factory=lambda: _env_int("RESEARCH_MAX_AGENT_OPENS", 30))
    max_seconds: float = field(default_factory=lambda: float(_env_int("RESEARCH_MAX_SECONDS", 240)))
    max_rounds: int = field(default_factory=lambda: _env_int("RESEARCH_MAX_ROUNDS", 3))
    concurrency: int = field(default_factory=lambda: _env_int("RESEARCH_CONCURRENCY", 4))
    open_timeout: float = 30.0


# ---------------------------------------------------------------- facets
# A facet is chosen only when the goal's words call for it (explicit: critical), or when the kind of request
# implies it (implied: supporting). "numeric": the facet needs documented figures. "official": an official
# source is expected. "fresh": old data is a gap. "classes": which publisher classes answer it best.
FACETS: dict[str, dict] = {
    "market_size": {"ar": "حجم السوق", "en": "market size", "q_ar": "حجم سوق {s} في {c}", "q_en": "{s} market size {c}",
                    "terms": r"حجم السوق|حجم سوق|قيمة السوق|market size|السوق", "numeric": True, "official": True, "fresh": True,
                    "classes": {"official": 3, "market_report": 2.5, "news": 2, "academic": 1.5}},
    "demand": {"ar": "الطلب والاستهلاك", "en": "demand consumption", "q_ar": "الطلب على {s} في {c} استهلاك",
               "q_en": "{s} consumption demand {c}", "terms": r"الطلب|طلب|استهلاك|demand|consumption",
               "numeric": True, "fresh": True, "classes": {"official": 3, "academic": 2.5, "market_report": 2, "news": 2}},
    "competitors": {"ar": "المنافسون", "en": "competitors brands", "q_ar": "أشهر علامات {s} في {c} المنافسين",
                    "q_en": "{s} brands competitors {c}", "terms": r"منافس|المنافسين|علامات|شركات|competitor|brands",
                    "classes": {"news": 2.5, "market_report": 2, "commercial": 2, "official": 1}},
    "prices": {"ar": "الأسعار", "en": "prices", "q_ar": "سعر {s} في {c}", "q_en": "{s} price {c}",
               "terms": r"سعر|أسعار|الأسعار|تسعير|price|pricing", "numeric": True, "fresh": True,
               "classes": {"commercial": 3.5, "news": 1.5, "official": 1.5, "market_report": 1}},
    "channels": {"ar": "قنوات التوزيع", "en": "retail distribution", "q_ar": "توزيع {s} في {c} متاجر التجزئة",
                 "q_en": "{s} retail distribution channels {c}", "terms": r"توزيع|قنوات|متاجر|تجزئة|distribution|retail",
                 "classes": {"news": 2, "market_report": 2, "official": 1.5, "commercial": 1.5}},
    "costs": {"ar": "التكاليف", "en": "costs", "q_ar": "تكاليف إنتاج وتوزيع {s} في {c}", "q_en": "{s} production cost {c}",
              "terms": r"تكلفة|تكاليف|ميزانية|مالي|تمويل|cost|budget", "numeric": True,
              "classes": {"official": 2, "news": 2, "market_report": 1.5, "academic": 1.5, "commercial": 1}},
    "regulation": {"ar": "التنظيم والتراخيص", "en": "regulation requirements", "q_ar": "اشتراطات وتراخيص {s} في {c}",
                   "q_en": "{s} regulation requirements {c}", "terms": r"ترخيص|تراخيص|تنظيم|اشتراطات|لوائح|هيئة|regulation|license",
                   "official": True, "classes": {"official": 4, "news": 1.5, "market_report": 0.5}},
    "consumers": {"ar": "المستهلكون والسكان", "en": "consumers population", "q_ar": "شريحة مستهلكي {s} في {c}",
                  "q_en": "{s} consumer behavior {c}", "terms": r"مستهلك|المستهلكين|شريحة|عملاء|سكان|consumer",
                  "numeric": True, "official": True, "classes": {"official": 3, "academic": 2.5, "market_report": 2, "news": 1.5}},
    "trends": {"ar": "الاتجاهات والنمو", "en": "trends growth", "q_ar": "نمو سوق {s} في {c} اتجاهات",
               "q_en": "{s} market growth trends {c}", "terms": r"اتجاه|اتجاهات|نمو|trend|growth", "fresh": True,
               "classes": {"market_report": 2.5, "news": 2.5, "official": 2, "academic": 1.5}},
    "risks": {"ar": "المخاطر", "en": "risks challenges", "q_ar": "تحديات ومخاطر {s} في {c}", "q_en": "{s} market challenges {c}",
              "terms": r"مخاطر|تحديات|risk|challenge", "classes": {"news": 2, "academic": 2, "market_report": 2, "official": 1.5}},
    "technology": {"ar": "التقنية", "en": "software", "q_ar": "{s} تقنية", "q_en": "{s} open source",
                   "terms": r"تقنية|برمجيات|برنامج|مكتبة|كود|software|library|code|framework",
                   "classes": {"code": 3, "academic": 2, "news": 1, "commercial": 1}},
    "literature": {"ar": "الدراسات العلمية", "en": "research studies", "q_ar": "دراسة علمية {s}", "q_en": "{s} study",
                   "terms": r"دراسات|الدراسات|دراسة علمية|علمية|أبحاث|بحث علمي|أوراق علمية|academic|paper|studies|literature",
                   "classes": {"academic": 4, "official": 2}},
}
IMPLIED = [  # kinds of request that imply facets the user did not name
    (r"إطلاق|منتج جديد|فرصة|دراسة جدوى|launch|opportunity", ["market_size", "demand", "competitors", "consumers", "channels", "regulation"]),
    (r"تقرير مالي|خطة مالية|ميزانية|مالي|financial|budget", ["costs", "prices"]),
    (r"خطة تنفيذ|خطة عمل|execution plan", ["channels", "costs"]),
    (r"الطلب|demand", ["consumers"]),
]
REFUSALS = {"blocked", "access_wall", "rate_limited", "robots_disallowed", "robots_unreachable", "host_refused_access",
            "host_on_hold", "crawl_delay_too_long"}
NUMERIC_FACETS = {k for k, v in FACETS.items() if v.get("numeric")}

COUNTRIES = [  # (pattern, Arabic, English, ISO3)
    (r"السعودي|السعودية|المملكة|saudi|ksa", "السعودية", "Saudi Arabia", "SAU"),
    (r"الإمارات|الامارات|إماراتي|uae|emirates", "الإمارات", "UAE", "ARE"),
    (r"مصر|مصري|egypt", "مصر", "Egypt", "EGY"),
    (r"الكويت|كويتي|kuwait", "الكويت", "Kuwait", "KWT"),
    (r"قطر|قطري|qatar", "قطر", "Qatar", "QAT"),
    (r"الأردن|اردني|jordan", "الأردن", "Jordan", "JOR"),
    (r"عمان|عماني|oman", "عُمان", "Oman", "OMN"),
    (r"البحرين|bahrain", "البحرين", "Bahrain", "BHR"),
]
WB_INDICATORS = {"consumers": ["SP.POP.TOTL", "SP.URB.TOTL"], "market_size": ["NY.GDP.PCAP.CD"], "demand": ["SP.POP.1564.TO"],
                 "costs": ["FP.CPI.TOTL.ZG"]}
_STOP = set("""ادرس ادرسي لي فرصة إطلاق اطلاق منتج جديد في السوق سوق وابحث ابحث عن المنافسين وحلل حلل الأسعار والطلب
واكتب اكتب خطة تنفيذ وتقريرا وتقريرًا ماليا ماليًا وأعد أعد ملفا ملفًا نهائيا نهائيًا السعودي السعودية المملكة من إلى على مع هذا هذه
التي الذي ثم أو و ما كل بين حول دراسة تحليل تقرير المنتج الميزانية ميزانية ألف مليون ريال دولار بسعر صرف أي ما يعادل والميزانية
المستخدم الاختبار المنتج: هو هي لخص الدراسات العلمية دراسات أفضل اكتب قارن عن the a of in for and to with study analyze market new product""".split())


def detect_country(text: str):
    for pat, ar, en, iso in COUNTRIES:
        if re.search(pat, text or "", re.I):
            return {"ar": ar, "en": en, "iso": iso}
    return None


def extract_subject(goal: str) -> str:
    """The thing being studied, from the goal's own words (e.g. "قهوة باردة معلبة"). Explicit markers first."""
    g = re.sub(r"\s+", " ", goal or "")
    m = re.search(r"(?:المنتج(?: المستخدم في الاختبار)?|منتج|product)\s*[:：]\s*([^،,.\n؛;]{3,60})", g, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:إطلاق|اطلاق|بيع|launch)\s+(?!منتج جديد)([\u0600-\u06FFA-Za-z ]{3,40}?)\s+(?:في|بـ|ب|in)\s", g)
    if m and not re.fullmatch(r"\s*(منتج|خدمة)\s*", m.group(1)):
        return m.group(1).strip()
    words = [w for w in re.findall(r"[\u0600-\u06FFA-Za-z]{3,}", g) if w.lower() not in _STOP
             and not any(re.fullmatch(f["terms"], w, re.I) for f in FACETS.values())]
    return " ".join(words[:3])


def choose_facets(goal: str) -> dict:
    """{facet: "critical" | "supporting"} from the goal's words. Nothing matched: one general facet."""
    g = goal or ""
    out: dict[str, str] = {}
    for k, f in FACETS.items():
        if re.search(f["terms"], g, re.I):
            out[k] = "critical"
    for pat, implied in IMPLIED:
        if re.search(pat, g, re.I):
            for k in implied:
                out.setdefault(k, "supporting")
    if "market_size" in out and out["market_size"] == "critical" and not re.search(r"حجم|قيمة|market size", g, re.I):
        out["market_size"] = "supporting"  # "السوق السعودي" names the market, it does not ask for its size
    return out or {"overview": "critical"}


# ---------------------------------------------------------------- source classes
_CLASS_RULES = [
    ("social", r"(^|\.)(twitter|x|facebook|instagram|tiktok|youtube|reddit|linkedin|pinterest|snapchat)\.com$"),
    ("official", r"(\.gov(\.[a-z]{2})?|\.gob\.[a-z]{2}|\.go\.[a-z]{2}|^data\.worldbank\.org|worldbank\.org|imf\.org|oecd\.org|un\.org|who\.int|fao\.org|wto\.org|europa\.eu)$"),
    ("academic", r"(doi\.org|arxiv\.org|sciencedirect\.com|springer\.com|link\.springer\.com|mdpi\.com|wiley\.com|tandfonline\.com|"
                 r"researchgate\.net|ncbi\.nlm\.nih\.gov|nature\.com|frontiersin\.org|plos\.org|ssrn\.com|semanticscholar\.org|"
                 r"jstor\.org|emerald\.com|sagepub\.com|\.edu(\.[a-z]{2})?|\.ac\.[a-z]{2})$"),
    ("market_report", r"(statista\.com|mordorintelligence\.com|imarcgroup\.com|grandviewresearch\.com|euromonitor\.com|"
                      r"researchandmarkets\.com|kenresearch\.com|6wresearch\.com|expertmarketresearch\.com|fortunebusinessinsights\.com|"
                      r"marketsandmarkets\.com|techsciresearch\.com|blueweaveconsulting\.com|alliedmarketresearch\.com|giiresearch\.com)$"),
    ("news", r"(argaam\.com|aleqt\.com|arabnews\.com|reuters\.com|bloomberg\.com|zawya\.com|cnbc\.com|cnbcarabia\.com|alarabiya\.net|"
             r"sabq\.org|okaz\.com\.sa|alriyadh\.com|spa\.gov\.sa|asharqbusiness\.com|forbesmiddleeast\.com|gulfnews\.com|"
             r"thenationalnews\.com|ft\.com|wsj\.com|aawsat\.com|news\.google\.com|saudigazette\.com\.sa)$"),
    ("commercial", r"(amazon\.[a-z.]+|noon\.com|carrefourksa\.com|carrefour\.[a-z.]+|danube\.sa|tamimimarkets\.com|panda\.com\.sa|"
                   r"nahdionline\.com|lulu(hypermarket)?\.[a-z.]+|ninja\.sa|jahez\.net|hungerstation\.com|othaimmarkets\.com|"
                   r"barnsstore\.com|halfmillion\.com|starbucks\.[a-z.]+|nestle[a-z.-]*|almarai\.com)$"),
    ("encyclopedia", r"wikipedia\.org$"),
    ("code", r"(github\.com|gitlab\.com|pypi\.org|npmjs\.com)$"),
]


def source_class(url: str, hint: str = "") -> str:
    h = acc.host_of(url)
    for cls, pat in _CLASS_RULES:
        if re.search(pat, h, re.I):
            return cls
    if hint in ("academic", "code"):
        return hint
    if re.search(r"/(product|products|p|dp|item|shop|store)/|السعر|price", url, re.I):
        return "commercial"
    return "web"


def domain_of(url: str) -> str:
    h = acc.host_of(url)
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in ("com", "gov", "org", "net", "edu", "ac", "co"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------------------------------------------------------- extraction
_NUM = r"\d[\d,٬.]*(?:\.\d+)?"
_UNIT = (r"(?:%|٪|ريال|ر\.س|SAR|SR\b|دولار|USD|US\$|\$|مليون|مليار|ألف|billion|million|bn|mn|طن|tons?|لتر|liters?|كجم|kg|"
         r"سنويًا|سنويا|annually|CAGR)")
_FIGURE = re.compile(rf"[^.\n؟!]{{0,160}}?(?:{_NUM}\s*{_UNIT}|{_UNIT}\s*{_NUM})[^.\n؟!]{{0,120}}", re.I)


def figures(text: str, terms: list[str], limit: int = 8) -> list[str]:
    """Sentences that state a figure with a unit, preferring those that mention the facet or subject."""
    out = []
    for m in _FIGURE.finditer(text or ""):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if 20 <= len(s) <= 300 and s not in out:
            out.append(s)
        if len(out) >= 60:
            break
    scored = sorted(out, key=lambda s: -sum(1 for t in terms if t and t.lower() in s.lower()))
    return scored[:limit]


def relevant_tables(tables: list, terms: list[str], limit: int = 2, rows: int = 12) -> list:
    keep = []
    for t in tables or []:
        if not isinstance(t, list) or len(t) < 2:
            continue
        flat = " ".join(" ".join(str(c) for c in r) for r in t[:30]).lower()
        hits = sum(1 for x in terms if x and x.lower() in flat) + (1 if re.search(r"\d", flat) else 0)
        if hits:
            keep.append((hits, [[str(c)[:60] for c in r[:8]] for r in t[:rows]]))
    return [t for _, t in sorted(keep, key=lambda x: -x[0])[:limit]]


_MONEY = re.compile(rf"({_NUM})\s*(مليار|مليون|billion|million|bn|mn)?\s*(ريال|ر\.س|SAR|دولار|USD|US\$|\$)", re.I)


def money_values(text: str) -> list[float]:
    """Amounts in SAR (USD x 3.75) stated with a scale, for conflict checks on market size."""
    out = []
    for n, scale, cur in _MONEY.findall(text or ""):
        try:
            v = float(n.replace(",", "").replace("٬", ""))
        except ValueError:
            continue
        mult = {"مليار": 1e9, "billion": 1e9, "bn": 1e9, "مليون": 1e6, "million": 1e6, "mn": 1e6}.get((scale or "").lower(), 0)
        if not mult:
            continue
        v *= mult * (3.75 if cur.lower() in ("دولار", "usd", "us$", "$") else 1)
        out.append(v)
    return out


def _year(s: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group(0)) if m else None


# ---------------------------------------------------------------- the manager
class ResearchManager:
    def __init__(self, goal: str, emit=None, llm=None, budget: Budget | None = None, state: dict | None = None,
                 reuse: dict | None = None, persist=None, clock=time.monotonic, today: date | None = None):
        from .. import tools  # the same reader, search chain and World Bank client as the agents' tools
        self.tools = tools
        self.goal, self.emit_fn, self.llm, self.persist = goal, emit, llm, persist
        self.budget = budget or Budget()
        self.clock, self.today = clock, today or date.today()
        self.policy = acc.Access(fetch=lambda u: self.tools.fetch_raw(u, timeout=8.0))
        self._sem = asyncio.Semaphore(max(1, self.budget.concurrency))
        self._search_sem = asyncio.Semaphore(2)  # providers rate-limit bursts; two searches at a time
        self._open_locks: dict[str, asyncio.Lock] = {}
        self._texts: dict[str, str] = {}  # full text of readings, this process only (not persisted)
        self._t0 = clock()
        self.state = {"goal": goal[:2000], "facets": {}, "subject": {}, "country": None, "candidates": {}, "readings": {},
                      "searches": [], "rounds": 0, "used": {"searches": 0, "opens": 0, "agent_opens": 0, "seconds": 0.0},
                      "conflicts": [], "status": {}, "done": False, "next_id": 1, "reused_from": None, "budget": asdict(self.budget)}
        if state:  # resume after an interruption: what was opened is not opened again
            self.state.update(json.loads(json.dumps(state)))
            self.state["used"]["seconds"] = 0.0
        if reuse:  # a follow-up: evidence read by the previous turn stays valid and is not opened again
            self._adopt(reuse)
        self.policy.load(self.state.get("access") or {})

    # ------------------------------------------------------------ plumbing
    async def emit(self, type_: str, data: dict):
        if self.emit_fn:
            await self.emit_fn(type_, data)

    def elapsed(self) -> float:
        return self.clock() - self._t0

    def out_of_time(self) -> bool:
        return self.elapsed() >= self.budget.max_seconds

    def _adopt(self, prev: dict):
        n = 0
        for k, rd in (prev.get("readings") or {}).items():
            if k not in self.state["readings"] and not rd.get("failure"):
                rd = dict(rd)
                rd["from_previous"] = True
                rd["id"] = f"E{self.state['next_id']}"
                self.state["next_id"] += 1
                self.state["readings"][k] = rd
                n += 1
        self.state["reused_from"] = {"readings": n}
        if not self.state["subject"] and prev.get("subject"):
            self.state["subject"] = prev["subject"]
            self.state["country"] = prev.get("country")

    def to_dict(self) -> dict:
        self.state["access"] = self.policy.to_dict()
        self.state["used"]["seconds"] = round(self.state["used"]["seconds"] + 0, 1)
        return self.state

    # ------------------------------------------------------------ planning
    async def plan(self):
        st = self.state
        if st["facets"]:
            return
        st["facets"] = choose_facets(self.goal)
        st["country"] = st.get("country") or detect_country(self.goal)
        subj = st.get("subject") or {}
        if not subj.get("ar"):
            subj = {"ar": extract_subject(self.goal), "en": ""}
        st["subject"] = subj
        queries = await self._llm_queries() if self.llm else {}
        c = st["country"] or {"ar": "", "en": ""}
        st["queries"] = {}
        for k in st["facets"]:
            f = FACETS.get(k) or {"q_ar": "{s} {c}", "q_en": "{s} {c}"}
            got = queries.get(k) or {}
            ar = got.get("ar") or f["q_ar"].format(s=subj["ar"], c=c["ar"])
            en = got.get("en") or (f["q_en"].format(s=subj["en"], c=c["en"]) if subj.get("en") else "")
            st["queries"][k] = [q for q in (_clean_q(ar), _clean_q(en)) if q]
        await self.emit("research_plan", {"facets": st["facets"], "subject": subj, "country": st["country"],
                                          "queries": st["queries"], "budget": st["budget"]})

    async def _llm_queries(self) -> dict:
        """One model call for search phrasing (Arabic and English) and the subject's English name. Optional:
        on any error or invalid answer the deterministic templates are used. It cannot add facets."""
        facets = list(self.state["facets"])
        prompt = ("اكتب استعلامات بحث قصيرة (3–7 كلمات) كما يكتبها إنسان في محرك بحث، لكل محور من المحاور المذكورة فقط، "
                  "بالعربية والإنجليزية، عن موضوع الطلب نفسه. لا روابط ولا أرقام مختلقة. أعد JSON فقط بالشكل: "
                  '{"subject_ar": "...", "subject_en": "...", "queries": {"<facet>": {"ar": "...", "en": "..."}}}')
        try:
            msg = await asyncio.wait_for(self.llm.chat_json([
                {"role": "system", "content": "أنت مخطط بحث. " + prompt},
                {"role": "user", "content": f"الطلب: {self.goal[:1500]}\nالمحاور: {', '.join(facets)}"}],
                temperature=0.0, max_tokens=700), timeout=120)
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(msg, dict):
            return {}
        se, sa = _clean_q(msg.get("subject_en", ""), 60), _clean_q(msg.get("subject_ar", ""), 60)
        if se and re.fullmatch(r"[A-Za-z0-9 .&'-]{3,60}", se):
            self.state["subject"]["en"] = se
        if sa and not self.state["subject"].get("ar"):
            self.state["subject"]["ar"] = sa
        out = {}
        for k, v in (msg.get("queries") or {}).items():
            if k in self.state["facets"] and isinstance(v, dict):
                out[k] = {"ar": _clean_q(v.get("ar", "")), "en": _clean_q(v.get("en", ""))}
        return out

    # ------------------------------------------------------------ discovery
    def _subject_words(self) -> list[str]:
        """The subject's head words (Arabic and English): a page that never mentions them is not about it."""
        s = self.state.get("subject") or {}
        out = []
        for part in (s.get("ar", ""), s.get("en", "")):
            ws = [w for w in re.findall(r"[\w\u0600-\u06FF]{3,}", part.lower()) if w not in _STOP]
            out += [w for w in ws if len(w) >= 4 or re.search(r"[\u0600-\u06FF]", w)]
        return list(dict.fromkeys(out))

    def _terms(self, facet: str) -> list[str]:
        f = FACETS.get(facet) or {}
        s = self.state["subject"]
        words = re.findall(r"[\w\u0600-\u06FF]{3,}", f"{s.get('ar', '')} {s.get('en', '')}".lower())
        return [w for w in words if w not in _STOP] + [t for t in re.split(r"\|", f.get("terms", "")) if len(t) > 2][:4]

    async def _add_candidates(self, facet: str, results: list[dict], provider: str, query: str):
        for i, x in enumerate(results or []):
            u = str(x.get("url") or "")
            if not u.startswith("http"):
                continue
            k = normalize_url(u)
            c = self.state["candidates"].get(k)
            if not c:
                c = {"url": u, "title": str(x.get("title") or "")[:200], "snippet": str(x.get("snippet") or "")[:500],
                     "provider": x.get("provider") or provider, "also": [], "published": str(x.get("published") or "")[:20],
                     "publisher": str(x.get("publisher") or "")[:80], "hint": x.get("source_type") or "",
                     "abstract": str(x.get("abstract") or "")[:3000], "fulltext": list(x.get("fulltext") or [])[:3],
                     "doi": x.get("doi") or "", "facets": [], "rank": i, "headline_only": bool(x.get("headline_only"))
                     or u.startswith("https://news.google.com/")}
                self.state["candidates"][k] = c
                if len(self.state["candidates"]) <= 150:
                    await self.emit("evidence", {"stage": "discovered", "url": u, "title": c["title"],
                                                 "provider": c["provider"], "facets": [facet], "query": query[:120]})
            elif provider not in c["also"] and provider != c["provider"]:
                c["also"].append(provider)
            if facet not in c["facets"]:
                c["facets"].append(facet)

    async def _search(self, facet: str, query: str, kinds: tuple[str, ...]):
        st = self.state
        if not query or st["used"]["searches"] >= self.budget.max_searches or self.out_of_time():
            return
        for kind in kinds:
            if st["used"]["searches"] >= self.budget.max_searches:
                return
            st["used"]["searches"] += 1
            entry = {"facet": facet, "query": query, "kind": kind, "results": 0, "provider": "", "error": ""}
            await self._search_sem.acquire()
            try:
                if kind == "web":
                    res, src, err = await self.tools.search_results(query)
                    entry.update(results=len(res), provider=src.split(" — ")[0], error=err)
                    await self._add_candidates(facet, res, entry["provider"] or "web", query)
                else:
                    names = specialized.available(kind)
                    got = await registry.fan_out(names, query) if names else {}
                    ok = {n: r for n, r in got.items() if r}
                    entry.update(results=sum(len(r) for r in ok.values()), provider=" + ".join(ok) or "",
                                 error="" if ok else ("no_provider" if not names else "no_results"))
                    for n, r in ok.items():
                        await self._add_candidates(facet, r, n, query)
            except Exception as e:  # noqa: BLE001  one provider failing must not stop the run
                entry["error"] = type(e).__name__
            finally:
                self._search_sem.release()
            st["searches"].append(entry)
            await self.emit("research_search", entry)

    async def _official(self, facet: str):
        """World Bank indicators: official, keyless figures recorded as read evidence."""
        c = self.state.get("country")
        if not c:
            return
        for ind in WB_INDICATORS.get(facet, []):
            url = f"https://data.worldbank.org/indicator/{ind}?locations={c['iso']}"
            k = normalize_url(url)
            if k in self.state["readings"]:
                rd = self.state["readings"][k]
                if facet not in rd["facets"]:
                    rd["facets"].append(facet)
                continue
            if self.state["used"]["searches"] >= self.budget.max_searches:
                return
            self.state["used"]["searches"] += 1
            txt = await self.tools.world_bank(f"{c['iso']} {ind}")
            ok = txt.startswith(self.tools.READ_MARK)
            page = txt[len(self.tools.READ_MARK):].split("]", 1)[0] if ok else url
            rd = self._reading(page, facet, {
                "status": 200 if ok else None, "failure": None if ok else ("not_found" if "لا بيانات" in txt else "network_error"),
                "method": "api:worldbank", "kind": "json", "ctype": "application/json", "title": (txt.split("\n")[1] if ok else "")[:160],
                "publisher": "World Bank", "text": txt.split("\n", 1)[-1] if ok else "", "tables": [], "completeness": "full" if ok else ""},
                cls="official", provider="World Bank")
            await self._record(rd, task=None)

    # ------------------------------------------------------------ ranking
    def score(self, c: dict, facet: str, chosen_domains: dict) -> float:
        f = FACETS.get(facet) or {"classes": {"official": 2, "academic": 1.5, "news": 1.5, "market_report": 1.5, "commercial": 1}}
        cls = source_class(c["url"], c.get("hint", ""))
        text = f"{c['title']} {c['snippet']}".lower()
        terms = self._terms(facet)
        rel = min(4.0, sum(1.0 for t in terms if t.lower() in text))
        s = rel * 1.5 + f["classes"].get(cls, 0.5)
        if cls in ("social",):
            s -= 4
        if cls == "encyclopedia":
            s -= 1
        y = _year(c.get("published") or "") or _year(c["snippet"][:40])
        age = (self.today.year - y) if y else None
        if f.get("fresh") and age is not None:
            s += 1.5 if age <= 1 else 0.8 if age <= 3 else -1.0 if age > 5 else 0
        if re.search(rf"{_NUM}\s*{_UNIT}", text, re.I):
            s += 1.0
        if re.search(r"جدول|إحصاء|إحصائيات|بيانات|table|statistics|data|report|تقرير", text, re.I):
            s += 0.5
        s += 0.4 * min(2, len(c.get("also") or []))  # found independently by several providers
        d = domain_of(c["url"])
        s -= 1.5 * chosen_domains.get((facet, d), 0) + 0.8 * max(0, chosen_domains.get(("*", d), 0) - 1)
        s -= 0.02 * int(c.get("rank") or 0)  # tie-break only: a search engine's order is not reliability
        return round(s, 3)

    def pick(self, facet: str, limit: int) -> list[dict]:
        st = self.state
        chosen_domains: dict = {}
        for rd in st["readings"].values():
            d = domain_of(rd["url"])
            chosen_domains[("*", d)] = chosen_domains.get(("*", d), 0) + 1
            if facet in rd.get("facets", []):
                chosen_domains[(facet, d)] = chosen_domains.get((facet, d), 0) + 1
        pool = []
        for k, c in st["candidates"].items():
            if facet not in c["facets"] or k in st["readings"] or c.get("tried"):
                continue
            if c["headline_only"] and not c.get("abstract"):
                continue  # a headline is not a page; it stays "discovered"
            if not self.policy.allowed_host(c["url"]):
                continue
            pool.append((self.score(c, facet, chosen_domains), k, c))
        out = []
        for s, k, c in sorted(pool, key=lambda x: -x[0]):
            d = domain_of(c["url"])
            if chosen_domains.get((facet, d), 0) >= 1 and len(pool) > limit:
                continue  # independence: one more source from another domain before a second one from the same
            chosen_domains[(facet, d)] = chosen_domains.get((facet, d), 0) + 1
            out.append(c)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ opening
    def _reading(self, url: str, facet: str | None, rd: dict, cls: str = "", provider: str = "") -> dict:
        terms = self._terms(facet) if facet else self._terms("overview")
        text = rd.get("text") or ""
        ex = self.tools.excerpt(text, " ".join(terms), 1400) if text else ""
        if not ex and text and rd.get("completeness") == "summary":
            ex = text[:1200]
        if not ex and text and rd.get("method") == "api:worldbank":
            ex = text[:1200]
        figs = figures(text, terms)
        tabs = relevant_tables(rd.get("tables") or [], terms)
        offers = [{"name": str(o.get("name", ""))[:80], "price": o.get("price"), "currency": o.get("currency", "")}
                  for o in (rd.get("offers") or [])[:8]]
        subj = self._subject_words()
        hay = (text + " " + (rd.get("title") or "")).lower()
        arabic_page = bool(re.search(r"[\u0600-\u06FF]", re.sub(r"\[صفحة \d+\]|ورقة:", "", hay[:20000])))
        subj = [w for w in subj if bool(re.search(r"[\u0600-\u06FF]", w)) == arabic_page]  # judge in the page's script
        about = rd.get("method") == "api:worldbank" or not subj or any(w in hay for w in subj)
        useful = bool(not rd.get("failure") and about and (len(ex) >= 60 or figs or tabs or offers))
        k = normalize_url(url)
        prev = self.state["readings"].get(k) or {}
        out = {"id": prev.get("id") or "", "url": url, "facets": sorted(set((prev.get("facets") or []) + ([facet] if facet else []))),
               "title": (rd.get("title") or "")[:200], "publisher": (rd.get("publisher") or "")[:80],
               "published": (rd.get("published") or rd.get("modified") or rd.get("last_modified") or "")[:40],
               "status": rd.get("status"), "failure": rd.get("failure"), "method": rd.get("method") or "",
               "kind": rd.get("kind") or "", "ctype": (rd.get("ctype") or "")[:60], "completeness": rd.get("completeness") or "",
               "pages": f"{rd.get('pages_read')}/{rd.get('pages_total')}" if rd.get("pages_total") else "",
               "ocr_pages": len(rd.get("ocr_pages") or []), "tables_total": len(rd.get("tables") or []),
               "tables": tabs, "offers": offers, "figures": figs, "excerpt": ex[:1400], "chars": len(text),
               "hash": rd.get("hash") or "", "class": cls or source_class(url), "domain": domain_of(url),
               "provider": provider, "useful": useful, "notes": [str(n)[:120] for n in (rd.get("notes") or [])][:4],
               "wall": rd.get("wall") or ""}
        if text:
            self._texts[k] = text
        return out

    async def _record(self, rd: dict, task: str | None):
        st = self.state
        k = normalize_url(rd["url"])
        if not rd.get("failure"):
            if not rd.get("id"):
                rd["id"] = f"E{st['next_id']}"
                st["next_id"] += 1
            st["readings"][k] = rd
        else:
            st.setdefault("failed", {})[k] = {kk: rd.get(kk) for kk in ("url", "status", "failure", "method", "facets", "wall")}
        ev = {"stage": "opened", "url": rd["url"], "title": rd.get("title"), "facets": rd.get("facets"),
              "status": rd.get("status"), "failure": rd.get("failure"), "method": rd.get("method"), "kind": rd.get("kind"),
              "ctype": rd.get("ctype"), "tables": rd.get("tables_total"), "hash": rd.get("hash"), "completeness": rd.get("completeness"),
              "pages": rd.get("pages"), "chars": rd.get("chars"), "useful": rd.get("useful"), "publisher": rd.get("publisher"),
              "published": rd.get("published"), "provider": rd.get("provider"), "source_class": rd.get("class")}
        if task:
            ev["task"] = task
        await self.emit("evidence", ev)

    async def open_url(self, url: str, facet: str | None, provider: str = "", task: str | None = None,
                       hint: str = "", agent: bool = False) -> dict:
        """Open one URL once per run (concurrent callers wait for the first), within budget and access rules."""
        k = normalize_url(url)
        lock = self._open_locks.setdefault(k, asyncio.Lock())
        async with lock:
            st = self.state
            if k in st["readings"]:
                rd = st["readings"][k]
                if facet and facet not in rd["facets"]:
                    rd["facets"].append(facet)
                return rd
            if k in (st.get("failed") or {}):
                return st["failed"][k]
            used = "agent_opens" if agent else "opens"
            cap = self.budget.max_agent_opens if agent else self.budget.max_opens
            if st["used"][used] >= cap or (not agent and self.out_of_time()):
                return {"url": url, "failure": "budget_exhausted", "facets": [facet] if facet else []}
            why = await self.policy.check(url, crawl=False)
            if why:
                rd = self._reading(url, facet, {"failure": why, "method": "robots"}, provider=provider)
                await self._record(rd, task)
                return rd
            st["used"][used] += 1
            async with self._sem:
                try:
                    raw = await asyncio.wait_for(self.tools.read_url(url), timeout=self.budget.open_timeout)
                except asyncio.TimeoutError:
                    raw = {"url": url, "status": None, "failure": "timeout", "method": "httpx"}
                except Exception as e:  # noqa: BLE001  a broken page never breaks the run
                    raw = {"url": url, "status": None, "failure": "network_error", "notes": [type(e).__name__]}
            self.policy.record(url, raw.get("failure"), raw.get("retry_after"))
            rd = self._reading(raw.get("url") or url, facet, raw, cls=source_class(url, hint), provider=provider)
            if normalize_url(rd["url"]) != k:  # redirected: remember under the asked URL too
                rd["asked"] = url
            await self._record(rd, task)
            if not rd.get("failure") and normalize_url(rd["url"]) != k:
                st["readings"][k] = rd
            return rd

    async def open_candidate(self, c: dict, facet: str, task: str | None = None) -> dict:
        """Open a candidate by the best legal route: a scholarly record's full-text link or open copy, then
        its landing page (DOI), and when all are closed, its abstract — recorded as a summary only."""
        c["tried"] = True
        routes = list(c.get("fulltext") or [])
        if c.get("doi"):
            try:
                routes += [u for u in await specialized.open_access(c["doi"]) if u not in routes]
            except Exception:  # noqa: BLE001
                pass
        routes.append(c["url"])
        last = None
        for u in routes[:4]:
            rd = await self.open_url(u, facet, provider=c.get("provider", ""), task=task, hint=c.get("hint", ""))
            if not rd.get("failure") and rd.get("useful", True):
                return rd
            last = rd
            if rd.get("failure") == "budget_exhausted":
                break
        if c.get("abstract") and len(c["abstract"]) >= 80:
            k = normalize_url(c["url"])
            rd = self._reading(c["url"], facet, {"status": None, "failure": None, "method": f"api:{(c.get('provider') or 'index').lower()}:abstract",
                                                 "kind": "abstract", "title": c.get("title"), "publisher": c.get("publisher"),
                                                 "published": c.get("published"), "text": c["abstract"], "completeness": "summary"},
                               cls="academic", provider=c.get("provider", ""))
            rd["notes"] = (rd.get("notes") or []) + ([f"النص الكامل لم يُفتح: {last.get('failure')}"] if last and last.get("failure") else [])
            st = self.state
            st.get("failed", {}).pop(k, None)
            await self._record(rd, task)
            return rd
        return last or {"url": c["url"], "failure": "not_opened"}

    # ------------------------------------------------------------ the loop
    def facet_status(self, facet: str) -> dict:
        st = self.state
        cands = [c for c in (st.get("candidates") or {}).values() if facet in c["facets"]]
        reads = [r for r in st["readings"].values() if facet in r.get("facets", [])]
        fails = [r for r in (st.get("failed") or {}).values() if facet in (r.get("facets") or [])
                 and r.get("method") != "api:worldbank"]  # "no indicator value" is not a failed source for the facet
        useful = [r for r in reads if r.get("useful")]
        domains = {r["domain"] for r in useful}
        figs = sum(len(r.get("figures") or []) + len(r.get("offers") or []) + len(r.get("tables") or []) for r in useful)
        official = any(r.get("class") == "official" for r in useful)
        f = FACETS.get(facet, {})
        crit = st["facets"].get(facet) == "critical"
        need_sources = 2 if crit else 1
        sufficient = (len(useful) >= need_sources and len(domains) >= min(need_sources, 2)
                      and (not f.get("numeric") or figs > 0)
                      and (not (f.get("official") and crit and st.get("country")) or official))
        conflict = any(x["facet"] == facet and not x.get("resolved") for x in st.get("conflicts") or [])
        if sufficient:
            label = "sufficient"
        elif useful:
            label = "useful"
        elif reads:
            label = "opened_not_useful"
        elif fails and all(r.get("failure") in REFUSALS for r in fails):
            label = "refused"
        elif cands and not fails:
            label = "not_opened"
        elif fails:
            label = "failed"
        else:
            label = "no_results"
        return {"status": label, "conflicting": conflict, "candidates": len(cands), "read": len(reads),
                "useful": len(useful), "domains": len(domains), "figures": figs, "official": official,
                "failed": len(fails), "critical": crit}

    def _detect_conflicts(self):
        st = self.state
        for facet in ("market_size",):
            vals = []
            for r in st["readings"].values():
                if facet in r.get("facets", []) and r.get("useful"):
                    for fig in r.get("figures") or []:
                        if re.search(r"سوق|market", fig, re.I):
                            for v in money_values(fig):
                                vals.append((v, r["id"], r["domain"]))
            if len({d for _, _, d in vals}) >= 2:
                lo, hi = min(vals), max(vals)
                if lo[0] > 0 and hi[0] / lo[0] > 2 and lo[2] != hi[2]:
                    key = (facet, lo[1], hi[1])
                    if not any((x["facet"], x["low"]["id"], x["high"]["id"]) == key for x in st["conflicts"]):
                        st["conflicts"].append({"facet": facet, "low": {"sar": lo[0], "id": lo[1]}, "high": {"sar": hi[0], "id": hi[1]},
                                                "ratio": round(hi[0] / lo[0], 2), "resolved": False,
                                                "note": "تقديرات حجم السوق تختلف بأكثر من الضعف بين مصدرين مستقلين"})

    def _round_queries(self, facet: str, rnd: int) -> list[tuple[str, tuple]]:
        st = self.state
        qs = st["queries"].get(facet) or []
        s, c = st["subject"], st.get("country") or {"ar": "", "en": ""}
        f = FACETS.get(facet, {})
        academic = facet in ("literature", "demand", "consumers", "market_size", "risks")
        out = []
        crit = st["facets"].get(facet) == "critical"
        if rnd == 1:
            if qs:
                out.append((qs[0], ("web",)))
            if len(qs) > 1 and not crit:  # supporting facets: one web query first; academic indexes only if relevant
                if academic:
                    out.append((qs[1], ("academic",)))
            elif len(qs) > 1:
                out.append((qs[1], ("web",) + (("academic",) if academic else ()) + (("code",) if facet == "technology" else ())))
            elif facet in ("literature", "technology") and qs:
                out.append((qs[0], ("academic",) if facet == "literature" else ("code",)))
        elif rnd == 2:
            short = _clean_q(f"{s.get('ar', '')} {f.get('ar', '')}")
            if short and short not in qs:
                out.append((short, ("web",)))
            if facet == "regulation" and c.get("en") == "Saudi Arabia":
                out.append((_clean_q(f"{s.get('ar', '')} اشتراطات site:gov.sa"), ("web",)))
            if s.get("en") and len(qs) < 2:
                out.append((_clean_q(f"{s['en']} {f.get('en', '')} {c.get('en', '')}"), ("web",)))
        else:
            if s.get("en"):
                out.append((_clean_q(f"{s['en']} {f.get('en', '')}"), ("web",) + (("academic",) if academic else ())))
            else:
                out.append((_clean_q(f"{f.get('ar', '')} {c.get('ar', '')} {s.get('ar', '').split(' ')[0] if s.get('ar') else ''}"), ("web",)))
        return [(q, k) for q, k in out if q]

    async def run(self) -> dict:
        """Rounds of discovery and opening until every critical facet is sufficient or the budget ends."""
        st = self.state
        await self.plan()
        start_round = st["rounds"]
        for rnd in range(start_round + 1, self.budget.max_rounds + 1):
            todo = [f for f in st["facets"] if self.facet_status(f)["status"] != "sufficient"]
            if not todo or self.out_of_time():
                break
            st["rounds"] = rnd
            await self.emit("research_round", {"round": rnd, "facets": todo})
            jobs = []
            for f in todo:
                for q, kinds in self._round_queries(f, rnd):
                    jobs.append(self._search(f, q, kinds))
                if rnd == 1:
                    jobs.append(self._official(f))
            await asyncio.gather(*jobs)
            opens = []
            for f in todo:
                have = sum(1 for r in st["readings"].values() if f in r.get("facets", []) and r.get("useful"))
                n = max(0, self.budget.per_facet_opens - (have if rnd == 1 else 0))
                if rnd > 1:
                    n = min(n, 2)
                for c in self.pick(f, n):
                    opens.append(self.open_candidate(c, f))
            if opens:
                await asyncio.gather(*opens)
            self._detect_conflicts()
            st["status"] = {f: self.facet_status(f) for f in st["facets"]}
            st["used"]["seconds"] = round(self.elapsed(), 1)
            await self.emit("research_round_done", {"round": rnd, "status": {k: v["status"] for k, v in st["status"].items()},
                                                    "used": st["used"]})
            if self.persist:
                try:
                    await self.persist()
                except Exception:  # noqa: BLE001
                    pass
        st["status"] = {f: self.facet_status(f) for f in st["facets"]}
        st["done"] = True
        st["used"]["seconds"] = round(self.elapsed(), 1)
        await self.emit("research_done", {"status": {k: v["status"] for k, v in st["status"].items()},
                                          "readings": len(st["readings"]), "used": st["used"],
                                          "conflicts": len(st["conflicts"])})
        return st

    # ------------------------------------------------------------ agents' tools, through the manager
    async def agent_tool(self, name: str, args, allowed: set[str], task: str | None):
        """(text, read urls or None). None keeps the agent's own reading of the result."""
        val = args.get("url") or args.get("query") if isinstance(args, dict) else args
        if val is None and isinstance(args, dict) and len(args) == 1:
            val = next(iter(args.values()))
        val = str(val or "")
        if name not in allowed:
            return await self.tools.call_tool(name, args, allowed), None
        if name == "fetch_url":
            rd = await self.open_url(val.strip(), None, provider="agent", task=task, agent=True)
            return self.render_reading(rd), ([rd["url"]] if not rd.get("failure") else [])
        if name == "web_search":
            q = re.sub(r"\s+", " ", val).strip()[:200]
            res, src, err = await self.tools.search_results(q)
            if err or not res:
                return await self.tools.web_search(q), None
            facet = "agent"
            await self._add_candidates(facet, res, src.split(" — ")[0], q)
            lines = [f"المصدر: {src}"] + [self.tools._result_line(i, r, " + " in src) for i, r in enumerate(res, 1)]
            read = await self._agent_reads([self.state["candidates"][normalize_url(r["url"])] for r in res
                                            if str(r.get("url", "")).startswith("http")], q, task, 2)
            return self._listing(lines, read), [rd["url"] for rd in read]
        if name == "research_sources":
            kind, _, q = val.partition(":")
            kind, q = kind.strip().lower(), re.sub(r"\s+", " ", q).strip()[:200]
            if kind not in specialized.KINDS or not q:
                return await self.tools.research_sources(val), None
            names = specialized.available(kind)
            got = await registry.fan_out(names, q) if names else {}
            ok = {n: r for n, r in got.items() if r}
            if not ok:
                return await self.tools.research_sources(val), None
            for n, r in ok.items():
                await self._add_candidates("agent", r, n, q)
            merged = registry.merge(list(ok.values()))[:10]
            lines = [f"المصدر: {' + '.join(ok)}"] + [self.tools._result_line(i, r, True) for i, r in enumerate(merged, 1)]
            read = await self._agent_reads([self.state["candidates"][normalize_url(r["url"])] for r in merged], q, task, 2)
            return self._listing(lines, read, index=True), [rd["url"] for rd in read]
        return await self.tools.call_tool(name, args, allowed), None

    async def _agent_reads(self, cands: list[dict], query: str, task, n: int) -> list[dict]:
        out = []
        for c in cands:
            if len(out) >= n:
                break
            k = normalize_url(c["url"])
            if k in self.state["readings"]:
                out.append(self.state["readings"][k])
                continue
            if c["headline_only"] or "wikipedia.org" in c["url"] or source_class(c["url"]) == "social":
                continue
            rd = await self.open_candidate(c, "agent", task=task)
            if not rd.get("failure"):
                out.append(rd)
        return out

    def _listing(self, lines: list[str], read: list[dict], index: bool = False) -> str:
        if index:
            lines.append("\nهذه قوائم من فهارس متخصصة. ما فتحه النظام منها وقرأه فعلًا مذكور أدناه بنوع قراءته؛ "
                         "الملخص (abstract) ليس نص الدراسة كاملًا.")
        if read:
            lines.append("\nمقتطفات من صفحات فتحها النظام فعلًا (استشهد بالرابط عند استخدام رقم منها):")
            for rd in read:
                lines.append(f"{self.tools.READ_MARK}{rd['url']}]\n{_completeness_ar(rd)}\n{rd.get('excerpt') or ''}")
        return self.tools._clip("\n".join(lines), 8000)

    def render_reading(self, rd: dict, max_chars: int = 12000) -> str:
        """A reading in fetch_url's format (so the ledger parser and agents read it the same way)."""
        if rd.get("failure"):
            if rd.get("status"):
                return f"HTTP {rd['status']} — {rd['url']}\n[لم يُقرأ: {rd['failure']}]"
            msg = {"unsafe_address": "خطأ: الرابط يشير إلى عنوان داخلي/خاص.", "invalid_url": "خطأ: رابط غير صالح (http/https فقط).",
                   "timeout": "خطأ في الجلب: Timeout"}.get(rd["failure"], f"تعذر الفتح: {rd['failure']}")
            return msg
        bits = []
        if rd.get("publisher"):
            bits.append(f"الناشر: {rd['publisher']}")
        if rd.get("published"):
            bits.append(f"تاريخ النشر: {rd['published']}")
        if rd.get("kind") and rd["kind"] not in ("html",):
            bits.append(f"نوع: {rd['kind']}")
        if rd.get("pages"):
            bits.append(f"صفحات: {rd['pages']}")
        bits += [f"اكتمال: {rd.get('completeness') or 'full'}", f"جداول: {rd.get('tables_total') or 0}", f"بصمة: {rd.get('hash') or ''}"]
        head = [f"HTTP {rd.get('status') or 200} — {rd['url']}", self.tools.PAGE_MARK + " | ".join(bits) + "]", _completeness_ar(rd)]
        if rd.get("tables"):
            head.append(self.tools.rx.tables_markdown(rd["tables"], 2000))
        text = self._texts.get(normalize_url(rd["url"])) or rd.get("excerpt") or ""
        return self.tools._clip("\n".join(head) + "\n\n" + text, max_chars)

    # ------------------------------------------------------------ the pack handed to agents
    def evidence_for(self, text: str) -> list[dict]:
        st = self.state
        want = [f for f in st["facets"] if re.search(FACETS.get(f, {}).get("terms", "^$"), text or "", re.I)]
        reads = [r for r in st["readings"].values() if r.get("useful")]
        if want:
            sel = [r for r in reads if set(r.get("facets", [])) & set(want)]
            if sel:
                return sel
        return reads

    def pack(self, task_text: str = "", max_chars: int = 9000) -> str:
        st = self.state
        reads = self.evidence_for(task_text)
        if not st["facets"] and not st["readings"]:
            return ""
        lines = ["## أدلة قرأها النظام فعلًا لهذه المهمة (Research Manager)",
                 "كل بند أدناه صفحة أو مستند فتحه النظام بنفسه واستخرج منه النص. استشهد بجانب كل رقم تأخذه برقم الدليل ورابطه الكامل معًا، مثل: [E3](https://example.com/page). لا تكتب رقم الدليل وحده.",
                 "«ملخص فقط» يعني أن النظام قرأ ملخص الدراسة لا نصها الكامل: لا تنسب إلى الدراسة ما ليس في الملخص.",
                 "إن لم تجد في هذه الأدلة رقمًا تحتاجه (سعرًا أو حجم سوق أو طلبًا) فاكتب «غير متاح في الأدلة المقروءة» ولا تخمّنه ولا تولّد رقمًا بديلًا؛ "
                 "الافتراض مسموح فقط لمعامل يقرره المستخدم (مثل توزيع الميزانية) ويُوسم (افتراض).", ""]
        size = sum(len(x) for x in lines)
        order = {f: i for i, f in enumerate(st["facets"])}
        reads = sorted(reads, key=lambda r: (min([order.get(f, 99) for f in r.get("facets", [])] or [99]),
                                             -len(r.get("figures") or []), r.get("id", "")))
        for r in reads:
            block = [f"[{r['id']}] {r.get('title') or r['domain']} — {r.get('publisher') or r['domain']}"
                     + (f" — {r['published']}" if r.get("published") else "") + f"\n   {r['url']}",
                     f"   المحاور: {', '.join(FACETS.get(f, {}).get('ar', f) for f in r.get('facets', []))} | {_completeness_ar(r)}"
                     + (" | من الدراسة السابقة" if r.get("from_previous") else "")]
            if r.get("figures"):
                block.append("   أرقام كما وردت: " + " ؛ ".join(r["figures"][:5]))
            if r.get("offers"):
                block.append("   عروض منظمة: " + " ؛ ".join(f"{o['name']} = {o['price']} {o['currency']}" for o in r["offers"][:5]))
            if r.get("excerpt"):
                block.append("   مقتطف: " + re.sub(r"\s*\n\s*", " / ", r["excerpt"])[:700])
            if r.get("tables"):
                block.append(self.tools.rx.tables_markdown(r["tables"][:1], 900))
            b = "\n".join(block)
            if size + len(b) > max_chars:
                lines.append(f"(أدلة إضافية مقروءة لم تُعرض لضيق المساحة: {len(reads) - reads.index(r)})")
                break
            lines.append(b)
            size += len(b)
        gaps = self.gaps()
        if gaps:
            lines += ["", "### نواقص البحث (لا تملأها بتخمين؛ اذكرها في ناتجك)"] + [f"- {g}" for g in gaps]
        for x in st["conflicts"]:
            if not x.get("resolved"):
                lines.append(f"- تعارض: {x['note']} ({x['low']['id']} مقابل {x['high']['id']}). اعرض القيمتين ومصدريهما ولا تختر واحدة دون تبرير.")
        return "\n".join(lines)

    _EID = re.compile(r"(?<![\w\[/])[\[(]?\bE(\d{1,3})\b[\])]?(?!\]?\()")

    def _by_id(self) -> dict:
        return {r["id"]: r for r in self.state["readings"].values() if r.get("id")}

    def link_ids(self, text: str) -> str:
        """Agents often cite evidence as "E15" or "(E15)": turn each into a link to the page the system read,
        so the reviewer and the final answer see the real source. Unknown ids are left as written."""
        ids = self._by_id()

        def sub(m):
            r = ids.get(f"E{m.group(1)}")
            return f"[E{m.group(1)}]({r['url']})" if r else m.group(0)
        return self._EID.sub(sub, text or "")

    def verifier_evidence(self, output: str, task_text: str = "", max_chars: int = 3500) -> str:
        """What the reviewer needs to judge figures: the evidence the output cites (by id or URL) and the
        evidence given to the task, each with the figures and a short excerpt exactly as read."""
        ids = self._by_id()
        cited = [ids[f"E{n}"] for n in dict.fromkeys(re.findall(r"\bE(\d{1,3})\b", output or "")) if f"E{n}" in ids]
        low = (output or "").lower()
        cited += [r for r in ids.values() if r not in cited and r["url"].lower().rstrip("/") in low]
        rest = [r for r in self.evidence_for(task_text) if r not in cited]
        out, size = [], 0
        for r in cited + rest:
            b = f"[{r['id']}] {r['url']} | {_completeness_ar(r)}"
            if r.get("figures"):
                b += "\n  أرقام كما وردت: " + " ؛ ".join(r["figures"][:4])
            if r.get("offers"):
                b += "\n  عروض: " + " ؛ ".join(f"{o['name']} = {o['price']} {o['currency']}" for o in r["offers"][:3])
            if r.get("excerpt"):
                b += "\n  مقتطف: " + re.sub(r"\s+", " ", r["excerpt"])[:220]
            if r in cited:
                b += self._claim_check(output, r)
            if size + len(b) > max_chars:
                break
            out.append(b)
            size += len(b)
        return "\n".join(out)

    def _claim_check(self, output: str, r: dict) -> str:
        """Deterministic help for the reviewer: the names and numbers the output attributes to this source,
        and whether each one appears in the text the system extracted from it."""
        text = self._texts.get(normalize_url(r["url"])) or r.get("excerpt") or ""
        if not text:
            return ""
        lines = [ln for ln in (output or "").splitlines() if r["id"] in ln or r["url"] in ln]
        toks = []
        for ln in lines:
            ln = re.sub(r"https?://\S+|\[E\d+\]", " ", ln)
            toks += re.findall(r"\b[A-Z][A-Za-z&'.-]{2,}(?:\s+[A-Z][A-Za-z&'.-]{2,})*", ln)
            toks += [n for n in re.findall(r"\d[\d,.]*\d|\d", ln) if len(n.replace(",", "").replace(".", "")) >= 2]
        toks = list(dict.fromkeys(t.strip() for t in toks if t.strip()))[:12]
        if not toks:
            return ""
        flat = re.sub(r"\s+", " ", text)
        low = flat.lower()
        found = [t for t in toks if t.lower() in low or t.replace(",", "") in flat.replace(",", "")]
        missing = [t for t in toks if t not in found]
        out = ""
        if found:
            out += "\n  موجود في نص المصدر: " + "، ".join(found)
            hits = []
            for t in found[:3]:
                i = low.find(t.lower())
                if i >= 0:
                    hits.append("…" + flat[max(0, i - 80):i + 80] + "…")
            if hits:
                out += "\n  من النص: " + " | ".join(hits)
        if missing:
            out += "\n  غير موجود في نص المصدر المستخرج: " + "، ".join(missing)
        return out

    def gaps(self) -> list[str]:
        st = self.state
        out = []
        labels = {"no_results": "لم يجد البحث نتائج", "not_opened": "وُجدت روابط لم تُفتح (الميزانية)",
                  "refused": "رفضت المواقع الوصول (حظر أو تسجيل دخول أو تحقق بشري) ولم يُتجاوز ذلك",
                  "opened_not_useful": "فُتحت صفحات لكن لم يكن فيها ما يخص المحور", "failed": "فشل فتح الروابط",
                  "useful": "أدلة مفيدة لكنها دون حد الكفاية"}
        for f, s in (st.get("status") or {}).items():
            if s["status"] != "sufficient":
                need = []
                if FACETS.get(f, {}).get("numeric") and not s["figures"]:
                    need.append("لا أرقام موثقة")
                if FACETS.get(f, {}).get("official") and s["critical"] and not s["official"] and st.get("country"):
                    need.append("لا مصدر رسمي")
                out.append(f"{FACETS.get(f, {}).get('ar', f)}: {labels.get(s['status'], s['status'])}"
                           f" (مقروء مفيد: {s['useful']}، نطاقات: {s['domains']})" + (f" — {'، '.join(need)}" if need else ""))
        return out

    def index(self, max_chars: int = 2500) -> str:
        """Compact list of read evidence for the synthesizer: id, title, URL, reading type."""
        rows = []
        for r in sorted(self.state["readings"].values(), key=lambda r: r.get("id", "")):
            if r.get("useful"):
                rows.append(f"[{r['id']}] {r.get('title', '')[:70]} — {r['url']} — {_completeness_ar(r)}")
        txt = "\n".join(rows)
        return txt[:max_chars]

    def read_urls(self) -> list[str]:
        return [r["url"] for r in self.state["readings"].values() if not r.get("failure")]


def _completeness_ar(r: dict) -> str:
    c = r.get("completeness") or "full"
    kind = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "abstract": "ملخص دراسة", "json": "بيانات رسمية"}.get(r.get("kind", ""), "صفحة")
    lab = {"full": "نص كامل", "partial": "قراءة جزئية (حدود الصفحات)", "summary": "ملخص فقط — ليس نص الدراسة",
           "needs_ocr": "يحتاج OCR"}.get(c, c)
    return f"{kind} — {lab}" + (f" — صفحات {r['pages']}" if r.get("pages") else "")


def _clean_q(q, limit: int = 120) -> str:
    q = re.sub(r"https?://\S+", "", str(q or ""))
    q = re.sub(r"[\"“”«»{}\[\]<>]", " ", q)
    return re.sub(r"\s+", " ", q).strip()[:limit]


def annotate_unread(answer: str, read: set[str]) -> tuple[str, list[str]]:
    """Mark every link in the final answer that the system did not read, so it is never shown as a source."""
    from .evidence import _URL
    marked = []

    def sub(m):
        u = m.group(0).rstrip(".")
        if normalize_url(u) in read:
            return m.group(0)
        marked.append(u)
        return m.group(0) + " [رابط لم يقرأه النظام]"
    out = _URL.sub(sub, answer or "")
    return out, marked
