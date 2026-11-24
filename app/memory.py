"""Long-term memory for the orchestrator.

Each browser owns a private memory space (a random secret; the server only stores its SHA-256).
After a run, the orchestrator distils durable items (who the user is, their projects, decisions,
researched findings, a one-line task summary). Before the next run it recalls the relevant ones
(lexical BM25 tuned for Arabic + importance + recency, plus the latest task summaries for
follow-ups) and gives them to the planner. Items can supersede older ones; the user can list,
delete or clear everything.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from collections import Counter

import httpx

from . import store as sb
from .llm import LLM, LLMError

KINDS = ("profile", "project", "decision", "finding", "task")
MAX_ITEMS = 500
RECALL_LIMIT = 10
RECENT_TASKS = 2
CONTEXT_BUDGET = 3500  # characters handed to the planner

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|sbp_[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_\-]{20,}|"
                     r"\b(?=[A-Za-z]*\d)[A-Za-z0-9]{32,}\b|password|كلمة المرور|كلمة السر)", re.I)


def space_hash(space: str) -> str:
    return hashlib.sha256(space.encode()).hexdigest()


def valid_space(space: str | None) -> bool:
    return bool(space) and 20 <= len(space) <= 200 and re.fullmatch(r"[A-Za-z0-9_\-]+", space) is not None


# ---------------- text scoring (Arabic-aware) ----------------
_DIAC = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_STOP = set("في من على الى إلى عن مع هذا هذه ذلك التي الذي ما ماذا كيف هل او أو و ثم لا لم لن ان أن إن كان كانت "
            "يكون اريد أريد ابغى عندي لدي بين كل بعد قبل حتى عند the a an of to in on for and or is are with".split())


def _norm(word: str) -> str:
    w = _DIAC.sub("", word.lower())
    w = re.sub("[إأآا]", "ا", w).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    for p in ("وال", "بال", "كال", "فال", "لل", "ال"):
        if w.startswith(p) and len(w) - len(p) >= 3:
            w = w[len(p):]
            break
    for s in ("ات", "ون", "ين", "ها", "ه"):
        if w.endswith(s) and len(w) - len(s) >= 3:
            w = w[:-len(s)]
            break
    return w


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[\w\u0600-\u06FF]+", text or ""):
        if raw.lower() in _STOP or len(raw) < 2:
            continue
        out.append(_norm(raw))
    return out


def rank(query: str, items: list[dict], now: float | None = None) -> list[tuple[float, dict]]:
    """BM25 over items + small boosts for importance and recency."""
    now = now or time.time()
    q = set(tokens(query))
    docs = [tokens(i["content"]) for i in items]
    n = len(docs) or 1
    avg = sum(map(len, docs)) / n or 1
    df = Counter(t for d in docs for t in set(d))
    scored = []
    for item, d in zip(items, docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if s > 0:
            age_days = max(0.0, (now - float(item.get("created_at") or now)) / 86400)
            s *= 1 + 0.1 * int(item.get("importance") or 3)
            s *= 1 + 0.3 * math.exp(-age_days / 30)
        scored.append((s, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------- storage ----------------
class MemoryStore:
    """Supabase table public.memories when configured, otherwise process memory."""

    def __init__(self):
        self._mem: dict[str, list[dict]] = {}

    def _h(self):
        return {"apikey": sb.KEY, "Authorization": f"Bearer {sb.KEY}", "Content-Type": "application/json"}

    async def list(self, space: str) -> list[dict]:
        sh = space_hash(space)
        if not sb.enabled():
            return sorted(self._mem.get(sh, []), key=lambda i: -i["created_at"])
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{sb.URL}/rest/v1/memories", headers=self._h(),
                            params={"space": f"eq.{sh}", "order": "created_at.desc", "limit": str(MAX_ITEMS),
                                    "select": "id,kind,content,run_id,importance,created_at"})
        if r.status_code != 200:
            raise RuntimeError(f"memory list failed: {r.status_code} {r.text[:200]}")
        rows = r.json()
        for row in rows:  # timestamptz -> epoch seconds
            row["created_at"] = _epoch(row["created_at"])
        return rows

    async def add(self, space: str, items: list[dict]):
        if not items:
            return
        sh = space_hash(space)
        if not sb.enabled():
            self._mem.setdefault(sh, []).extend(items)
            return
        rows = [{**{k: i[k] for k in ("id", "kind", "content", "run_id", "importance")}, "space": sh,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i["created_at"]))} for i in items]
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{sb.URL}/rest/v1/memories", headers={**self._h(), "Prefer": "return=minimal"}, json=rows)
        if r.status_code >= 300:
            raise RuntimeError(f"memory add failed: {r.status_code} {r.text[:200]}")

    async def delete(self, space: str, ids: list[str]) -> int:
        if isinstance(ids, str):
            ids = [ids]
        ids = [i for i in ids if valid_id(i)]
        if not ids:
            return 0
        sh = space_hash(space)
        if not sb.enabled():
            before = len(self._mem.get(sh, []))
            self._mem[sh] = [i for i in self._mem.get(sh, []) if i["id"] not in ids]
            return before - len(self._mem[sh])
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(f"{sb.URL}/rest/v1/memories", headers={**self._h(), "Prefer": "return=representation"},
                               params={"space": f"eq.{sh}", "id": f"in.({','.join(ids)})"})
        return len(r.json()) if r.status_code == 200 else 0

    async def clear(self, space: str) -> int:
        sh = space_hash(space)
        if not sb.enabled():
            return len(self._mem.pop(sh, []))
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(f"{sb.URL}/rest/v1/memories", headers={**self._h(), "Prefer": "return=representation"},
                               params={"space": f"eq.{sh}"})
        return len(r.json()) if r.status_code == 200 else 0


def valid_id(i) -> bool:
    return isinstance(i, str) and 0 < len(i) <= 64 and all(ch.isalnum() or ch in "_-" for ch in i)


def _epoch(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    from datetime import datetime
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


STORE = MemoryStore()


# ---------------- recall ----------------
def recall(goal: str, items: list[dict]) -> list[dict]:
    """Relevant items for this goal: top lexical matches + latest task summaries + key profile items."""
    picked: dict[str, dict] = {}
    for s, it in rank(goal, [i for i in items if i["kind"] != "task"])[:RECALL_LIMIT]:
        if s > 0.5:
            picked[it["id"]] = it
    for it in [i for i in items if i["kind"] == "task"][:RECENT_TASKS]:  # follow-ups ("كمّل"، "طوّرها")
        picked[it["id"]] = it
    for s, it in rank(goal, [i for i in items if i["kind"] == "task"])[:2]:
        if s > 0.5:
            picked[it["id"]] = it
    for it in sorted((i for i in items if i["kind"] == "profile"), key=lambda i: -int(i.get("importance") or 3))[:3]:
        picked[it["id"]] = it
    return list(picked.values())


LABEL = {"profile": "عن المستخدم", "project": "مشروع", "decision": "قرار", "finding": "معلومة مبحوثة",
         "task": "مهمة سابقة"}


def render_context(items: list[dict]) -> str:
    lines, used = [], 0
    for it in items:
        date = time.strftime("%Y-%m-%d", time.gmtime(float(it.get("created_at") or time.time())))
        line = f"- [{LABEL.get(it['kind'], it['kind'])} · {date}] {it['content']}"
        if used + len(line) > CONTEXT_BUDGET:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ---------------- extraction ----------------
EXTRACT = """أنت مسؤول ذاكرة المنسق. بعد انتهاء مهمة، استخرج ما يستحق التذكر في مهام قادمة لنفس المستخدم.
أعد JSON فقط:
{"task_summary": "سطر واحد: ماذا طلب المستخدم وما الخلاصة الرئيسية (أرقام مهمة إن وجدت)",
 "add": [{"kind": "profile|project|decision|finding", "content": "جملة واحدة مستقلة مفهومة وحدها", "importance": 1-5}],
 "supersede": ["معرفات عناصر قديمة صارت خاطئة أو حلّ محلها عنصر جديد"]}
الأنواع: profile = من هو المستخدم وتفضيلاته (لغة، أسلوب، مدينة، مهنة). project = مشروع مستمر ومعطياته (ميزانية، تكاليف، أهداف).
decision = قرار اتخذه المستخدم. finding = معلومة بحثية مفيدة مع مصدرها.
قواعد: 0 إلى 6 عناصر فقط، الأهم أولًا. لا تكرر ما في الذاكرة الحالية بنفس المعنى. لا تحفظ معلومات عامة بديهية.
لا تحفظ أبدًا مفاتيح أو كلمات مرور أو بيانات دفع أو أرقام هويات. التقديرات تُكتب على أنها تقديرات."""


def _clean(text: str) -> str | None:
    text = " ".join(str(text or "").split())[:400]
    if len(text) < 8 or _SECRET.search(text):
        return None
    return text


async def extract_and_save(llm: LLM, space: str, run_id: str, goal: str, result: str,
                           existing: list[dict]) -> dict:
    related = [it for s, it in rank(goal + " " + result[:1500], existing)[:15] if s > 0]
    mem_list = "\n".join(f"{it['id']}: {it['content']}" for it in related) or "(فارغة)"
    try:
        out = await llm.chat_json([{"role": "system", "content": EXTRACT}, {"role": "user", "content":
                                   f"طلب المستخدم:\n{goal[:3000]}\n\nالجواب النهائي:\n{result[:6000]}\n\nالذاكرة الحالية ذات الصلة:\n{mem_list}"}],
                                  temperature=0.1, max_tokens=1200)
    except (ValueError, LLMError) as e:
        return {"saved": [], "superseded": [], "error": str(e)}
    now = time.time()
    new = []
    summary = _clean(out.get("task_summary"))
    if summary:
        new.append({"id": uuid.uuid4().hex[:16], "kind": "task", "content": summary, "run_id": run_id,
                    "importance": 3, "created_at": now})
    for a in (out.get("add") or [])[:6]:
        if not isinstance(a, dict):
            continue
        content = _clean(a.get("content"))
        kind = a.get("kind") if a.get("kind") in KINDS[:-1] else "finding"
        if content:
            try:
                imp = min(5, max(1, int(a.get("importance") or 3)))
            except (TypeError, ValueError):
                imp = 3
            new.append({"id": uuid.uuid4().hex[:16], "kind": kind, "content": content, "run_id": run_id,
                        "importance": imp, "created_at": now})
    valid_ids = {it["id"] for it in existing}
    sup = [str(i) for i in (out.get("supersede") or []) if str(i) in valid_ids]
    await STORE.add(space, new)
    await STORE.delete(space, sup)
    overflow = len(existing) - len(sup) + len(new) - MAX_ITEMS
    if overflow > 0:  # prune least important, oldest first
        keep_out = sorted((i for i in existing if i["id"] not in sup),
                          key=lambda i: (int(i.get("importance") or 3), float(i.get("created_at") or 0)))[:overflow]
        await STORE.delete(space, [i["id"] for i in keep_out])
    return {"saved": new, "superseded": sup}
