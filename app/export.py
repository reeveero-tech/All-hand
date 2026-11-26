"""Turn a run into a standalone report file the user can keep (Markdown), built only from what the
run actually recorded: the answer, the plan, each task's verification, its sources and its limits."""
from __future__ import annotations

import datetime as dt
import re

STATUS = {"done": "اكتملت", "partial": "اكتملت جزئيًا", "failed": "فشلت", "cancelled": "أوقفها المستخدم",
          "interrupted": "انقطعت", "running": "ما زالت تعمل",
          "paused_quota": "متوقفة مؤقتًا: نفدت حصة النموذج (العمل محفوظ)", "paused_provider": "متوقفة مؤقتًا: المزود غير متاح (العمل محفوظ)"}
TASK = {"done": "مُتحقق منها", "failed": "لم تجتز التحقق", "skipped": "لم تُنفذ", "pending": "لم تبدأ",
        "running": "لم تكتمل"}
_URL = re.compile(r"^https?://", re.I)


def _when(ts) -> str:
    try:
        return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return "-"


def _cell(s) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def _kept(t: dict) -> list[dict]:
    """The attempts whose output the run actually used: the passing one, or the one kept for a failed task."""
    atts = t.get("attempts") or []
    if t.get("best_attempt"):
        return atts[t["best_attempt"] - 1:t["best_attempt"]]
    return atts[-1:]


def sources(run: dict) -> tuple[list[str], list[str]]:
    """(pages an agent really opened, links cited without being opened), from the attempts that were used."""
    read: list[str] = []
    cited: list[str] = []
    for t in (run.get("tasks") or {}).values():
        for a in _kept(t):
            for s in a.get("read") or []:
                s = str(s).strip()
                if _URL.match(s) and s not in read:
                    read.append(s)
            for s in a.get("sources") or []:
                s = str(s).strip()
                if _URL.match(s) and s not in cited:
                    cited.append(s)
    return read, [u for u in cited if u not in read]


def last_score(t: dict):
    for a in reversed(t.get("attempts") or []):
        v = a.get("verdict") or {}
        if v.get("score") is not None:
            return v["score"]
    return None


def to_markdown(run: dict) -> str:
    tasks = list((run.get("tasks") or {}).values())
    v = run.get("verdict") or {}
    llm = run.get("llm") or {}
    out = [f"# {run.get('goal', '').strip().splitlines()[0][:200] if run.get('goal') else 'مهمة'}", "",
           f"- **المعرّف:** `{run.get('id')}`",
           f"- **الحالة:** {STATUS.get(run.get('status'), run.get('status'))}",
           f"- **البدء:** {_when(run.get('created_at'))} — **الانتهاء:** {_when(run.get('finished_at'))}",
           f"- **النموذج:** {llm.get('provider', '-')} / {llm.get('model', '-')}",
           f"- **الوكلاء الذين عملوا:** {run.get('agents_spawned', 0)}",
           f"- **التحقق النهائي:** {str(v.get('score')) + '/10' if v.get('score') is not None else 'لم يُجرَ'}"]
    if run.get("follow_up_of"):
        out.append(f"- **متابعة للمهمة:** `{run['follow_up_of']}`")
    if run.get("scenario"):
        out.append("- **تنبيه:** مهمة تجريبية/افتراضية؛ معطياتها ليست حقائق عن المستخدم.")
    out += ["", "## الطلب", "", (run.get("goal") or "").strip(), ""]
    checks = run.get("goal_checks") or []
    if checks:
        out += ["### تدقيق أرقام الطلب (محسوبة بالحاسبة الدقيقة)", ""]
        for c in checks:
            mark = "صحيح" if c.get("ok") else f"خطأ: المذكور {c.get('stated'):,} والصحيح {c.get('correct'):,} {c.get('unit', '')}"
            out.append(f"- «{_cell(c.get('text'))}» — `{_cell(c.get('expr'))}` — {mark}")
        out.append("")
    out += ["## الجواب النهائي", "",
            (run.get("result") or "").strip() or "_لا يوجد جواب نهائي._", ""]
    if tasks:
        out += ["## الخطة وما نُفذ منها", "", "| المهمة | الدور | الحالة | تقييم المدقق | المحاولات |", "|---|---|---|---|---|"]
        for t in tasks:
            s = last_score(t)
            out.append(f"| {_cell(t.get('id'))} — {_cell(t.get('title'))} | {_cell(t.get('role'))} | "
                       f"{TASK.get(t.get('status'), t.get('status'))} | {str(s) + '/10' if s is not None else '-'} | "
                       f"{len(t.get('attempts') or [])} |")
        out.append("")
    pc = run.get("prior_check") or {}
    if pc.get("ledger") is not None:
        from .quality import TAGS
        out += ["## ما ورثته هذه المتابعة من النتيجة السابقة", ""]
        for i in pc.get("issues") or []:
            out.append(f"- {'مرفوض' if i.get('severity') == 'error' else 'تنبيه'}: {_cell(i.get('reason'))}")
        counts: dict = {}
        for it in pc.get("ledger") or []:
            counts[it["tag"]] = counts.get(it["tag"], 0) + 1
        out += [f"- {TAGS.get(k, k)}: {n} سطرًا" for k, n in counts.items()] + [""]
    plaus = v.get("plausibility") or []
    if plaus:
        out += ["## فحص معقولية الأرقام في الجواب النهائي", ""]
        out += [f"- {'خطأ' if i.get('severity') == 'error' else 'تنبيه'}: {_cell(i.get('reason'))}" for i in plaus] + [""]
    rs = run.get("research") or {}
    if rs:
        out += ["## قياس البحث في هذا التشغيل", "",
                f"- عمليات البحث: {rs.get('search_calls', 0)}، أعادت نتائج: {rs.get('search_ok', 0)}، مجموع النتائج: {rs.get('results', 0)}",
                f"- المحركات: " + ("، ".join(f"{k} ({n})" for k, n in (rs.get("providers") or {}).items()) or "-"),
                f"- صفحات قُرئت فعلًا: {rs.get('pages_read', 0)}؛ منها مذكور في الجواب: {rs.get('sources_in_answer', 0)}",
                f"- أسطر رقمية في الجواب: {rs.get('numeric_lines', 0)}، بلا وسم مصدر أو تقدير: {rs.get('numeric_lines_untagged', 0)}", ""]
    read, cited = sources(run)
    out += ["## المصادر", ""]
    if read:
        out += ["صفحات فتحها الوكلاء فعلًا:", ""] + [f"{i}. {u}" for i, u in enumerate(read, 1)] + [""]
    if cited:
        out += ["روابط ذكرها الوكلاء دون أن يفتحوها (لم يُتحقق منها):", ""] + [f"- {u}" for u in cited] + [""]
    if not read and not cited:
        out += ["لم يسجل الوكلاء أي رابط مصدر.", ""]
    gaps = [t for t in tasks if t.get("status") != "done"]
    issues = [i for i in (v.get("issues") or []) if i]
    out += ["## حدود هذا التقرير", ""]
    if not gaps and not issues and run.get("status") == "done":
        out.append("كل المهام اجتازت التحقق، ولم يسجل المدقق النهائي ملاحظات.")
    for t in gaps:
        out.append(f"- المهمة {t.get('id')} ({t.get('title')}): {TASK.get(t.get('status'), t.get('status'))}.")
    for i in issues:
        out.append(f"- ملاحظة المدقق النهائي: {i}")
    out += ["- التقرير مبني فقط على ما سجله التشغيل؛ تحقّق من الأرقام الحساسة قبل اتخاذ قرار.", ""]
    done = [t for t in tasks if t.get("output")]
    if done:
        out += ["## ملحق: ناتج كل مهمة", ""]
        for t in done:
            out += [f"### {t.get('id')} — {t.get('title')} ({TASK.get(t.get('status'), t.get('status'))})", "",
                    str(t.get("output")).strip(), ""]
    return "\n".join(out).rstrip() + "\n"
