"""Checks a result's figures against the world they describe, and classifies what a previous result may pass on.

Arithmetic audits (orchestrator.apply_audit) prove that a + b = c. They cannot see that c is absurd: on
production a follow-up inherited a demand of 3.79 billion cups a year and computed, correctly, a 604,000 %
return. The model only transcribes the figures the text states; every check below is computed here, and each
failure says why in terms of the text's own numbers (no fixed cap on what an estimate may be).
"""
from __future__ import annotations

import re

from .llm import LLM, LLMError, ModelPaused

PLAUSIBILITY = """استخرج من النص الأرقام كما وردت فيه حرفيًا (لا تحسب ولا تقدّر شيئًا من عندك). لكل حقل غير مذكور ضع null.
- product: اسم المنتج أو فئته باختصار.
- country_iso3: رمز الدولة المستهدفة بثلاثة أحرف (مثل SAU) إن كان السوق دولة محددة.
- population: عدد السكان أو المستهلكين المستهدفين الذي بُني عليه تقدير الطلب، كما ورد.
- market_units_per_year: حجم الطلب السنوي في السوق بالوحدات (أكواب، عبوات...) كما ورد.
- market_unit: اسم الوحدة.
- market_basis: "source" إن كان حجم الطلب منسوبًا لمصدر برابط، "estimate" إن كان تقديرًا بمنهج، "assumption" إن كان افتراضًا، "none" إن لم يُذكر أساسه.
- planned_units_per_year: الكمية التي يخطط المشروع نفسه لبيعها في السنة الأولى.
- price: سعر بيع الوحدة. unit_cost: تكلفة الوحدة. budget: الميزانية المتاحة للمشروع.
- revenue_year1: إيرادات المشروع في السنة الأولى. total_cost_year1: إجمالي تكاليف السنة الأولى.
- roi_pct: العائد على الاستثمار بالنسبة المئوية كما ورد. market_value_per_year: قيمة السوق السنوية بالعملة.
- currency: عملة المبالغ.
أعد JSON فقط بهذه المفاتيح."""

FIELDS = ("population", "market_units_per_year", "planned_units_per_year", "price", "unit_cost", "budget",
          "revenue_year1", "total_cost_year1", "roi_pct", "market_value_per_year")


def _f(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).replace(",", "").replace("\u066c", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _fmt(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


DEMAND_JUDGE = """أنت محلل أسواق. أمامك فئة منتج وبلد. ما الحد الأعلى المعتاد لاستهلاك الفرد الواحد سنويًا من هذه الفئة تحديدًا
(لا من كل المشروبات أو كل الأغذية)، بنفس الوحدة المذكورة، محسوبًا على كل السكان؟ أعطِ رقمًا واحدًا سخيًا (الحد الأعلى لا المتوسط).
أعد JSON فقط: {"typical_max_per_capita_year": رقم, "basis": "من أين تعرف ذلك باختصار"}"""
JUDGE_FACTOR = 2.0  # an estimate is rejected only when it is several times what is usual for the category


def check_figures(fig: dict, population: float | None = None, population_source: str | None = None,
                  demand_judgment: dict | None = None) -> list[dict]:
    """Deterministic checks of the stated figures against each other. Each item: check, severity
    (error fails the result, warning is shown), reason, values (the stated numbers the reason is about)."""
    v = {k: _f(fig.get(k)) for k in FIELDS}
    pop = v["population"] or population
    out = []
    basis = str(fig.get("market_basis") or "none")
    unit = str(fig.get("market_unit") or "وحدة")
    demand_bad = False
    if v["market_units_per_year"] and pop:
        daily = v["market_units_per_year"] / pop / 365
        if daily > 1:
            demand_bad = True
            src = f" (السكان {_fmt(pop)}" + (f"، المصدر: {population_source}" if population_source else "") + ")"
            out.append({"check": "demand_per_capita", "severity": "error", "values": [v["market_units_per_year"]],
                        "reason": f"حجم الطلب {_fmt(v['market_units_per_year'])} {unit} سنويًا يعني {daily:.2f} {unit} لكل فرد "
                                  f"يوميًا من كل السكان{src}، وهذا غير معقول لمنتج واحد ما لم يثبته مصدر."})
        elif demand_judgment and _f(demand_judgment.get("typical_max_per_capita_year")):
            per_year = v["market_units_per_year"] / pop
            typical = _f(demand_judgment.get("typical_max_per_capita_year"))
            if typical > 0 and per_year > typical * JUDGE_FACTOR:
                demand_bad = True
                out.append({"check": "demand_context", "severity": "error", "values": [v["market_units_per_year"]],
                            "reason": f"حجم الطلب {_fmt(v['market_units_per_year'])} {unit} سنويًا يعني {per_year:,.1f} {unit} للفرد سنويًا "
                                      f"من كل السكان ({_fmt(pop)})، أي {per_year / typical:,.1f} أضعاف الحد الأعلى المعتاد لهذه الفئة "
                                      f"(نحو {_fmt(typical)} للفرد سنويًا" + (f"؛ {demand_judgment['basis']}" if demand_judgment.get("basis") else "") +
                                      "). أعد التقدير بمصدر أو صرّح بأنه غير متاح."})
    if v["planned_units_per_year"] and v["market_units_per_year"] and v["planned_units_per_year"] > v["market_units_per_year"]:
        demand_bad = True
        out.append({"check": "plan_exceeds_market", "severity": "error", "values": [v["planned_units_per_year"]],
                    "reason": f"المبيعات المخططة {_fmt(v['planned_units_per_year'])} تتجاوز حجم السوق كله {_fmt(v['market_units_per_year'])}."})
    if v["revenue_year1"] and v["market_value_per_year"] and v["revenue_year1"] > v["market_value_per_year"]:
        demand_bad = True
        out.append({"check": "revenue_exceeds_market", "severity": "error", "values": [v["revenue_year1"]],
                    "reason": f"إيراد السنة الأولى {_fmt(v['revenue_year1'])} يتجاوز قيمة السوق السنوية كلها {_fmt(v['market_value_per_year'])}."})
    if v["revenue_year1"] and v["planned_units_per_year"] and v["price"]:
        cap = v["planned_units_per_year"] * v["price"]
        if v["revenue_year1"] > cap * 1.1:
            out.append({"check": "revenue_vs_units", "severity": "error", "values": [v["revenue_year1"]],
                        "reason": f"الإيراد {_fmt(v['revenue_year1'])} أكبر من الكمية × السعر = {_fmt(cap)}."})
    if v["roi_pct"] is not None and v["revenue_year1"] and v["total_cost_year1"]:
        calc = (v["revenue_year1"] - v["total_cost_year1"]) / v["total_cost_year1"] * 100
        if abs(calc - v["roi_pct"]) > max(5.0, abs(calc) * 0.1):
            out.append({"check": "roi_recomputed", "severity": "error", "values": [v["roi_pct"]],
                        "reason": f"العائد المذكور {_fmt(v['roi_pct'])}% لا يطابق (الإيراد − التكلفة) ÷ التكلفة = {calc:.1f}%."})
    if v["revenue_year1"] and v["budget"] and basis != "source" and not demand_bad and v["revenue_year1"] > v["budget"] * 50:
        demand_bad = True
        out.append({"check": "revenue_vs_budget", "severity": "error",
                    "values": [x for x in (v["revenue_year1"], v["market_units_per_year"]) if x],
                    "reason": f"إيراد السنة الأولى {_fmt(v['revenue_year1'])} يساوي {v['revenue_year1'] / v['budget']:,.0f} ضعف الميزانية "
                              f"{_fmt(v['budget'])}، ومبني على طلب غير موثق بمصدر؛ لا يمكن تمويل إنتاج وتوزيع بهذا الحجم من هذه الميزانية."})
    if v["roi_pct"] is not None and v["roi_pct"] > 1000 and (demand_bad or basis != "source"):
        out.append({"check": "roi_unsupported", "severity": "error",
                    "values": [v["roi_pct"]] + ([v["market_units_per_year"]] if v["market_units_per_year"] and basis != "source" else []),
                    "reason": f"عائد {_fmt(v['roi_pct'])}% مبني على حجم طلب " +
                              ("ثبت أنه غير معقول" if demand_bad else "غير موثق بمصدر") + "؛ لا يصح تقديمه نتيجة."})
    elif demand_bad:
        dep = [x for x in (v["roi_pct"], v["revenue_year1"]) if x]
        if dep:
            out.append({"check": "depends_on_bad_demand", "severity": "error", "values": dep,
                        "reason": "الإيراد/العائد المذكور مبني على تقدير طلب ثبت أنه غير معقول، فلا يُعتمد."})
    if v["planned_units_per_year"] and v["unit_cost"] and v["budget"]:
        need = v["planned_units_per_year"] * v["unit_cost"]
        if need > v["budget"] * 1.05:
            out.append({"check": "budget_funding", "severity": "warning", "values": [v["planned_units_per_year"]],
                        "reason": f"إنتاج {_fmt(v['planned_units_per_year'])} وحدة بتكلفة {_fmt(v['unit_cost'])} يحتاج {_fmt(need)} "
                                  f"والميزانية {_fmt(v['budget'])}؛ يجب أن يوضح الجواب مصدر تمويل الفرق (كإعادة استثمار المبيعات)."})
    return out


_COUNTRIES = [("السعودية|السعودي|المملكة العربية السعودية|Saudi", "SAU"), ("الإمارات|UAE|Emirates", "ARE"), ("الكويت|Kuwait", "KWT"),
              ("قطر|Qatar", "QAT"), ("البحرين|Bahrain", "BHR"), ("سلطنة عمان|Oman", "OMN"), ("مصر|Egypt", "EGY"),
              ("الأردن|Jordan", "JOR")]


def country_of(text: str) -> str | None:
    """The country the text is about: the most mentioned one, whole words only ('مصروفات' is not Egypt)."""
    best, n = None, 0
    for pat, iso in _COUNTRIES:
        k = len(re.findall(rf"(?<!\w)(?:[وبلفك]|ال|وال|بال)?(?:{pat})(?!\w)", text or "", re.I))  # \w covers Arabic letters, not ،
        if k > n:
            best, n = iso, k
    return best


async def check_plausibility(llm: LLM, text: str) -> dict:
    """{'figures': what the text states, 'issues': [...], 'errors': n}. Raises only ModelPaused (the run pauses:
    a check that could not run is not a check that passed)."""
    if not re.search(r"\d{2,}", text or ""):
        return {"figures": {}, "issues": [], "errors": 0}
    try:
        fig = await llm.chat_json([{"role": "system", "content": PLAUSIBILITY}, {"role": "user", "content": text[:14000]}],
                                  temperature=0.0, max_tokens=700)
    except ModelPaused:
        raise
    except (ValueError, LLMError) as e:
        return {"figures": {}, "issues": [], "errors": 0, "error": str(e)[:200]}
    if not re.fullmatch(r"[A-Za-z]{3}", str(fig.get("country_iso3") or "")):
        fig["country_iso3"] = country_of(text)
    pop, pop_src = None, None
    if not _f(fig.get("population")) and _f(fig.get("market_units_per_year")) and re.fullmatch(r"[A-Za-z]{3}", str(fig.get("country_iso3") or "")):
        pop, pop_src = await _wb_population(str(fig["country_iso3"]).upper())
    judgment = None
    pop_used = _f(fig.get("population")) or pop
    units = _f(fig.get("market_units_per_year"))
    if units and pop_used and units / pop_used / 365 <= 1:
        product = str(fig.get("product") or "")
        try:
            judgment = await llm.chat_json([{"role": "system", "content": DEMAND_JUDGE}, {"role": "user", "content":
                f"المنتج/الفئة: {product or '(كما في النص)'}\nالبلد: {fig.get('country_iso3') or '-'}\n"
                f"الوحدة: {fig.get('market_unit') or 'وحدة'}\n\nعنوان النص (لتحديد الفئة فقط):\n{_no_digits(text)[:300]}"}],
                temperature=0.0, max_tokens=300)  # no figures from the text: the judge must not anchor on the estimate it judges
        except ModelPaused:
            raise
        except (ValueError, LLMError):
            judgment = None
    issues = check_figures(fig, pop, pop_src, judgment)
    return {"figures": {k: fig.get(k) for k in (*FIELDS, "product", "market_unit", "market_basis", "country_iso3", "currency")},
            "demand_judgment": judgment,
            "issues": issues, "errors": sum(1 for i in issues if i["severity"] == "error")}


def _no_digits(text: str) -> str:
    return re.sub(r"[\d\u0660-\u0669][\d\u0660-\u0669,.\s%]*", "…", text or "")


async def _wb_population(iso3: str) -> tuple[float | None, str | None]:
    from .tools import world_bank
    res = await world_bank(f"{iso3} SP.POP.TOTL")
    m = re.search(r"- \d{4}: ([\d,]+)", res)
    src = re.search(r"المصدر: (\S+)", res)
    return (_f(m.group(1)), src.group(1) if src else None) if m else (None, None)


def apply_plausibility(v: dict, p: dict) -> dict:
    errs = [i for i in p.get("issues") or [] if i["severity"] == "error"]
    warns = [i for i in p.get("issues") or [] if i["severity"] == "warning"]
    if not errs and not warns:
        return v
    out = {**v, "plausibility": p["issues"]}
    out["issues"] = [f"رقم غير معقول تحقق منه النظام: {i['reason']}" for i in errs] + \
                    [f"تنبيه معقولية: {i['reason']}" for i in warns] + list(v.get("issues") or [])
    if errs:
        out["pass"] = False
        out["score"] = min(float(v.get("score") or 0), 4.0)
        out["fix"] = ("أعد بناء تقدير الطلب على أساس معقول وموثق (السكان من مصدر رسمي × نسبة المستهلكين × معدل استهلاك واقعي، "
                      "مع وسم كل مدخل)، وأعد حساب الإيراد والعائد منه، أو صرّح بأن البيانات لا تكفي. " + str(v.get("fix") or ""))
    return out


# --- what a previous result may pass on to a follow-up -------------------------------------------------------

TAGS = {"rejected": "مرفوض — ثبت خطؤه أو عدم معقوليته، لا تستخدمه",
        "user": "صرّح به المستخدم",
        "sourced": "مدعوم بمصدر مذكور",
        "calculated": "نتيجة حسابية (أعد حسابها إن تغيّر أحد مدخلاتها)",
        "estimate": "تقدير — أعد فحص منهجه قبل استخدامه",
        "assumption": "افتراض",
        "uncertain": "غير مؤكد — لا تقدّمه كحقيقة"}
_SCALE = {"مليار": 1e9, "مليون": 1e6, "ألف": 1e3, "الف": 1e3, "billion": 1e9, "million": 1e6, "k": 1e3}
_NUM = re.compile(r"(\d{1,3}(?:[ \u202f\u00a0,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(مليار|مليون|ألف|الف|billion|million)?", re.I)


def values_in(line: str) -> list[float]:
    out = []
    for m in _NUM.finditer(line):
        n = _f(re.sub(r"[ \u202f\u00a0,]", "", m.group(1)))
        if n is None:
            continue
        out.append(n * _SCALE.get((m.group(2) or "").lower(), 1))
    return out


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(b) * 0.01, 0.5)


_TOTAL_LINE = re.compile(r"إجمالي|الإجمالي|المجموع|مجموع|total|=", re.I)


def classify_line(line: str, user_values: list[float], rejected: list[float], rejected_totals: list[float] = ()) -> str:
    vals = values_in(line)
    if any(_near(x, r) for x in vals for r in rejected):
        return "rejected"
    if rejected_totals and _TOTAL_LINE.search(line) and any(_near(x, r) for x in vals for r in rejected_totals):
        return "rejected"  # a wrong total is wrong where it is stated as a total, not in every row that shares the figure
    if re.search(r"https?://|المصدر:|\(مصدر", line):
        return "sourced"
    if re.search(r"افتراض|نفترض|بافتراض|assum", line, re.I):
        return "assumption"
    if re.search(r"تقدير|تقديري|تقريب|يُقدَّر|يقدر|estimate", line, re.I):
        return "estimate"
    if re.search(r"=|محسوب", line):
        return "calculated"
    if vals and all(any(_near(x, u) for u in user_values) for x in vals if x >= 10):
        return "user"
    return "uncertain"


def prior_ledger(answer: str, user_text: str, rejected: list[float], limit: int = 40,
                 rejected_totals: list[float] = ()) -> list[dict]:
    """Every line of the previous answer that carries a figure, with what it may be used as."""
    user_values = values_in(user_text or "")
    out = []
    for raw in (answer or "").splitlines():
        line = raw.strip().strip("|").strip()
        if not line or not any(x >= 10 for x in values_in(line)):
            continue
        out.append({"line": re.sub(r"\s*\|\s*", " | ", line)[:220], "tag": classify_line(line, user_values, rejected, rejected_totals)})
        if len(out) >= limit:
            break
    return out


def render_ledger(items: list[dict], issues: list[dict], max_chars: int = 3000) -> str:
    """Compact: the free model takes ~16k characters per call, and the previous answer itself (with rejected
    lines labeled in place) follows this note. So list what is rejected or sourced, and only count the rest."""
    if not items and not issues:
        return ""
    parts = ["سجل معلومات النتيجة السابقة (صنّفه النظام آليًا؛ التزم بوسم كل سطر):"]
    for i in issues:
        if i["severity"] == "error":
            parts.append(f"- مرفوض: {i['reason'][:300]}")
    order = {"rejected": 0, "sourced": 1, "user": 2, "calculated": 3, "estimate": 4, "assumption": 5, "uncertain": 6}
    counts: dict = {}
    for it in items:
        counts[it["tag"]] = counts.get(it["tag"], 0) + 1
    listed = 0
    for it in sorted(items, key=lambda it: order.get(it["tag"], 9)):
        if it["tag"] not in ("rejected", "sourced", "user"):
            continue
        line = f"- [{TAGS[it['tag']]}] {it['line'][:160]}"
        if sum(map(len, parts)) + len(line) > max_chars - 600:
            break
        parts.append(line)
        listed += 1
    rest = {k: v for k, v in counts.items() if k not in ("rejected", "sourced", "user")}
    if rest:
        parts.append("بقية الأسطر: " + "، ".join(f"{TAGS[k].split(' —')[0]} {v}" for k, v in rest.items())
                     + " — لا تُعامل كحقائق دون إعادة فحص.")
    if any(it["tag"] == "rejected" for it in items) or any(i["severity"] == "error" for i in issues):
        parts.append("إن احتجت إيرادًا أو عائدًا وتقدير الطلب السابق مرفوض: لا تشتقه من الأرقام المرفوضة. ابنِه من حدود الميزانية "
                     "(عدد الوحدات التي تموّلها الميزانية = الميزانية المخصصة للإنتاج ÷ تكلفة الوحدة) وسعر بمصدر أو موسوم، "
                     "في سيناريوهات (متحفظ/متوسط/متفائل) موسومة «افتراض»، أو اذكر ما يلزم في «بيانات مطلوبة».")
    parts.append("قواعد: لا تنقل سطرًا مرفوضًا أو غير مؤكد كحقيقة؛ لا ترث تقديرات الطلب أو الإيرادات دون إعادة فحص أساسها؛ "
                 "إن غيّر المستخدم معطًى (كالميزانية) فالقيمة الجديدة تحل محل القديمة في كل حساب، والقديمة سجل تاريخي فقط.")
    return "\n".join(parts)


_MARKED_REJECTED = re.compile(r"مرفوض|مستبعد|غير معقول|لا يُستخدم|لا يستخدم|ثبت خطؤ|استُبعد|rejected", re.I)


_DISPUTED = re.compile(r"غير موثوق|غير موثق|بلا مصدر|لا توجد لها|لا يوجد لها|لا مصدر", re.I)
_COMPUTES = re.compile(r"[=×*÷]|المستخدمة? في الحساب|نعتمد|اعتمدنا", re.I)


def reuses_rejected(text: str, rejected: list[float], rejected_totals: list[float] = ()) -> list[str]:
    """Lines that build on a figure already proven wrong (a follow-up must not carry it forward).
    A line that names the figure only to say it is rejected is fine; tiny values are ignored (too common to tell)."""
    rej = [r for r in rejected if abs(r) >= 100]
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _MARKED_REJECTED.search(line):
            continue
        if _DISPUTED.search(line) and not _COMPUTES.search(line):  # disputes the figure without building on it
            continue
        vals = values_in(line)
        hit = any(_near(x, r) for x in vals for r in rej) or (
            rejected_totals and _TOTAL_LINE.search(line) and any(_near(x, r) for x in vals for r in rejected_totals if abs(r) >= 100))
        if hit:
            out.append(line[:200])
    return list(dict.fromkeys(out))


def apply_rejected(v: dict, lines: list[str]) -> dict:
    if not lines:
        return v
    v = dict(v)
    v["pass"] = False
    v["score"] = min(v.get("score") or 0, 4)
    v["issues"] = list(v.get("issues") or []) + [
        "استُخدم رقم ثبت في الدراسة السابقة أنه خاطئ أو غير معقول: " + " ؛ ".join(lines[:3])
        + " — احذفه، ولا تبنِ عليه إيرادًا أو عائدًا؛ استخدم رقمًا بمصدر مقروء، أو افتراضًا موسومًا «افتراض» بسيناريوهات، "
          "أو اذكره في «بيانات مطلوبة»."]
    v["reused_rejected"] = lines[:5]
    return v


_USER_TAG = re.compile(r"من المستخدم|صرّح به المستخدم|حسب المستخدم")


def false_user_tags(text: str, user_text: str) -> list[str]:
    """Lines that credit the user with figures the user never gave (a mislabeled source is a fabricated one)."""
    user_values = values_in(user_text or "")
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not _USER_TAG.search(line) or re.search(r"[=×*÷]|محسوب", line):
            continue
        vals = [x for x in values_in(line) if x >= 10]
        if vals and not any(_near(x, u) for x in vals for u in user_values):
            out.append(line[:160])
    return out
