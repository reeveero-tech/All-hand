"""Research quality gate: is the evidence behind this answer enough for THIS request?

No fixed "10 sources". The thresholds come from the request's profile: how many facets are critical, which of
them need documented figures, which need an official source, whether scholarly sources were asked for, and
whether the facets go stale. The gate reads only what the run really did (the manager's state, the evidence
ledger and the final answer). Its verdict is one of:
    sufficient   - every requirement met
    partial      - something is missing; the run may not end "done", and the gaps are listed
    insufficient - almost nothing was read; the answer is not research-backed
A technical success (searches ran, pages opened) never raises an answer's quality by itself.
"""
from __future__ import annotations

import math
import re
from datetime import date

from .manager import FACETS, _year, annotate_unread  # noqa: F401  (re-exported for the orchestrator)
from . import relevance as rel
from .registry import normalize_url

_URL = re.compile(r"https?://[^\s)\]>»\"'|،,]+")
_TAG = re.compile(r"https?://|\[E\d+\]|المصدر|مصدر:|من المستخدم|محسوب|تقدير|افتراض|غير متاح|=")


def profile(state: dict) -> dict:
    facets = state.get("facets") or {}
    critical = [f for f, w in facets.items() if w == "critical"]
    numeric = [f for f in critical if FACETS.get(f, {}).get("numeric")]
    official = [f for f in facets if FACETS.get(f, {}).get("official") and facets[f] == "critical"] if state.get("country") else []
    n = max(1, len(critical))
    return {"critical": critical, "numeric": numeric, "official_needed": bool(official),
            "academic_needed": "literature" in critical,
            "fresh": [f for f in critical if FACETS.get(f, {}).get("fresh")],
            "min_readable": max(3, min(15, math.ceil(1.5 * n))),
            "min_domains": max(2, min(8, math.ceil(0.8 * n))),
            "max_age_years": 3}


def _numeric_lines(answer: str) -> list[str]:
    out = []
    for ln in (answer or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or re.fullmatch(r"[\s|:\-]*", s):
            continue
        body = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", _URL.sub("", s))
        if re.search(r"\d", body) and re.search(r"ريال|دولار|%|٪|مليون|مليار|ألف|وحدة|SAR|USD", body):
            out.append(s)
    return out


def evaluate(state: dict | None, ledger: dict | None, answer: str, today: date | None = None) -> dict:
    today = today or date.today()
    state, ledger = state or {}, ledger or {}
    if not state.get("facets"):
        return {"verdict": "not_applicable", "gaps": [], "checks": {}, "profile": {}}
    p = profile(state)
    reads = list({(r.get("id") or k): r for k, r in reversed(list((state.get("readings") or {}).items()))
                  if not r.get("failure")}.values())  # one entry per document, first address kept
    useful = [r for r in reads if r.get("useful")]
    full = [r for r in useful if r.get("completeness") in ("full", "partial", "")]
    status = state.get("status") or {}
    domains = {r.get("domain") for r in useful}
    checks: dict[str, dict] = {}
    gaps: list[str] = []

    def check(name: str, ok: bool, value, need, gap: str):
        checks[name] = {"ok": bool(ok), "value": value, "need": need}
        if not ok:
            gaps.append(gap)

    covered = [f for f in p["critical"] if (status.get(f) or {}).get("status") == "sufficient"]
    uncovered = [f for f in p["critical"] if f not in covered]
    check("axes_covered", not uncovered, len(covered), len(p["critical"]),
          "محاور أساسية بلا أدلة كافية: " + "، ".join(FACETS.get(f, {}).get("ar", f) for f in uncovered))
    check("readable_sources", len(useful) >= p["min_readable"], len(useful), p["min_readable"],
          f"مصادر مقروءة مفيدة {len(useful)} من {p['min_readable']} مطلوبة لهذا الطلب")
    check("independent_domains", len(domains) >= p["min_domains"], len(domains), p["min_domains"],
          f"نطاقات مستقلة {len(domains)} من {p['min_domains']}")
    if p["official_needed"]:
        n = sum(1 for r in useful if r.get("class") == "official")
        check("official_sources", n >= 1, n, 1, "لا مصدر رسمي مقروء (جهة حكومية أو دولية)")
    if p["academic_needed"]:
        n = sum(1 for r in useful if r.get("class") == "academic")
        nfull = sum(1 for r in useful if r.get("class") == "academic" and r.get("completeness") != "summary")
        check("specialized_sources", n >= 1, {"academic": n, "full_text": nfull}, 1, "لا دراسة علمية مقروءة")
    for f in p["numeric"]:
        s = status.get(f) or {}
        check(f"numeric:{f}", (s.get("figures") or 0) > 0, s.get("figures") or 0, 1,
              f"{FACETS.get(f, {}).get('ar', f)}: لا أرقام موثقة من مصدر مقروء")
    stale = []
    for f in p["fresh"]:
        ys = [_year(r.get("published") or "") for r in useful if f in (r.get("facets") or [])]
        ys = [y for y in ys if y]
        if ys and today.year - max(ys) > p["max_age_years"]:
            stale.append(f)
    check("freshness", not stale, stale, f"<= {p['max_age_years']} سنوات",
          "أحدث مصدر أقدم من 3 سنوات في: " + "، ".join(FACETS.get(f, {}).get("ar", f) for f in stale))
    nums = _numeric_lines(answer)
    tagged = [ln for ln in nums if _TAG.search(ln)]
    ratio = round(len(tagged) / len(nums), 2) if nums else 1.0
    check("claims_with_sources", ratio >= 0.8, ratio, 0.8,
          f"{len(nums) - len(tagged)} سطرًا فيها أرقام بلا مصدر أو وسم")
    unread = ledger.get("uncited_links_in_answer") or []
    check("no_unread_links", not unread, len(unread), 0,
          f"{len(unread)} رابطًا في الجواب لم يقرأه النظام (وُسمت في النص)")
    open_conf = [c for c in state.get("conflicts") or [] if not c.get("resolved") and c.get("facet") in p["critical"]
                 and not (c["low"]["id"] in (answer or "") and c["high"]["id"] in (answer or ""))]
    check("conflicts_disclosed", not open_conf, len(open_conf), 0, "تعارض بين مصادر في محور أساسي لم يُعرض في الجواب")
    # Evidence the gate rejected may not carry the answer: citing it (by id or by URL) is a defect, not a source.
    low = (answer or "").lower()
    cited_ids = set(re.findall(r"\bE(\d{1,3})\b", answer or ""))
    rejected = [r for r in reads if r.get("relevance") and not r.get("useful")]
    bad = [r for r in rejected if r.get("id", "")[1:] in cited_ids or r["url"].lower().rstrip("/") in low]
    check("no_rejected_evidence", not bad, [r.get("id") for r in bad], 0,
          "الجواب يستشهد بمصادر رفضتها بوابة الصلة: " + "، ".join(f"{r.get('id')} ({rel.LABEL_AR.get(r.get('label'), '')})"
                                                              for r in bad))
    checks["relevance"] = {"ok": True, "value": rel.summary(reads), "need": "معلومة"}
    tables = sum(len(r.get("tables") or []) for r in useful)
    checks["usable_tables"] = {"ok": True, "value": tables, "need": "معلومة"}
    if ledger.get("cited_as_summary"):
        checks["cited_as_summary"] = {"ok": True, "value": ledger["cited_as_summary"], "need": "يُذكر أنه ملخص"}
    hard = [k for k, v in checks.items() if not v["ok"]]
    if len(useful) == 0 or (len(useful) < 2 and len(p["critical"]) > 1):
        verdict = "insufficient"
    elif hard:
        verdict = "partial"
    else:
        verdict = "sufficient"
    return {"verdict": verdict, "gaps": gaps, "checks": checks, "profile": p,
            "summary_only_sources": sum(1 for r in useful if r.get("completeness") == "summary"),
            "full_sources": len(full)}


def read_set(state: dict | None, extra: list[str] | None = None) -> set[str]:
    out = {normalize_url(r["url"]) for r in ((state or {}).get("readings") or {}).values() if not r.get("failure")}
    for r in ((state or {}).get("readings") or {}).values():
        if r.get("asked"):
            out.add(normalize_url(r["asked"]))
    out |= {normalize_url(u) for u in extra or []}
    return out
