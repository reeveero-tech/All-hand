"""Deterministic financial model: agents state assumptions, the code does the arithmetic.

Why (production run 6a03d324e329 and the v5-v7 studies): most rejections of financial tasks were arithmetic
written by the free model (e.g. 2,250,000 + 900,000 + 225,000 + 112,500 written as 1,237,500) or totals that did
not match their rows. Here the agent hands in a small JSON of inputs, each tagged with where it came from; the
system checks every tag against what it really read, computes the scenarios and renders the tables. The reviewer
then judges whether the assumptions are reasonable and honestly labelled, not whether the model can add.
"""
from __future__ import annotations

import json
import math
import re

from . import quality

# A task is financial when it asks for money arithmetic, not when it merely mentions a price.
_FIN = re.compile(r"(?:التحليل|النموذج|الخطة|التوقعات|الدراسة)\s+المالي|التكاليف|الإيرادات|نقطة\s*التعادل|التعادل|الربحية|"
                  r"العائد\s+على\s+الاستثمار|التدفق(?:ات)?\s+النقدي|الأرباح\s+والخسائر|"
                  r"financial|break[- ]?even|\broi\b|cash\s*flow|p&l|profit", re.I)

_TOTALS = re.compile(r"إجمالي|الإجمالي|المجموع|مجموع|total", re.I)  # a row named so would be summed by the table audit
FIELD = "model"
SCENARIOS = ("low", "base", "high")
SCEN_AR = {"low": "متحفظ", "base": "أساسي", "high": "متفائل"}
MAX_ITEMS = 12

INSTRUCTIONS = """
النموذج المالي يحسبه النظام لا أنت (إلزامي في هذه المهمة):
- إن كانت المهمة تتطلب إيرادات أو أرباحًا أو نقطة تعادل أو عائدًا، فلا تكتب مجاميعها ولا جداول حسابها بنفسك. ضع في ردك النهائي، بجانب "output"، حقلًا "model" بهذا الشكل:
{"currency": "ريال", "months": 12,
 "price": {"value": 8, "tag": "[E3]"},
 "unit_cost": {"value": 3.5, "tag": "افتراض: تكلفة التعبئة والمكونات لعبوة 250 مل"},
 "units_per_month": {"low": 5000, "base": 10000, "high": 18000, "tag": "افتراض: ..."},
 "fixed_monthly": [{"name": "إيجار مستودع", "value": 6000, "tag": "افتراض: ..."}],
 "startup": [{"name": "معدات وتعبئة أولية", "value": 60000, "tag": "افتراض: ..."}]}
- tag لكل رقم إلزامي، وهو واحد من ثلاثة: رقم دليل قرأه النظام مثل "[E3]" (والرقم موجود فعلًا في ذلك الدليل)، أو "من المستخدم"، أو "افتراض: سبب قصير ومنهجه".
- لا تنسب رقمًا إلى دليل لم يرد فيه. إن لم تجد الرقم في الأدلة فاكتبه افتراضًا معقولًا وبرّره. الافتراض الموسوم بصدق مقبول.
- في "output" اكتب التحليل والتبرير، وضع السطر {{النموذج_المالي}} حيث يجب أن تظهر الجداول؛ سيستبدله النظام بجداول محسوبة.
"""


def is_financial(task: dict) -> bool:
    return bool(_FIN.search(f"{task.get('title', '')} {task.get('instructions', '')}"))


def _num(x) -> float | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = re.sub(r"[\s,\u066c\u202f\u00a0]", "", x.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")))
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract(res: dict) -> dict | None:
    """The model block from the agent's final reply: the "model" field, else a ```agos-model fenced block."""
    m = res.get(FIELD)
    if isinstance(m, dict):
        return m
    if isinstance(m, str):
        try:
            v = json.loads(m)
            return v if isinstance(v, dict) else None
        except ValueError:
            pass
    f = re.search(r"```(?:agos-model|json)?\s*(\{[\s\S]*?\"units_per_month\"[\s\S]*?\})\s*```", res.get("output") or "")
    if f:
        try:
            v = json.loads(f.group(1))
            return v if isinstance(v, dict) else None
        except ValueError:
            return None
    return None


_SCEN_KEYS = {"low": ("low", "متحفظ", "conservative", "pessimistic", "min"),
              "base": ("base", "أساسي", "اساسي", "expected", "mid", "medium"),
              "high": ("high", "متفائل", "optimistic", "max")}
_UNIT_NUM = re.compile(r"^\s*([\d٠-٩][\d٠-٩,\u066c.٫\s]*)\s*[^\d٠-٩\-–]*$")


def _units_num(x):
    """A scenario's monthly units as the agent wrote them: 10000, "10,000", "10,000 عبوة", {"value": 10000}."""
    if isinstance(x, dict):
        x = x.get("value", x.get("units"))
    if isinstance(x, str) and _num(x) is None:
        m = _UNIT_NUM.match(x)  # one number followed by a unit word; never a range such as "8,000–12,000"
        return m.group(1).strip() if m else x
    return x


def units_block(raw: dict) -> dict:
    """{"low", "base", "high", "tag"} from the forms agents send (production run 755e194ad2fd sent a block the
    strict reader could not use, and the financial task failed with no model in the report)."""
    u = next((raw[k] for k in ("units_per_month", "units", "monthly_units") if k in raw), None)
    if isinstance(u, list) and len(u) == 3:
        return {"low": _units_num(u[0]), "base": _units_num(u[1]), "high": _units_num(u[2]), "tag": ""}
    if not isinstance(u, dict):
        return {}
    out = {"tag": u.get("tag") or ""}
    for s, keys in _SCEN_KEYS.items():
        k = next((k for k in keys if k in u), None)
        if k is not None:
            out[s] = _units_num(u[k])
            if not out["tag"] and isinstance(u[k], dict):
                out["tag"] = u[k].get("tag") or ""
    return out


class Checker:
    """Checks a tag against what the system read and what the user said. Plain callables keep it testable."""

    def __init__(self, evidence_text=None, user_text: str = ""):
        self.evidence_text = evidence_text or (lambda _id: None)
        self.user_values = quality.values_in(user_text or "")

    def tag(self, value: float, tag: str) -> tuple[str, str, str | None]:
        """-> (kind, label, problem). kind: evidence | user | assumption."""
        t = str(tag or "").strip()
        ids = re.findall(r"\bE(\d{1,3})\b", t)
        if ids:
            for n in ids:
                text = self.evidence_text(f"E{n}")
                if text and any(quality._near(value, v) for v in quality.values_in(text)):
                    return "evidence", f"[E{n}]", None
            why = ("الدليل غير موجود في ما قرأه النظام أو رُفض لعدم صلته بالدراسة" if not any(self.evidence_text(f"E{n}") for n in ids)
                   else "الرقم غير موجود في نص الدليل")
            return "assumption", "افتراض (لم يثبت من " + "، ".join(f"E{n}" for n in ids) + ")", f"{why}: {t}"
        if "المستخدم" in t or t.lower().startswith("user"):
            if any(quality._near(value, u) for u in self.user_values):
                return "user", "من المستخدم", None
            return "assumption", "افتراض (ليس في طلب المستخدم)", f"نُسب إلى المستخدم وليس في طلبه: {t}"
        reason = re.sub(r"^\(?\s*(?:افتراض|تقدير|assumption)\s*[:：)]?\s*", "", t, flags=re.I).strip(" )")
        if not reason:
            return "assumption", "افتراض بلا تبرير", "افتراض بلا تبرير"
        return "assumption", f"افتراض: {reason[:140]}", None


def normalize(raw: dict, chk: Checker) -> tuple[dict | None, list[str]]:
    """Validated inputs with every tag checked, or None with the reasons the block cannot be used."""
    errs, probs = [], []

    def one(v, name):
        if not isinstance(v, dict):
            v = {"value": v, "tag": ""}
        n = _num(v.get("value"))
        if n is None or n < 0:
            errs.append(f"قيمة غير صالحة: {name}")
            return None
        kind, label, p = chk.tag(n, v.get("tag"))
        if p:
            probs.append(f"{name}: {p}")
        return {"name": name, "value": n, "kind": kind, "tag": label}

    def items(lst, what):
        out = []
        for i, x in enumerate((lst or [])[:MAX_ITEMS] if isinstance(lst, list) else []):
            if isinstance(x, dict):
                name = re.sub(r"\s+", " ", _TOTALS.sub("", str(x.get("name") or "")).replace("|", "/")).strip()
                it = one(x, (name or f"{what} {i + 1}")[:80])
                if it:
                    out.append(it)
        return out

    price = one(raw.get("price"), "سعر بيع الوحدة")
    cost = one(raw.get("unit_cost"), "التكلفة المتغيرة للوحدة")
    u = units_block(raw)
    units = {}
    for s in SCENARIOS:
        n = _num(u.get(s))
        if n is None or n < 0:
            got = repr(u.get(s))[:40] if s in u else "غير موجود"
            errs.append(f"عدد وحدات غير صالح للسيناريو {SCEN_AR[s]} ({s}): {got}")
        else:
            units[s] = n
    if len(units) == 3 and not units["low"] <= units["base"] <= units["high"]:
        errs.append("يجب أن تكون الوحدات: المتحفظ ≤ الأساسي ≤ المتفائل")
    ukind, ulabel, up = chk.tag(units.get("base") or 0, u.get("tag"))
    if up:
        probs.append(f"الوحدات الشهرية: {up}")
    months = int(_num(raw.get("months")) or 12)
    if not 1 <= months <= 60:
        errs.append("المدة يجب أن تكون بين 1 و60 شهرًا")
    if price and cost and price["value"] <= cost["value"]:
        errs.append("سعر البيع لا يتجاوز التكلفة المتغيرة؛ لا توجد نقطة تعادل")
    if errs or not price or not cost:
        return None, errs or ["السعر والتكلفة مطلوبان"]
    return {"currency": str(raw.get("currency") or "ريال")[:12], "months": months, "price": price, "unit_cost": cost,
            "units": units, "units_tag": {"kind": ukind, "tag": ulabel},
            "fixed": items(raw.get("fixed_monthly"), "تكلفة ثابتة"), "startup": items(raw.get("startup"), "تكلفة تأسيس"),
            "problems": probs}, []


def _r(x: float) -> int:
    return int(round(x))


def compute(m: dict, budget: float | None = None) -> dict:
    """All figures from the inputs. Integers are rounded first so every printed total equals its printed rows."""
    price, cost = m["price"]["value"], m["unit_cost"]["value"]
    fixed = sum(_r(i["value"]) for i in m["fixed"])
    startup = sum(_r(i["value"]) for i in m["startup"])
    out = {"fixed_monthly": fixed, "startup": startup, "margin": round(price - cost, 2),
           "breakeven_units": math.ceil(fixed / (price - cost)) if fixed else 0, "scenarios": {}}
    for s in SCENARIOS:
        u = m["units"][s]
        rev, var = _r(u * price), _r(u * cost)
        profit = rev - var - fixed
        total = profit * m["months"]
        out["scenarios"][s] = {
            "units": _r(u), "revenue": rev, "variable": var, "fixed": fixed, "profit": profit,
            "period_profit": total, "net": total - startup,
            "roi_pct": round((total - startup) / startup * 100, 2) if startup else None,
            "payback_months": round(startup / profit, 2) if profit > 0 and startup else None,
        }
    if budget:
        worst = out["scenarios"]["low"]["profit"]
        need = startup + (max(0, -worst) * m["months"])
        out["budget"] = {"value": _r(budget), "startup_share_pct": round(startup / budget * 100, 1),
                         "left_after_startup": _r(budget) - startup, "need_worst_case": need,
                         "fits": need <= budget}
    return out


def _f(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.2f}".rstrip("0").rstrip(".")  # -33.33, 2.6: never a padded -33.30
    return f"{int(x):,}"


def render(m: dict, c: dict) -> str:
    cur, mo = m["currency"], m["months"]
    L = ["**النموذج المالي (محسوب بالنظام: جداول حسبها كود AGOS من المدخلات التالية)**", "",
         "| المُدخل | القيمة | المصدر |", "|---|---|---|",
         f"| {m['price']['name']} | {_f(m['price']['value'])} {cur} | {m['price']['tag']} |",
         f"| {m['unit_cost']['name']} | {_f(m['unit_cost']['value'])} {cur} | {m['unit_cost']['tag']} |",
         f"| الوحدات شهريًا (متحفظ / أساسي / متفائل) | {_f(m['units']['low'])} / {_f(m['units']['base'])} / "
         f"{_f(m['units']['high'])} | {m['units_tag']['tag']} |"]
    for i in m["fixed"]:
        L.append(f"| شهري: {i['name']} | {_f(_r(i['value']))} {cur} | {i['tag']} |")
    for i in m["startup"]:
        L.append(f"| تأسيس: {i['name']} | {_f(_r(i['value']))} {cur} | {i['tag']} |")
    L += ["", f"التكاليف الثابتة الشهرية: {_f(c['fixed_monthly'])} {cur}. تكاليف التأسيس: {_f(c['startup'])} {cur}. "
          f"هامش الوحدة: {_f(c['margin'])} {cur}. نقطة التعادل: {_f(c['breakeven_units'])} وحدة شهريًا.", "",
          f"| البند (شهريًا ما لم يُذكر) | {SCEN_AR['low']} | {SCEN_AR['base']} | {SCEN_AR['high']} |", "|---|---|---|---|"]
    rows = [("الوحدات المباعة", "units"), ("الإيرادات", "revenue"), ("التكاليف المتغيرة", "variable"),
            ("التكاليف الثابتة", "fixed"), ("صافي الربح الشهري", "profit"), (f"صافي الربح خلال {mo} شهرًا", "period_profit"),
            (f"الصافي بعد استرداد التأسيس ({mo} شهرًا)", "net"), ("العائد على الاستثمار %", "roi_pct"),
            ("فترة الاسترداد (أشهر)", "payback_months")]
    for label, k in rows:
        L.append(f"| {label} | " + " | ".join(_f(c["scenarios"][s][k]) for s in SCENARIOS) + " |")
    L += ["", "طريقة الحساب: الإيرادات = الوحدات × سعر البيع. التكاليف المتغيرة = الوحدات × التكلفة المتغيرة للوحدة. "
          "صافي الربح الشهري = الإيرادات − التكاليف المتغيرة − التكاليف الثابتة. ربح المدة = صافي الربح الشهري × عدد الأشهر. "
          "الصافي بعد الاسترداد = ربح المدة − تكاليف التأسيس. العائد على الاستثمار = الصافي بعد الاسترداد ÷ تكاليف التأسيس. "
          "فترة الاسترداد = تكاليف التأسيس ÷ صافي الربح الشهري (عند الربح فقط). "
          "نقطة التعادل = التكاليف الثابتة ÷ هامش الوحدة، مقربة إلى أعلى عدد صحيح. لا تشمل الحسابات الضرائب ولا الاستهلاك ما لم تُذكر مدخلًا."]
    b = c.get("budget")
    if b:
        low = c["scenarios"]["low"]["profit"]
        need = (f"السيناريو المتحفظ رابح شهريًا، فالمطلوب من الميزانية هو تكاليف التأسيس وحدها ({_f(b['need_worst_case'])} {cur})"
                if low >= 0 else
                f"السيناريو المتحفظ يخسر {_f(-low)} {cur} شهريًا، فالمطلوب تكاليف التأسيس وتغطية الخسارة {mo} شهرًا: "
                f"{_f(c['startup'])} + {_f(-low)} × {mo} = {_f(b['need_worst_case'])} {cur}")
        L += ["", f"الميزانية {_f(b['value'])} {cur} (من المستخدم): التأسيس يستهلك {_f(b['startup_share_pct'])}٪ منها، "
              f"ويتبقى {_f(b['left_after_startup'])} {cur}. {need}، " + ("وتكفيه الميزانية." if b["fits"] else "**ولا تكفيه الميزانية.**")]
    if m["problems"]:
        L += ["", "تنبيهات تحقق النظام من المصادر (أعيد وسم هذه الأرقام افتراضات):"] + [f"- {p}" for p in m["problems"][:8]]
    return "\n".join(L)


PLACEHOLDER = re.compile(r"\{\{\s*النموذج[_ ]المالي\s*\}\}")


def _no_model(out: str, errs: list[str]) -> str:
    """The placeholder never reaches a reader: it becomes a plain statement that the model was not computed."""
    note = "> **النموذج المالي لم يُحسب في هذه المحاولة:** " + PLACEHOLDER.sub("سطر النموذج المالي", "؛ ".join(errs))[:300]
    return PLACEHOLDER.sub(lambda _m: note, out)


def apply(res: dict, chk: Checker, budget: float | None = None) -> dict:
    """-> {"output", "model"|None, "computed"|None, "errors"}. Never raises: a bad block becomes review feedback."""
    out = res.get("output") or ""
    raw = extract(res)
    if raw is None:
        # A reply that left the {{النموذج_المالي}} line meant to send a model: without it the section is incomplete,
        # so it fails with feedback instead of passing a report whose tables are a placeholder (run 755e194ad2fd).
        wanted = bool(PLACEHOLDER.search(out))
        errs = ["لم يُرسل حقل model؛ هذه مهمة مالية يحسبها النظام: أرسل الافتراضات في الحقل model ولا تحسب بنفسك"]
        if wanted:
            errs.insert(0, "وضعت السطر {{النموذج_المالي}} لكن لم ترسل الحقل model")
        return {"output": _no_model(out, errs), "model": None, "computed": None, "missing": not wanted, "errors": errs}
    m, errs = normalize(raw, chk)
    if m is None:
        errs = [f"حقل model غير صالح: {e}" for e in errs]
        return {"output": _no_model(out, errs), "model": None, "computed": None, "errors": errs,
                "raw": json.dumps(raw, ensure_ascii=False)[:600]}
    c = compute(m, budget)
    table = render(m, c)
    out = re.sub(r"```(?:agos-model|json)?\s*\{[\s\S]*?\"units_per_month\"[\s\S]*?\}\s*```", "", out)
    out = PLACEHOLDER.sub(lambda _m: table, out) if PLACEHOLDER.search(out) else out.rstrip() + "\n\n" + table
    return {"output": out, "model": m, "computed": c, "errors": []}


def verdict(v: dict, info: dict) -> dict:
    """An invalid block fails the attempt with exact feedback. A missing block is not itself a failure (a budget
    allocation or a qualitative cost section needs no unit model), but when the attempt already failed for its
    own arithmetic the feedback says how to avoid it. A valid block leaves the verdict to the reviewer."""
    if not info["errors"]:
        return v
    if info.get("missing"):
        if v.get("arithmetic"):
            return {**v, "fix": "لا تحسب بنفسك: أرسل المدخلات في الحقل model وسيحسبها النظام. " + str(v.get("fix") or "")}
        return v
    return {**v, "pass": False, "score": min(float(v.get("score") or 0), 4.0),
            "issues": info["errors"] + list(v.get("issues") or []),
            "fix": "صحّح الحقل model بالشكل المطلوب، كل رقم بوسمه. " + str(v.get("fix") or "")}


REVIEW_NOTE = ("جداول «النموذج المالي (محسوب بالنظام)» حسبها الكود من المدخلات؛ لا تراجع حسابها ولا ترفض بسببه. "
               "قيّم فقط: هل المدخلات معقولة لهذا المنتج والسوق والميزانية، وهل وسم كل مدخل صادق. الافتراض الموسوم «افتراض: سبب» "
               "مع تبرير معقول مقبول ولا يُعد اختلاقًا؛ الاختلاق هو رقم يُنسب إلى مصدر أو إلى المستخدم دون أن يرد فيه، "
               "وقد أعاد النظام وسم هذه الحالات وذكرها في التنبيهات.")
