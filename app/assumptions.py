"""Assumptions a study depends on (the budget first), which results depend on them, and what a change affects.

A follow-up that only changes the budget ("غيّر الميزانية إلى 300 ألف ريال وحدّث النتائج المتأثرة فقط") does not
need a new plan, new research or new competitor and price analysis. It needs every result that depends on the
budget recomputed with the new value, and nothing that depends on it left on the old value. All of this is decided
here by code, from the previous run's saved plan and outputs: no model call.
"""
from __future__ import annotations

import re

_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_SCALE = {"ألف": 1e3, "الف": 1e3, "آلاف": 1e3, "الاف": 1e3, "k": 1e3, "K": 1e3, "مليون": 1e6, "ملايين": 1e6, "m": 1e6, "M": 1e6,
          "million": 1e6, "thousand": 1e3, "مليار": 1e9, "billion": 1e9}
_CUR = r"(?:ريال|ر\.س|SAR|﷼|دولار|USD|\$|درهم|AED|جنيه|دينار)"
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(ألف|الف|آلاف|الاف|مليون|ملايين|مليار|million|thousand|billion|[kKmM](?![a-zA-Z]))?\s*(" + _CUR + r")?")
_BUDGET = re.compile(r"ميزاني|budget|رأس\s*المال|capital", re.I)
_CHANGE = re.compile(r"غيّ?ر|عدّ?ل|حدّ?ث|اجعل|ارفع|خفّ?ض|قلّ?ل|زِد|بدلًا|بدلا|بدل |تصبح|إلى\s+\d|الى\s+\d|change|update|set .*budget|instead", re.I)
# A task depends on the budget when its subject is money spent or earned against it.
DEPENDS = re.compile(r"ميزاني|تمويل|تكلف|تكاليف|مال(?:ي|ية)|عائد|ربح|استرداد|تدفق|نقطة التعادل|إنفاق|انفاق|موارد|خطة (?:ال)?تنفيذ|"
                     r"خطة (?:ال)?إطلاق|توزيع|استثمار|ROI|break.?even|budget|financ|cost|capex|opex|payback|cash", re.I)


def _num(raw: str, scale: str | None) -> float | None:
    try:
        v = float(raw.replace(",", ""))
    except ValueError:
        return None
    return v * _SCALE.get(scale or "", 1.0)


def amounts(text: str) -> list[dict]:
    """Money amounts in budget context: {"value", "unit", "text"} in order of appearance."""
    out = []
    for line in (text or "").translate(_AR).splitlines():
        if not _BUDGET.search(line):
            continue
        for m in _AMOUNT.finditer(line):
            v = _num(m.group(1), m.group(2))
            if v is None or v < 1000 or (not m.group(2) and not m.group(3) and v < 10000):
                continue  # small bare numbers in a budget line are years, counts or percentages
            out.append({"value": v, "unit": (m.group(3) or "").strip() or None, "text": m.group(0).strip()})
    return out


def budget_change(goal: str) -> dict | None:
    """The new budget a follow-up asks for, if it asks to change it to a stated value."""
    g = (goal or "").translate(_AR)
    if not _BUDGET.search(g) or not _CHANGE.search(g):
        return None
    found = amounts(g)
    if not found:
        return None
    new = found[-1]  # "من 200 ألف إلى 300 ألف": the last amount is the new one
    return {"name": "budget", "new": new["value"], "unit": new["unit"] or (found[0]["unit"] if found else None), "text": new["text"]}


def prior_budget(prev: dict) -> dict | None:
    """The budget the previous study used: stated by the user in its goal, else the value its results used most."""
    stated = amounts(prev.get("goal") or "")
    if stated:
        return {"value": stated[0]["value"], "unit": stated[0]["unit"], "source": "user"}
    counts: dict[float, int] = {}
    units: dict[float, str | None] = {}
    texts = [t.get("output") or "" for t in (prev.get("tasks") or {}).values()] + [prev.get("result") or ""]
    for text in texts:
        for a in amounts(text):
            counts[a["value"]] = counts.get(a["value"], 0) + 1
            units.setdefault(a["value"], a["unit"])
    if not counts:
        return None
    v = max(counts, key=lambda k: (counts[k], k))
    return {"value": v, "unit": units.get(v), "source": "assumed_by_study"}


def fmt(v: float) -> str:
    return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"


def _mentions(text: str, value: float | None) -> bool:
    if value is None:
        return False
    t = (text or "").translate(_AR)
    return any(abs(a["value"] - value) < 0.5 for a in amounts(t)) or fmt(value) in t


def affected(tasks: list[dict], old: float | None) -> dict:
    """Which root tasks depend on the budget: by subject, by stating the old budget, then everything downstream."""
    direct = {t["id"] for t in tasks
              if DEPENDS.search(f"{t.get('title', '')} {t.get('instructions', '')}") or _mentions(t.get("output") or "", old)
              or _BUDGET.search(t.get("output") or "")}
    hit, changed = set(direct), True
    while changed:
        changed = False
        for t in tasks:
            if t["id"] not in hit and set(t.get("depends_on") or []) & hit:
                hit.add(t["id"])
                changed = True
    return {"direct": sorted(direct), "all": [t["id"] for t in tasks if t["id"] in hit],
            "unchanged": [t["id"] for t in tasks if t["id"] not in hit]}


_WAS = re.compile(r"سابق|السابقة|القديمة|بدلًا من|بدلا من|بدل |كانت|كان |من .{0,20}إلى|previous|instead of|was ", re.I)


def stale_lines(text: str, old: float | None, new: float | None = None) -> list[str]:
    """Lines that still use the old budget as current: a budget line with the old value and no marker that it is
    the previous one. (One updated number with its dependents left on the old value is not an update.)"""
    if old is None or (new is not None and abs(old - new) < 0.5):
        return []
    bad = []
    for line in (text or "").splitlines():
        if not _BUDGET.search(line.translate(_AR)) or _WAS.search(line):
            continue
        if any(abs(a["value"] - old) < 0.5 for a in amounts(line)):
            bad.append(line.strip()[:200])
    return bad


def instruction(change: dict, old: dict | None, previous_output: str) -> str:
    unit = change.get("unit") or (old or {}).get("unit") or ""
    was = f" بدلًا من {fmt(old['value'])} {unit}".rstrip() if old else ""
    return (f"\n\nتحديث متابعة (ملزم): الميزانية الجديدة {fmt(change['new'])} {unit} (من المستخدم){was}. "
            "أعد حساب كل رقم يعتمد على الميزانية (التوزيع، التكاليف، الإيراد المتوقع إن ارتبط بالإنفاق، نقطة التعادل، "
            "العائد، مدة الاسترداد، حجم التنفيذ) بالقيمة الجديدة، واحسب الأرقام بالحاسبة. لا تُبقِ أي رقم مبني على الميزانية "
            "السابقة؛ إن ذكرتها فاذكرها صراحة بأنها «السابقة». أبقِ ما لا يتأثر بالميزانية كما هو بمصادره."
            + (f"\n\nناتجك السابق لهذه المهمة (عدّل ما يتأثر بالميزانية فقط):\n{previous_output[:3500]}" if previous_output else ""))
