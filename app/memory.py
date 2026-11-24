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

import os

import httpx

from . import store as sb
from .llm import LLM, LLMError

KINDS = ("profile", "project", "decision", "finding", "task")

# Nature = where an item came from and how far it can be trusted. Kind says *what* it is about;
# nature says *who asserted it*. Only user_stated / user_confirmed may be attributed to the user.
NATURES = ("user_stated", "user_confirmed", "external_source", "task_output", "inferred", "assumption", "scenario",
           "unverified")
TRUST = {"user_confirmed": 5, "user_stated": 4, "external_source": 3, "task_output": 2, "inferred": 1, "assumption": 1,
         "unverified": 1, "scenario": 0}
# Shown to the user and the planner. Derived from the nature (not from the model) so it cannot be inflated.
CONFIDENCE = {"user_confirmed": 0.95, "user_stated": 0.9, "external_source": 0.7, "task_output": 0.5, "inferred": 0.4,
              "assumption": 0.3, "unverified": 0.2, "scenario": 0.0}
# Natures the user may set from the memory panel (user_stated needs a quote, external_source needs a URL).
USER_SETTABLE = ("user_confirmed", "assumption", "scenario")
USER_NATURES = ("user_stated", "user_confirmed")
NATURE_LABEL = {"user_stated": "صرّح به المستخدم", "user_confirmed": "أكده المستخدم", "external_source": "من مصدر خارجي",
                "task_output": "نتيجة أو توصية من الفريق", "inferred": "استنتاج غير مصرّح به",
                "assumption": "افتراض/تقدير", "scenario": "سيناريو اختباري/افتراضي", "unverified": "مصدر غير مؤكد"}
# Cues that a request is a test, example or hypothetical. False positives only make memory more cautious.
_HYPO = re.compile(r"(افترض|لنفترض|نفترض|بافتراض|افتراضي|سيناريو|تخيل|تخيّل|وهمي|وهمية|مثال تجريبي|بيانات اختبار|"
                   r"للاختبار|لغرض الاختبار|اختبار الذاكرة|اختبر الذاكرة|مشروع تجريبي|\bhypothetical\b|\bsuppose\b|"
                   r"\bimagine\b|\bdummy\b|\btest data\b|\bscenario\b)", re.I)
_ESTIMATE = re.compile(r"(تقدير|تقديري|تقريب|تقريبًا|تقريبا|متوقع|[أايتن]?توقع|يُتوقع|ربما|حوالي|نحو \d|قد يصل|افتراض|\bestimate)", re.I)
_URL = re.compile(r"https?://[^\s)\]}>\"'،]+")
_NUM = re.compile(r"\d[\d,٬.]*")
TABLE = os.environ.get("MEMORY_TABLE", "memories")  # override only for staging tests
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
    else:  # attached prepositions/conjunctions: لمشروعي، بجدة، وسعر
        if w[:1] in ("ب", "ل", "و", "ف", "ك") and len(w) >= 4:
            w = w[1:]
    for s in ("كم", "هم", "نا", "ني", "ات", "ون", "ين", "ها", "ي", "ك", "ه"):
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
    q = set(tokens(query)) - _GENERIC_Q
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


_GENERIC_Q = {"مشروع", "خطه", "اكتب", "اريد", "ساعد", "عمل"}


# ---------------- storage ----------------
class MemoryUnavailable(RuntimeError):
    """The memory backend refused or failed. Always surfaced to the user; never swallowed."""


class MemoryStore:
    """Supabase table public.memories when configured, otherwise process memory."""

    def __init__(self):
        self._mem: dict[str, list[dict]] = {}

    def _h(self):
        return {"apikey": sb.KEY, "Authorization": f"Bearer {sb.KEY}", "Content-Type": "application/json"}

    async def list(self, space: str) -> list[dict]:
        sh = space_hash(space)
        if not sb.enabled():
            return sorted((normalise(dict(i)) for i in self._mem.get(sh, [])), key=lambda i: -i["created_at"])
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{sb.URL}/rest/v1/{TABLE}", headers=self._h(),
                            params={"space": f"eq.{sh}", "order": "created_at.desc", "limit": str(MAX_ITEMS),
                                    "select": "id,kind,content,run_id,importance,created_at," + ",".join(META)})
            if r.status_code == 400 and "does not exist" in r.text:  # table not migrated yet: read legacy rows
                r = await c.get(f"{sb.URL}/rest/v1/{TABLE}", headers=self._h(),
                                params={"space": f"eq.{sh}", "order": "created_at.desc", "limit": str(MAX_ITEMS),
                                        "select": "id,kind,content,run_id,importance,created_at"})
        if r.status_code != 200:
            raise MemoryUnavailable(f"memory list failed: {r.status_code} {r.text[:200]}")
        rows = r.json()
        for row in rows:  # timestamptz -> epoch seconds; legacy rows have no provenance -> unverified
            row["created_at"] = _epoch(row["created_at"])
            normalise(row)
        return rows

    async def add(self, space: str, items: list[dict]):
        if not items:
            return
        sh = space_hash(space)
        if not sb.enabled():
            self._mem.setdefault(sh, []).extend(items)
            return
        rows = [{**{k: i.get(k) for k in ("id", "kind", "content", "run_id", "importance") + META}, "space": sh,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i["created_at"]))} for i in items]
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{sb.URL}/rest/v1/{TABLE}", headers={**self._h(), "Prefer": "return=minimal"}, json=rows)
        if r.status_code >= 300:
            raise MemoryUnavailable(f"memory add failed: {r.status_code} {r.text[:200]}")

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
            r = await c.delete(f"{sb.URL}/rest/v1/{TABLE}", headers={**self._h(), "Prefer": "return=representation"},
                               params={"space": f"eq.{sh}", "id": f"in.({','.join(ids)})"})
        if r.status_code != 200:
            raise MemoryUnavailable(f"memory delete failed: {r.status_code} {r.text[:200]}")
        return len(r.json())

    async def update(self, space: str, item_id: str, changes: dict) -> dict | None:
        """User edit from the memory panel. Returns the updated item or None if not found."""
        if not valid_id(item_id):
            return None
        changes = {k: v for k, v in changes.items() if k in ("content", "nature", "subject")}
        sh = space_hash(space)
        if not sb.enabled():
            for it in self._mem.get(sh, []):
                if it["id"] == item_id:
                    it.update(changes)
                    return normalise(dict(it))
            return None
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.patch(f"{sb.URL}/rest/v1/{TABLE}", headers={**self._h(), "Prefer": "return=representation"},
                              params={"space": f"eq.{sh}", "id": f"eq.{item_id}"}, json=changes)
        if r.status_code != 200:
            raise MemoryUnavailable(f"memory update failed: {r.status_code} {r.text[:200]}")
        if not r.json():
            return None
        row = r.json()[0]
        row.pop("space", None)
        row["created_at"] = _epoch(row["created_at"])
        return normalise(row)

    async def clear(self, space: str) -> int:
        sh = space_hash(space)
        if not sb.enabled():
            return len(self._mem.pop(sh, []))
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.delete(f"{sb.URL}/rest/v1/{TABLE}", headers={**self._h(), "Prefer": "return=representation"},
                               params={"space": f"eq.{sh}"})
        if r.status_code != 200:
            raise MemoryUnavailable(f"memory clear failed: {r.status_code} {r.text[:200]}")
        return len(r.json())


META = ("nature", "subject", "evidence", "source_goal")


def normalise(it: dict) -> dict:
    if it.get("nature") not in NATURES:
        it["nature"] = "unverified"
    for k in ("subject", "evidence", "source_goal"):
        it.setdefault(k, None)
    it["confidence"] = CONFIDENCE[it["nature"]]
    return it


def valid_id(i) -> bool:
    return isinstance(i, str) and 0 < len(i) <= 64 and all(ch.isalnum() or ch in "_-" for ch in i)


def _epoch(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    from datetime import datetime
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


STORE = MemoryStore()


# ---------------- recall ----------------
_GENERIC = set(tokens("مشروع مشروعي شركة شركتي متجر متجري عمل نشاط خطة دراسة"))


def subject_key(subject: str | None) -> str:
    return " ".join(sorted(set(tokens(subject or "")) - _GENERIC))


def _subject_hits(goal: str, items: list[dict]) -> set[str]:
    """Subjects explicitly named in the goal (by their distinctive words)."""
    g = set(tokens(goal))
    hits = set()
    for it in items:
        key = subject_key(it.get("subject"))
        if key and set(key.split()) & g:
            hits.add(key)
    return hits


def recall(goal: str, items: list[dict], scenario: bool = False) -> list[dict]:
    """Relevant items for this goal, never mixing projects and never letting test data pose as the user's.

    - Scenario items are recalled only when the current request is itself a scenario/test, or names them.
    - If the goal names a subject, items about *other* subjects are dropped.
    - If it names none and the user has several projects, we do not guess which one "my project" means.
    """
    items = [normalise(dict(i)) for i in items]
    scenario = scenario or bool(_HYPO.search(goal or ""))
    focus = _subject_hits(goal, items)

    def allowed(it):
        key = subject_key(it.get("subject"))
        if focus and key and key not in focus:
            return False  # a different project
        if it["nature"] == "scenario" and not scenario:
            return False  # test data stays out of real work; only a test/hypothetical request may use it
        return True

    pool = [i for i in items if allowed(i)]
    real_subjects = {subject_key(i.get("subject")) for i in pool
                     if i["kind"] == "project" and i["nature"] in USER_NATURES and subject_key(i.get("subject"))}
    picked: dict[str, dict] = {}
    for s, it in rank(goal, [i for i in pool if i["kind"] != "task"])[:RECALL_LIMIT]:
        if s > 0.5:
            picked[it["id"]] = it
    for s, it in rank(goal, [i for i in pool if i["kind"] == "task"])[:2]:
        if s > 0.5:
            picked[it["id"]] = it
    for it in [i for i in pool if i["kind"] == "task"][:RECENT_TASKS]:  # follow-ups ("كمّل"، "طوّرها")
        picked[it["id"]] = it
    for it in sorted((i for i in pool if i["kind"] == "profile" and i["nature"] in USER_NATURES),
                     key=lambda i: -int(i.get("importance") or 3))[:3]:
        picked[it["id"]] = it
    # "مشروعي": add the user's own project facts only when it is unambiguous which project is meant.
    if focus or len(real_subjects) == 1:
        own = [i for i in pool if i["kind"] == "project" and i["nature"] in USER_NATURES]
        for it in sorted(own, key=lambda i: (-int(i.get("importance") or 3), -float(i.get("created_at") or 0)))[:3]:
            picked[it["id"]] = it
    out = list(picked.values())
    if not focus and len(real_subjects) > 1:
        out = [i for i in out if not (i["kind"] == "project" and subject_key(i.get("subject")))]
        names = sorted({i.get("subject") for i in pool if i["kind"] == "project" and i["nature"] in USER_NATURES
                        and i.get("subject")})
        out.append({"id": "_ambiguous", "kind": "note", "nature": "unverified", "subject": None, "evidence": None,
                    "source_goal": None, "run_id": None, "importance": 5, "created_at": time.time(),
                    "content": "للمستخدم أكثر من مشروع في الذاكرة (" + "، ".join(names) + ") والطلب لا يحدد أيها؛ "
                               "لا تفترض واحدًا منها، واطلب التوضيح أو اجعل الجواب عامًا."})
    return out


LABEL = {"profile": "عن المستخدم", "project": "مشروع", "decision": "قرار", "finding": "معلومة",
         "task": "مهمة سابقة", "note": "تنبيه"}

SECTIONS = [
    (USER_NATURES, "حقائق صرّح بها المستخدم نفسه (يجوز نسبتها إليه، مع ذكر أنها من مهمة سابقة إن لزم)"),
    (("external_source",), "معلومات من مصادر خارجية (انسبها إلى مصدرها المذكور، لا إلى المستخدم، وقد تكون تغيّرت منذ تاريخها)"),
    (("task_output",), "نتائج وتوصيات من مهام سابقة (ليست تصريحات من المستخدم: لا تقل «مشروعك» أو «قررتَ» بسببها؛ قل «في مهمة سابقة ورد أن...»)"),
    (("inferred", "assumption"), "استنتاجات وافتراضات وتقديرات (تبقى افتراضات: اذكر أنها تقديرية ولا تبنِ عليها كأنها حقائق)"),
    (("scenario",), "بيانات اختبارية أو سيناريو افتراضي (ليست بيانات المستخدم إطلاقًا: لا تنسبها إليه ولا تطلب منه بيانات حقيقية بناءً عليها)"),
    (("unverified",), "معلومات قديمة مصدرها غير مؤكد (لا تنسبها للمستخدم؛ اذكر أنها غير مؤكدة أو اطلب التأكيد إن احتجتها)"),
]

CONTEXT_HEADER = ("سياق من ذاكرة المنسق. كل سطر موسوم بطبيعته ومصدره، ولا يعني وجوده أنه حقيقة عن المستخدم. "
                  "الطلب الحالي يتقدم على الذاكرة عند التعارض. عند نقل أي معلومة إلى وكيل أو إلى الجواب، انقل وسمها معها.")


def _line(it: dict) -> str:
    date = time.strftime("%Y-%m-%d", time.gmtime(float(it.get("created_at") or time.time())))
    tags = [LABEL.get(it["kind"], it["kind"])]
    if it.get("subject"):
        tags.append(f"موضوع: {it['subject']}")
    if it.get("run_id"):
        tags.append(f"مهمة {it['run_id']} · {date}")
    line = f"- [{' · '.join(tags)}] {it['content']}"
    if it.get("evidence") and it["nature"] == "external_source":
        line += f" (المصدر: {it['evidence'][:200]})"
    elif it.get("evidence"):
        line += f" (نص المستخدم: «{it['evidence'][:160]}»)"
    return line


def render_context(items: list[dict]) -> str:
    items = [normalise(dict(i)) for i in items]
    out, used = [CONTEXT_HEADER], 0
    notes = [i for i in items if i["kind"] == "note"]
    for n in notes:
        out.append(f"تنبيه: {n['content']}")
    for natures, title in SECTIONS:
        group = [i for i in items if i["nature"] in natures and i["kind"] != "note"]
        if not group:
            continue
        lines = []
        for it in group:
            line = _line(it)
            if used + len(line) > CONTEXT_BUDGET:
                break
            lines.append(line)
            used += len(line)
        if lines:
            out.append(f"### {title}\n" + "\n".join(lines))
    return "\n\n".join(out)


# ---------------- extraction ----------------
EXTRACT = """أنت مسؤول ذاكرة المنسق. بعد انتهاء مهمة، استخرج ما يستحق التذكر في مهام قادمة، مع تحديد طبيعة كل معلومة ومصدرها بدقة.
أعد JSON فقط:
{"task_summary": "سطر واحد: ماذا طُلب وما الخلاصة (أرقام مهمة إن وجدت)",
 "scenario": false,
 "add": [{"kind": "profile|project|decision|finding", "nature": "user_stated|external_source|task_output|inferred|assumption|scenario",
          "subject": "اسم قصير للمشروع أو الموضوع الذي تخصه المعلومة، أو null", "evidence": "user_stated: اقتباس حرفي من طلب المستخدم. external_source: رابط المصدر كما ورد في الجواب",
          "content": "جملة واحدة مستقلة مفهومة وحدها", "importance": 1-5}],
 "supersede": ["معرفات عناصر قديمة صارت خاطئة أو حلّ محلها عنصر جديد عن نفس الموضوع"]}
الطبيعة:
- user_stated: قالها المستخدم صراحة عن نفسه أو مشروعه في طلبه. يجب أن تضع في evidence نصه الحرفي.
- external_source: معلومة مأخوذة من مصدر خارجي (موقع، تقرير) ورد رابطه في الجواب. ضع الرابط في evidence.
- inferred: استنتاج عن المستخدم أو مشروعه لم يصرّح به حرفيًا.
- task_output: نتيجة أو توصية أو حساب أنتجه الفريق في الجواب (ليست قرارًا من المستخدم ولا معلومة عنه).
- assumption: تقدير أو افتراض أو رقم تقريبي، سواء من المستخدم أو من الفريق.
- scenario: الطلب مثال أو اختبار أو سيناريو افتراضي أو «لنفترض»، وليس وصفًا لواقع المستخدم.
scenario=true إن كان الطلب كله اختبارًا أو مثالًا أو سيناريو افتراضيًا؛ عندها كل العناصر scenario.
الأنواع: profile = من هو المستخدم وتفضيلاته (فقط إن قالها هو). project = مشروع ومعطياته. decision = قرار اتخذه المستخدم نفسه (توصيات الفريق ليست قرارات). finding = معلومة مفيدة مع مصدرها.
قواعد: اكتب task_summary وcontent بلغة طلب المستخدم. 0 إلى 6 عناصر، الأهم أولًا. لا تكرر ما في الذاكرة الحالية بنفس المعنى. لا تحفظ معلومات عامة بديهية.
لا تحفظ أبدًا مفاتيح أو كلمات مرور أو بيانات دفع أو أرقام هويات. إن شككت في الطبيعة فاختر الأضعف (task_output أو assumption)."""


def _clean(text: str) -> str | None:
    text = " ".join(str(text or "").split())[:400]
    if len(text) < 8 or _SECRET.search(text):
        return None
    return text


def _flat(text: str) -> str:
    return " ".join(_norm(w) for w in re.findall(r"[\w\u0600-\u06FF]+", text or ""))


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _nums(text: str) -> set[str]:
    """Numbers in a text, normalised so that 35,000 / ٣٥٠٠٠ / 35 ألف compare equal."""
    out = set()
    for m in re.finditer(r"(\d[\d,٬]*(?:\.\d+)?)\s*(ألف|الف|آلاف|مليون)?", (text or "").translate(_DIGITS)):
        n = m.group(1).replace(",", "").replace("٬", "")
        out.add(n)
        if m.group(2):
            mult = 1_000_000 if m.group(2) == "مليون" else 1000
            v = float(n) * mult
            out.add(str(int(v)) if v == int(v) else str(v))
    return out


def is_scenario_request(goal: str) -> bool:
    return bool(_HYPO.search(goal or ""))


def classify(a: dict, goal: str, scenario: bool, result: str = "") -> dict | None:
    """Turn one proposed item into a stored item, enforcing provenance rules in code rather than trusting the model.

    - In a scenario/test run, everything is scenario. Full stop.
    - user_stated needs evidence that really appears in the user's request, and every number in the
      content must appear in that request; otherwise it is downgraded to task_output.
    - Estimates stay assumptions. profile/decision can only come from the user.
    """
    content = _clean(a.get("content"))
    if not content:
        return None
    kind = a.get("kind") if a.get("kind") in KINDS[:-1] else "finding"
    nature = a.get("nature") if a.get("nature") in ("user_stated", "external_source", "task_output", "inferred",
                                                     "assumption", "scenario") else "task_output"
    evidence = _clean(str(a.get("evidence") or "").strip(" \"'«»“”")) if nature in ("user_stated", "external_source") else None
    if scenario:
        nature, evidence = "scenario", None
    elif nature == "external_source":
        urls = _URL.findall(evidence or "")
        if not urls or urls[0].rstrip(".,") not in (result or ""):  # the source must really be in the answer
            nature, evidence = "task_output", None
        else:
            evidence = urls[0].rstrip(".,")[:300]
    elif nature == "user_stated":
        backed = evidence and _flat(evidence) and _flat(evidence) in _flat(goal)
        if not backed or not _nums(content) <= _nums(goal):
            nature, evidence = "task_output", None
    if nature in ("task_output", "user_stated") and _ESTIMATE.search(content):
        nature = "assumption"  # an estimate stays an estimate, even the user's own (evidence kept)
    if kind in ("profile", "decision") and nature not in USER_NATURES:
        if kind == "profile":
            return None  # facts about the user must come from the user
        kind = "finding"  # a team recommendation is not the user's decision
    subject = _clean(a.get("subject")) if a.get("subject") not in (None, "null", "") else None
    try:
        imp = min(5, max(1, int(a.get("importance") or 3)))
    except (TypeError, ValueError):
        imp = 3
    return {"kind": kind, "nature": nature, "subject": subject[:80] if subject else None, "evidence": evidence,
            "content": content, "importance": imp}


def can_supersede(new: dict, old: dict) -> bool:
    """Weaker sources never overwrite stronger ones, test data never overwrites real data, projects never cross."""
    old = normalise(dict(old))
    if (new["nature"] == "scenario") != (old["nature"] == "scenario"):
        return False
    if subject_key(new.get("subject")) and subject_key(old.get("subject")) and \
            subject_key(new.get("subject")) != subject_key(old.get("subject")):
        return False
    return TRUST[new["nature"]] >= TRUST[old["nature"]]


async def extract_and_save(llm: LLM, space: str, run_id: str, goal: str, result: str,
                           existing: list[dict], scenario: bool = False) -> dict:
    existing = [normalise(dict(i)) for i in existing]
    related = [it for s, it in rank(goal + " " + result[:1500], existing)[:15] if s > 0]
    mem_list = "\n".join(f"{it['id']} [{it['nature']}{' · ' + it['subject'] if it.get('subject') else ''}]: {it['content']}"
                         for it in related) or "(فارغة)"
    try:
        out = await llm.chat_json([{"role": "system", "content": EXTRACT}, {"role": "user", "content":
                                   f"طلب المستخدم:\n{goal[:3000]}\n\nالجواب النهائي:\n{result[:6000]}\n\nالذاكرة الحالية ذات الصلة:\n{mem_list}"}],
                                  temperature=0.1, max_tokens=1400)
    except (ValueError, LLMError) as e:
        return {"saved": [], "superseded": [], "error": str(e)}
    scenario = scenario or is_scenario_request(goal) or out.get("scenario") is True
    now, src = time.time(), " ".join(goal.split())[:200]
    new = []
    summary = _clean(out.get("task_summary"))
    if summary:
        new.append({"kind": "task", "nature": "scenario" if scenario else "task_output", "subject": None,
                    "evidence": None, "content": summary, "importance": 3})
    for a in (out.get("add") or [])[:6]:
        if isinstance(a, dict) and (c := classify(a, goal, scenario, result)):
            new.append(c)
    for it in new:
        it.update(id=uuid.uuid4().hex[:16], run_id=run_id, created_at=now, source_goal=src)
    by_id = {it["id"]: it for it in existing}
    sup = []
    for i in (out.get("supersede") or []):
        old = by_id.get(str(i))
        # a supersede is honoured only if some new item about the same thing is allowed to replace it
        if old and any(n["kind"] != "task" and can_supersede(n, old) for n in new):
            sup.append(old["id"])
    await STORE.add(space, new)
    await STORE.delete(space, sup)
    overflow = len(existing) - len(sup) + len(new) - MAX_ITEMS
    if overflow > 0:  # prune least trusted, least important, oldest first
        keep_out = sorted((i for i in existing if i["id"] not in sup),
                          key=lambda i: (TRUST[i["nature"]], int(i.get("importance") or 3),
                                         float(i.get("created_at") or 0)))[:overflow]
        await STORE.delete(space, [i["id"] for i in keep_out])
    return {"saved": new, "superseded": sup, "scenario": scenario}
