"""Evidence ledger: one record per URL, built only from what the run's tools really returned.

Stages are kept apart on purpose, and a URL only moves up when a tool result proves it:
    discovered  - a search returned the link (a headline or snippet is NOT a reading of the page)
    opened      - the system requested the page (fetch_url or auto-read) and got an HTTP answer
    extracted   - the answer was 2xx and readable text came out of it
    relevant    - lines matching the question were taken from it (auto-read excerpt), or an agent opened it
    cited       - the final answer links to it AND it was extracted
Failures keep their reason class (blocked, not_found, server_error, unsafe_address, timeout, ...), so the
orchestrator and the user can see why a source is missing instead of a silent gap.
"""
from __future__ import annotations

import re

from .registry import normalize_url

STAGES = ("discovered", "opened", "extracted", "relevant", "cited")
_URL = re.compile(r"https?://[^\s)\]>»\"'|،,]+")


def classify_failure(result: str) -> str | None:
    """Reason class of a fetch_url result that did not yield a readable page; None when it did."""
    s = result or ""
    m = re.match(r"HTTP (\d{3})", s)
    if m:
        code = int(m.group(1))
        if 200 <= code < 300:
            return "non_text" if "[محتوى غير نصي" in s[:400] else None
        if code in (401, 403, 451):
            return "blocked"
        if code == 429:
            return "rate_limited"
        if code in (404, 410):
            return "not_found"
        if code >= 500:
            return "server_error"
        return f"http_{code}"
    if "الداخلية/الخاصة" in s:
        return "unsafe_address"
    if "رابط غير صالح" in s:
        return "invalid_url"
    if "تحويلات كثيرة" in s:
        return "too_many_redirects"
    if "Timeout" in s or "timed out" in s:
        return "timeout"
    return "network_error"


def build(events: list[dict], answer: str = "") -> dict:
    recs: dict[str, dict] = {}

    def rec(url: str) -> dict:
        k = normalize_url(url)
        if k not in recs:
            recs[k] = {"url": url, "stage": None, "found_by": [], "titles": [], "tasks": [], "via": [],
                       "status": None, "failure": None, "published": "", "publisher": "", "tables": 0,
                       "hash": "", "headline_only": False}
        return recs[k]

    def up(r: dict, stage: str):
        if r["stage"] is None or STAGES.index(stage) > STAGES.index(r["stage"]):
            r["stage"] = stage

    for e in events:
        if e.get("type") != "tool_result":
            continue
        task = e.get("task") or ""
        for f in e.get("found") or []:
            if not isinstance(f, dict) or not str(f.get("url", "")).startswith("http"):
                continue
            r = rec(f["url"])
            up(r, "discovered")
            for p in [f.get("provider")] + list(f.get("also") or []):
                if p and p not in r["found_by"]:
                    r["found_by"].append(p)
            if f.get("title") and len(r["titles"]) < 2:
                r["titles"].append(str(f["title"])[:160])
            if f.get("headline_only"):
                r["headline_only"] = True
            if task and task not in r["tasks"]:
                r["tasks"].append(task)
        for u in e.get("read") or []:  # auto-read excerpts and world_bank: opened, extracted, relevant lines taken
            r = rec(u)
            up(r, "relevant")
            r["via"] += [v for v in [e.get("tool")] if v and v not in r["via"]]
            r["status"] = r["status"] or 200
            if task and task not in r["tasks"]:
                r["tasks"].append(task)
        page = e.get("page")
        if isinstance(page, dict) and page.get("url"):
            r = rec(page["url"])
            r["via"] += [v for v in ["fetch_url"] if v not in r["via"]]
            if page.get("status"):
                r["status"] = page["status"]
                up(r, "opened")
            if page.get("failure"):
                r["failure"] = page["failure"]
            else:
                up(r, "relevant")  # an agent chose to open it and got readable text
                r["failure"] = None
            for k in ("published", "publisher", "hash"):
                if page.get(k) and not (k == "hash" and page.get("failure")):  # error pages look alike
                    r[k] = page[k]
            r["tables"] = max(r["tables"], int(page.get("tables") or 0))
            if task and task not in r["tasks"]:
                r["tasks"].append(task)

    cited_keys = {normalize_url(u.rstrip(".")) for u in _URL.findall(answer or "")}
    for k, r in recs.items():
        if k in cited_keys and r["stage"] in ("extracted", "relevant"):
            r["stage"] = "cited"
    counts = {s: 0 for s in STAGES}
    for r in recs.values():
        if r["stage"]:
            # cumulative: a cited source was also discovered, opened, extracted
            for s in STAGES[:STAGES.index(r["stage"]) + 1]:
                counts[s] += 1
    failures: dict[str, int] = {}
    for r in recs.values():
        if r["failure"]:
            failures[r["failure"]] = failures.get(r["failure"], 0) + 1
    hashes = [r["hash"] for r in recs.values() if r["hash"]]
    domains = {re.sub(r"^www\.", "", (re.match(r"https?://([^/]+)", r["url"]) or [None, ""])[1].lower())
               for r in recs.values() if r["stage"] in ("extracted", "relevant", "cited")}
    return {"counts": counts, "failures": failures,
            "duplicate_content": len(hashes) - len(set(hashes)),
            "independent_domains_read": len(domains - {""}),
            "headline_only": sum(1 for r in recs.values() if r["headline_only"] and r["stage"] == "discovered"),
            "uncited_links_in_answer": sorted(u for u in cited_keys if u not in recs
                                              or recs[u]["stage"] not in ("cited",))[:20],
            "records": sorted(recs.values(), key=lambda r: -STAGES.index(r["stage"]) if r["stage"] else 0)[:150]}
