"""Final Report Builder: the final report is assembled from saved, already-reviewed results, section by section.

Why (production run 84cca6feac24): the final answer was one long model call (up to 5 continuation parts) plus a
full rewrite on rejection, ~16 calls, written from scratch after every agent had finished. When the quota ran out in
that step, nothing of the report existed and every result was unusable.

Now:
- the skeleton is fixed by the plan: one section per root task, filled with that task's reviewed output as is
  (deterministic: no call), with its sources and, if it did not pass review, a visible banner and the reasons;
- the model writes only what needs wording across sections: the executive summary and recommendations (one call);
- the limits, data-needed and sources sections are built by code from the run's own records;
- each section is saved with a fingerprint of its inputs, so a resumed or follow-up run reuses an unchanged
  section and rebuilds the report from saved sections; a section that fails does not lose the others.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

URL = re.compile(r"https?://[^\s)\]>»\"']+")


def fp(*parts) -> str:
    """Stable fingerprint of a section's inputs."""
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]


def urls(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,،؛:") for u in URL.findall(text or "")))


def _issues(t: dict) -> list[str]:
    if t.get("status") != "failed":
        return []
    att = t.get("attempts") or []
    k = att[t["best_attempt"] - 1] if t.get("best_attempt") and t["best_attempt"] <= len(att) else (att or [{}])[-1]
    return [str(i) for i in ((k.get("verdict") or {}).get("issues") or [])][:3]


def task_sections(tasks: list[dict]) -> list[dict]:
    """One section per root task, in plan order, from its reviewed output. No model call."""
    out = []
    for t in tasks:
        if not t.get("output") or t.get("status") not in ("done", "failed"):
            continue
        status = "verified" if t["status"] == "done" else "unverified"
        text = str(t["output"]).strip()
        out.append({"id": f"task:{t['id']}", "kind": "task", "task": t["id"], "title": t.get("title") or t["id"],
                    "status": status, "issues": _issues(t), "text": text, "sources": urls(text),
                    "fp": fp("task", t["id"], status, text), "by": "code", "at": time.time()})
    return out


def digest(text: str, limit: int) -> str:
    """What the summary writer needs from a section: its headings and the lines that carry figures or sources,
    in order, within `limit` characters (the full text stays in the report)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    keep = [ln for ln in lines if ln.startswith("#") or re.search(r"\d", ln) or "http" in ln or ln.startswith(("- **", "**"))]
    out, n = [], 0
    for ln in keep or lines:
        if n + len(ln) + 1 > limit:
            break
        out.append(ln)
        n += len(ln) + 1
    return "\n".join(out)


def limits_section(tasks: list[dict], gaps: list[str], checks_note: str) -> dict:
    lines = []
    for t in tasks:
        if t.get("status") == "failed":
            why = "؛ ".join(_issues(t))
            lines.append(f"- القسم «{t.get('title')}» لم يجتز المراجعة" + (f": {why}" if why else "") + ". استخدمه بحذر.")
        elif t.get("status") in ("skipped", "pending", "running"):
            lines.append(f"- المهمة «{t.get('title')}» لم تُنفذ؛ لا يغطي التقرير هذا الجزء.")
    for g in gaps:
        lines.append(f"- {g}")
    if checks_note:
        lines.append(f"- {checks_note.strip().splitlines()[0][:400]}")
    text = "\n".join(lines) if lines else "- لا نواقص مسجلة: كل الأقسام اجتازت المراجعة ولم يوثق البحث نقصًا."
    need = [g for g in gaps]
    if need:
        text += "\n\n### بيانات مطلوبة لقرار موثوق\n" + "\n".join(f"- {g}" for g in need)
    return {"id": "limits", "kind": "limits", "title": "حدود هذا التقرير ونواقصه", "status": "verified", "text": text,
            "sources": [], "fp": fp("limits", text), "by": "code", "at": time.time()}


def sources_section(sections: list[dict], read: set[str]) -> dict:
    """Every link cited, the sections that cite it, and whether the system really read it."""
    where: dict[str, list[str]] = {}
    for s in sections:
        for u in s.get("sources") or []:
            where.setdefault(u, []).append(s["title"])
    rows = []
    for i, (u, secs) in enumerate(where.items(), 1):
        mark = "قرأه النظام" if u in read or u.rstrip("/") in read else "لم يقرأه النظام"
        rows.append(f"{i}. {u} — {mark} — يستند إليه: {'، '.join(dict.fromkeys(secs))}")
    text = "\n".join(rows) if rows else "لم يُستشهد بأي رابط في الأقسام."
    return {"id": "sources", "kind": "sources", "title": "المصادر وربطها بالأقسام", "status": "verified", "text": text,
            "sources": list(where), "fp": fp("sources", text), "by": "code", "at": time.time()}


def assemble(sections: list[dict]) -> str:
    """The report, rebuilt from saved sections only. The summary (if any) leads; task sections follow in plan order."""
    out = []
    for s in sections:
        if s["kind"] == "summary":
            out.append(s["text"].strip())
            continue
        head = f"## {s['title']}"
        body = s["text"].strip()
        if s["kind"] == "task" and s["status"] != "verified":
            reasons = "؛ ".join(s.get("issues") or [])
            body = (f"> تنبيه: هذا القسم لم يجتز المراجعة" + (f" ({reasons})" if reasons else "")
                    + "، فلا تعامل أرقامه كحقائق.\n\n" + body)
        out.append(f"{head}\n\n{body}")
    return "\n\n".join(x for x in out if x).strip()


def locate(issues: list[str], verdict: dict, sections: list[dict]) -> list[str]:
    """Which sections a rejection is about: arithmetic/plausibility findings carry the figures they flag, the
    reviewer's notes may name a section. Anything not located is about the summary (which ties sections together)."""
    hits: list[str] = []
    probes = [str(e.get("stated")) for e in verdict.get("arithmetic") or [] if e.get("stated") is not None]
    for i in verdict.get("plausibility") or []:
        if i.get("severity") == "error":
            probes += [str(x) for x in i.get("values") or []]
    for s in sections:
        if s["kind"] != "task":
            continue
        plain = re.sub(r"[,\u066c]", "", s["text"])
        if any(p and p.replace(".0", "") in plain for p in probes):
            hits.append(s["id"])
        elif any(s["title"] and s["title"] in i for i in issues):
            hits.append(s["id"])
    return list(dict.fromkeys(hits)) or ["summary"]


def file_record(name: str, content: str, fmt: str = "text/markdown") -> dict:
    data = content.encode("utf-8")
    return {"name": name, "format": fmt, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "created_at": time.time()}


def verify_file(rec: dict, content: str | None, must_contain: list[str]) -> bool:
    """A delivered file exists only if its content is stored, matches its checksum, decodes, and holds the report."""
    if not content or not rec:
        return False
    data = content.encode("utf-8")
    if len(data) != rec.get("bytes") or hashlib.sha256(data).hexdigest() != rec.get("sha256"):
        return False
    return all(m and m.strip()[:80] in content for m in must_contain)
