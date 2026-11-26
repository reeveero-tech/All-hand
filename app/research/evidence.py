"""Evidence ledger: one record per URL, built only from what the run's tools really returned.

Stages are kept apart on purpose, and a URL only moves up when a tool result proves it:
    discovered  - a search returned the link (a headline or snippet is NOT a reading of the page)
    opened      - the system requested the page (fetch_url or auto-read) and got an HTTP answer
    extracted   - the answer was 2xx and readable text came out of it
    relevant    - lines matching the question were taken from it (auto-read excerpt), or an agent opened it
    cited       - the final answer links to it AND it was extracted (readable text came out, no failure)
The research manager adds "evidence" events (it opens sources itself): they carry the facet (sub-question),
the reading method, the content type, the HTTP status, the failure reason, tables, the content hash and the
completeness ("full", "partial", "summary" for an abstract only, "needs_ocr"). A summary is extracted text, but
it is flagged so it is never presented as the full study.
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
            head = s[:600]
            m2 = re.search(r"\[لم يُقرأ: ([a-z_]+)\]", head)
            if m2:
                return m2.group(1)
            if "[محتوى غير نصي" in head:
                return "non_text"
            if "[مستند غير مقروء" in head:
                return "too_large" if "أكبر من الحد" in head else "unreadable_document"
            if "[يحتاج OCR" in head:
                return "needs_ocr"
            body = s.split("\n\n", 1)[1] if "\n\n" in s else ""
            no_tables = not re.search(r"جداول: [1-9]", head)
            if not body.strip() and no_tables:
                return "empty_content"  # a 2xx with no text is not a reading
            return None
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
                       "hash": "", "headline_only": False, "facets": [], "method": "", "kind": "", "ctype": "",
                       "completeness": "", "pages": "", "chars": 0, "tries": 0}
        return recs[k]

    def up(r: dict, stage: str):
        if r["stage"] is None or STAGES.index(stage) > STAGES.index(r["stage"]):
            r["stage"] = stage

    for e in events:
        if e.get("type") == "evidence":  # the research manager opened it itself
            r = rec(e.get("url") or "")
            if not r["url"].startswith("http"):
                continue
            r["tries"] += 1
            up(r, "discovered")
            for p in [e.get("provider")]:
                if p and p not in r["found_by"]:
                    r["found_by"].append(p)
            for fc in e.get("facets") or []:
                if fc not in r["facets"]:
                    r["facets"].append(fc)
            if e.get("title") and len(r["titles"]) < 2 and e["title"] not in r["titles"]:
                r["titles"].append(str(e["title"])[:160])
            if e.get("stage") == "discovered":
                continue
            r["via"] += [v for v in ["research_manager"] if v not in r["via"]]
            for k in ("method", "kind", "ctype", "pages"):
                if e.get(k):
                    r[k] = e[k]
            if e.get("status"):
                r["status"] = e["status"]
                up(r, "opened")
            if e.get("failure"):
                if r["stage"] not in ("extracted", "relevant"):  # a later failed retry does not erase a good reading
                    r["failure"] = e["failure"]
                continue
            r["failure"] = None
            up(r, "extracted")
            r["chars"] = max(r["chars"], int(e.get("chars") or 0))
            if e.get("completeness"):
                r["completeness"] = e["completeness"]
            if e.get("useful"):
                up(r, "relevant")
            for k in ("published", "publisher", "hash"):
                if e.get(k):
                    r[k] = e[k]
            r["tables"] = max(r["tables"], int(e.get("tables") or 0))
            continue
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
                if r["stage"] not in ("extracted", "relevant"):
                    r["failure"] = page["failure"]
            elif page.get("status") and 200 <= int(page["status"]) < 300:
                up(r, "relevant")  # an agent chose to open it and got readable text (a 2xx, not empty)
                r["failure"] = None
                for k in ("kind", "completeness", "pages"):
                    if page.get(k):
                        r[k] = page[k]
            for k in ("published", "publisher", "hash"):
                if page.get(k) and not (k == "hash" and page.get("failure")):  # error pages look alike
                    r[k] = page[k]
            r["tables"] = max(r["tables"], int(page.get("tables") or 0))
            if task and task not in r["tasks"]:
                r["tasks"].append(task)

    cited_keys = {normalize_url(u.rstrip(".")) for u in _URL.findall(answer or "")}
    for k, r in recs.items():
        if k in cited_keys and r["stage"] in ("extracted", "relevant") and not r["failure"]:
            r["stage"] = "cited"
            r["cited_as_summary"] = r["completeness"] == "summary"
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
    by_facet: dict[str, dict] = {}
    for r in recs.values():
        for fc in r["facets"]:
            b = by_facet.setdefault(fc, {"discovered": 0, "read": 0, "useful": 0, "failed": 0})
            b["discovered"] += 1
            if r["stage"] in ("extracted", "relevant", "cited"):
                b["read"] += 1
            if r["stage"] in ("relevant", "cited"):
                b["useful"] += 1
            if r["failure"]:
                b["failed"] += 1
    return {"counts": counts, "failures": failures, "by_facet": by_facet,
            "summary_only": sum(1 for r in recs.values() if r["completeness"] == "summary"
                                and r["stage"] in ("extracted", "relevant", "cited")),
            "cited_as_summary": sum(1 for r in recs.values() if r.get("cited_as_summary")),
            "duplicate_content": len(hashes) - len(set(hashes)),
            "independent_domains_read": len(domains - {""}),
            "headline_only": sum(1 for r in recs.values() if r["headline_only"] and r["stage"] == "discovered"),
            "uncited_links_in_answer": sorted(u for u in cited_keys if u not in recs
                                              or recs[u]["stage"] not in ("cited",))[:20],
            "records": sorted(recs.values(), key=lambda r: -STAGES.index(r["stage"]) if r["stage"] else 0)[:150]}
