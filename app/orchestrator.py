"""The orchestrator: plan -> dispatch specialist agents in parallel -> verify -> retry -> synthesize.

Large subtasks can be delegated to a sub-team (a nested orchestration), so one request can
spawn many agents, bounded by a per-run agent budget. Every step is streamed as an event.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from . import memory
from .agents import ROLES, run_agent
from .llm import LLM, LLMError

MAX_DEPTH = 2
MAX_TASKS_PER_PLAN = 10
MAX_ATTEMPTS = 3
APPROVAL_TIMEOUT = float(os.environ.get("PLAN_APPROVAL_TIMEOUT", "1800"))  # seconds a plan waits for its owner
DEFAULT_MAX_AGENTS = int(os.environ.get("MAX_AGENTS_PER_RUN", "60"))


@dataclass
class Run:
    goal: str
    llm_public: dict
    max_agents: int = DEFAULT_MAX_AGENTS
    can_exec: bool = False
    memory_space: str | None = None
    saved: bool | None = None  # None: no persistent store; False: final save failed (shown to the user)
    owner: str | None = None  # SHA-256 of the creating client's key; never sent back to clients
    follow_up_of: str | None = None  # previous run of the same conversation, when this is a follow-up
    thread_id: str | None = None  # first run of the conversation; every follow-up shares it
    context: str = ""  # what the previous run produced, given to the planner (kept so resume rebuilds it)
    resumed: dict | None = None  # {"from": run id, "reused": [task ids]} when continuing a stopped run
    approve_plan: bool = False  # pause after planning until the owner approves, edits or rejects the plan
    awaiting: str | None = None  # "plan" while waiting for that decision (status stays "running")
    plan_decision: dict | None = None  # what the owner decided: {"action", "dropped", "note", "at"}
    scenario: bool = False  # user marked this run as a test/hypothetical: nothing it learns is about the user
    goal_checks: list | None = None  # arithmetic the user stated in the request, recomputed exactly (None: not checked)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    status: str = "running"  # running | done | partial | failed | cancelled | interrupted (server shutdown)
    tasks: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    result: str = ""
    verdict: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    agents_spawned: int = 0
    memory: dict = field(default_factory=lambda: {"enabled": False, "recalled": [], "saved": [], "superseded": []})
    error: str = ""
    finished_at: float | None = None
    _subs: list = field(default_factory=list, repr=False)
    _decision: asyncio.Event | None = field(default=None, repr=False)

    async def emit(self, type_: str, data: dict):
        ev = {"seq": len(self.events), "t": round(time.time() - self.created_at, 2), "type": type_, **data}
        self.events.append(ev)
        for q in list(self._subs):
            q.put_nowait(ev)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._subs:
            self._subs.remove(q)

    def summary(self) -> dict:
        return {"id": self.id, "goal": self.goal, "status": self.status, "created_at": self.created_at,
                "finished_at": self.finished_at, "agents_spawned": self.agents_spawned,
                "thread_id": self.thread_id, "follow_up_of": self.follow_up_of, "awaiting": self.awaiting,
                "tasks_total": len(self.tasks),
                "tasks_done": sum(1 for t in self.tasks.values() if t["status"] == "done"),
                "tasks_failed": sum(1 for t in self.tasks.values() if t["status"] in ("failed", "skipped"))}

    def full(self) -> dict:
        return {**self.summary(), "llm": self.llm_public, "max_agents": self.max_agents, "tasks": self.tasks,
                "result": self.result, "verdict": self.verdict, "memory": self.memory, "usage": self.usage, "error": self.error, "saved": self.saved,
                "resumed": self.resumed, "approve_plan": self.approve_plan, "plan_decision": self.plan_decision, "context": self.context, "scenario": self.scenario, "goal_checks": self.goal_checks, "events": self.events}


ROLE_LIST = "\n".join(f"- {r.id}: {r.name} — {r.mission}" for r in ROLES.values())
NO_EXEC = " (تشغيل الكود معطّل على هذا الخادم: لا تختر هذا الدور؛ للحسابات اختر analyst الذي يملك حاسبة دقيقة)"


def role_list(can_exec: bool = True) -> str:
    return "\n".join(f"- {r.id}: {r.name} — {r.mission}" + ("" if can_exec or "run_python" not in r.tools or len(r.tools) > 1
                                                          else NO_EXEC) for r in ROLES.values())


FOLLOW_UP_PLAN = ("هذا طلب متابعة، ونتيجة المهمة السابقة مرفقة أدناه ويراها كل وكيل. لا تخطط لإعادة البحث أو التحليل الذي أُنجز؛ "
                  "خطط فقط لما يتطلبه التغيير الذي طلبه المستخدم الآن (غالبًا مهمة إلى ثلاث مهام)، ولا تضف بحثًا جديدًا إلا إن طلبه صراحةً "
                  "أو كانت النتيجة السابقة تخلو تمامًا مما يلزم.\n\n")

PLANNER = """أنت المنسق الرئيسي لفريق وكلاء ذكاء اصطناعي. حوّل طلب المستخدم إلى خطة تنفيذ.
الأدوار المتاحة:
{roles}

أعد كائن JSON فقط:
{{"simple": bool, "summary": "فهمك للهدف في جملة", "tasks": [
  {{"id": "t1", "title": "عنوان قصير", "role": "<role id>", "instructions": "تعليمات كاملة مستقلة",
    "acceptance": "معيار قبول قابل للتحقق", "depends_on": [], "decompose": false}}
]}}
قواعد:
- simple=true ومهمة واحدة بدور assistant للتحية والأسئلة البسيطة.
- غير ذلك: من 2 إلى {max_tasks} مهام، أقل عدد كافٍ. اجعل المهام المستقلة متوازية (depends_on فارغ).
- depends_on يشير فقط إلى معرفات سابقة في القائمة.
- كل مهمة تبني على ناتج مهمة أخرى (خطة أو تقرير مالي أو توصية تعتمد على بحث أو تحليل) يجب أن تذكرها في depends_on، وإلا لن ترى نتائجها. الاستقلال فقط للمهام التي لا تحتاج نواتج غيرها فعلًا.
- لا تجعل المهام سلسلة واحدة بلا داعٍ: اجعل جمع المعلومات المستقلة مهام متوازية (مثل المنافسين، والأسعار، والطلب)، واجعل كل مهمة تعتمد فقط على ما تحتاج ناتجه.
- اذكر في تعليمات كل مهمة موضوع الطلب ومعطياته الأساسية (المنتج، السوق، الميزانية...) صراحةً.
- اجعل معيار القبول قابلًا للتحقق بما يُنشر علنًا عادة. لا تشترط بيانات لا تُنشر غالبًا (كحصص السوق الدقيقة أو مبيعات المنافسين)؛ اطلب بدلها «تقديرًا موسومًا بمنهجه أو غير متاح».
- decompose=true فقط لمهمة ضخمة تحتاج فريقًا فرعيًا كاملًا{decompose_note}.
- لا تضف مهمة "تجميع نهائي"؛ المنسق يجمع النتائج بنفسه.
- الوكلاء ينتجون نصًا بتنسيق Markdown (جداول وعناوين)، ولا يملكون أداة لإنشاء ملفات PDF أو Word أو صور أو رسوم بيانية. لا تشترط في معيار القبول ملفًا أو رسمًا؛ إن طلب المستخدم «ملفًا نهائيًا» فالنظام يصدّر الجواب المجمّع تقريرًا قابلًا للتنزيل تلقائيًا.
- إن أُرفق «سياق من ذاكرة المنسق» فاستخدم ما يتصل بالطلب فقط: انقل المعطيات ذات الصلة صراحةً إلى تعليمات المهام لأن الوكلاء لا يرون سياق الذاكرة، وانقل معها وسمها كما هو (صرّح به المستخدم / نتيجة مهمة سابقة / افتراض / سيناريو اختباري / غير مؤكد). لا تحوّل نتيجة مهمة أو افتراضًا أو سيناريو إلى «معطيات المستخدم».
- إن أُرفق «سياق المحادثة السابقة» فهذا طلب متابعة: ابنِ على النتيجة السابقة، وانقل منها ما يلزم صراحةً إلى تعليمات المهام، ولا تُعِد إنجاز ما لم يتغير، واجعل المهام تركز على ما طلب المستخدم تغييره.
- اكتب بلغة المستخدم."""

VERIFIER = """أنت مدقق صارم ومستقل. قيّم ناتج وكيل مقابل المهمة ومعيار القبول.
افحص: هل أنجز المطلوب كاملًا؟ هل فيه ادعاءات بلا دليل أو أرقام مختلقة أو تناقضات أو كود لن يعمل؟
أعد JSON فقط: {"pass": bool, "score": 0-10, "issues": ["مشكلة محددة"], "fix": "ما يجب تصحيحه بالضبط"}
pass=true فقط إذا score>=7 ولا توجد مشكلة جوهرية.
ملاحظة: إذا كانت البيانات المطلوبة غير متاحة فعلًا، فالناتج الذي يصرّح بذلك بوضوح ويقدّم تقديرًا معقولًا موسومًا بأنه تقدير (مع منهجه) يُعدّ مقبولًا. الذي يُرفض هو الاختلاق أو الإيهام بالدقة. ويُرفض أيضًا ترك المهمة بلا إنجاز بحجة نقص البيانات إن كان التقدير الموسوم ممكنًا.
تحقق من صحة المعادلات والحسابات بنفسك؛ الخطأ الحسابي مشكلة جوهرية.
الحقل الموسوم «غير متاح» لأن بياناته لا تُنشر علنًا ليس نقصًا جوهريًا ما دام الناتج قدّم ما يمكن إثباته. الرقم الدقيق بلا مصدر مفتوح أسوأ من «غير متاح»."""

SYNTH = """أنت المنسق. اجمع نتائج فريقك في جواب نهائي واحد متماسك للمستخدم بلغته وبتنسيق Markdown.
- لا تضف معلومات غير موجودة في النتائج.
- احتفظ بالروابط والمصادر.
- إن فشلت مهام أو لم تُتحقق، اذكر ذلك بصدق في قسم قصير "حدود هذا الجواب".
- إذا كان في النتائج تقدير موسوم، استخدمه لتقديم جواب عملي (أرقام وسيناريوهات) مع إبقاء وسم التقدير.
- إن أُرفق سياق من ذاكرة المنسق فاحترم وسم كل معلومة: انسب للمستخدم («مشروعك»، «قررتَ») فقط ما وُسم بأنه صرّح به أو أكده. ما سواه قدّمه كما هو: «في مهمة سابقة ورد أن...» أو «بافتراض أن...» أو «في السيناريو الاختباري...». لا تطلب من المستخدم بيانات حقيقية (كالسيولة) عن مشروع لم يصرّح أنه مشروعه. إن احتجت معلومة مصدرها غير مؤكد فاذكر ذلك أو اطلب التأكيد."""

DRAFT_RULE = """
- كل نتائج الفريق هنا رُفضت من المدقق. ابدأ الجواب بسطر واضح: «مسودة غير متحقق منها: لم تجتز أي مهمة التحقق»، ثم قسم «لماذا رُفضت» يلخص أسباب الرفض المذكورة.
- ثم قدّم المحتوى العملي الذي طلبه المستخدم كاملًا من نتائج الفريق (الجداول، والتقديرات، والخطة، والميزانية) ولا تختصره إلى أسباب الرفض فقط؛ المستخدم يحتاج مسودة يعمل عليها.
- لا تقدّم أي رقم كحقيقة؛ وسم كل رقم بأنه تقدير غير متحقق منه، واحذف ما وصفه المدقق بأنه مختلق.
- اختم بما يلزم للحصول على نتيجة موثوقة (مصادر بيانات، معلومات من المستخدم)."""


def _sanitize_plan(plan: dict, depth: int, can_exec: bool = True) -> list[dict]:
    raw = plan.get("tasks") or []
    if not isinstance(raw, list) or not raw:
        raise ValueError("الخطة بلا مهام")
    tasks, seen = [], set()
    for i, t in enumerate(raw[:MAX_TASKS_PER_PLAN]):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i + 1}").replace(".", "_")
        if tid in seen:
            tid = f"{tid}_{i}"
        role = t.get("role") if t.get("role") in ROLES else "assistant"
        if not can_exec and ROLES[role].tools == ("run_python",):
            role = "analyst"  # its only tool is disabled here; the analyst calculates without running code
        deps = [str(d).replace(".", "_") for d in (t.get("depends_on") or []) if str(d).replace(".", "_") in seen]
        tasks.append({"id": tid, "title": str(t.get("title") or tid)[:120], "role": role,
                      "instructions": str(t.get("instructions") or t.get("title") or ""),
                      "acceptance": str(t.get("acceptance") or "ينجز التعليمات كاملة بدقة"),
                      "depends_on": deps, "decompose": bool(t.get("decompose")) and depth < MAX_DEPTH - 1})
        seen.add(tid)
    if not tasks:
        raise ValueError("الخطة بلا مهام صالحة")
    return tasks


async def plan(llm: LLM, goal: str, depth: int, can_exec: bool = True, follow_up: bool = False) -> dict:
    note = "" if depth == 0 else " (غير مسموح في هذا المستوى)"
    sys = PLANNER.format(roles=role_list(can_exec), max_tasks=MAX_TASKS_PER_PLAN if depth == 0 else 5, decompose_note=note)
    user = (FOLLOW_UP_PLAN if follow_up and depth == 0 else "") + goal
    p = await llm.chat_json([{"role": "system", "content": sys}, {"role": "user", "content": user}], max_tokens=3000)
    return {"simple": bool(p.get("simple")) and depth == 0, "summary": str(p.get("summary", "")),
            "tasks": _sanitize_plan(p, depth, can_exec)}


GOAL_CHARS = 2000
FOLLOW_UP_AGENT_CHARS = 6000


def anchor(goal: str) -> str:
    """The user's own request, shown to every agent and verifier so a task can't drift off-topic."""
    g = goal.strip()
    return g[:GOAL_CHARS] + ("…" if len(g) > GOAL_CHARS else "")


async def verify(llm: LLM, task: dict, output: str, goal: str = "", read: list | None = None) -> dict:
    head = (f"طلب المستخدم الأصلي (ارفض الناتج إن خرج عن موضوعه أو منتجه أو أرقامه، أو أعاد رقمًا ثبت خطؤه في "
            f"التنبيه الحسابي دون تصحيحه):\n{goal[:GOAL_CHARS + FOLLOW_UP_AGENT_CHARS + 2000]}\n\n"
            "تنبيه: هذا ناتج مهمة فرعية واحدة من خطة فيها مهام أخرى. قيّمه مقابل «المهمة» و«معيار القبول» أدناه فقط، "
            "ولا تعدّ غياب أجزاء الطلب التي لا تطلبها هذه المهمة نقصًا.\n\n") if goal else ""
    if read is not None:  # what the agent really opened, so claimed-but-unread links and figures can be judged
        head += ("صفحات فتحها الوكيل فعلًا بأداة القراءة: " + ("، ".join(read[:15]) if read else "لا شيء") +
                 "\nأي رقم دقيق (حصة سوقية، حجم سوق، سعر) منسوب لرابط ليس في هذه القائمة ولا في سياق المهمة يُعدّ غير مثبت؛ "
                 "وإن قُدّم كحقيقة فهو اختلاق جوهري.\n\n")
    try:
        v = await llm.chat_json([{"role": "system", "content": VERIFIER}, {"role": "user", "content":
                                 head + f"المهمة: {task['instructions']}\nمعيار القبول: {task['acceptance']}\n\nالناتج:\n{output[:12000]}"}],
                                temperature=0.0, max_tokens=800)
    except (ValueError, LLMError) as e:
        return {"pass": False, "score": 0, "issues": [f"تعذر التحقق: {e}"], "fix": ""}
    score = float(v.get("score") or 0)
    return {"pass": bool(v.get("pass")) and score >= 7, "score": score,
            "issues": [str(x) for x in (v.get("issues") or [])][:6], "fix": str(v.get("fix") or "")}


NUMCHECK = """استخرج من نص المستخدم العلاقات الحسابية الصريحة التي يذكرها هو نفسه: تحويل عملة بسعر صرف مذكور في النص، مجموع أو فرق، نسبة من مبلغ، سعر في كمية.
- لا تخترع علاقة ولا سعر صرف غير مذكور. لا تقيّم صحة العلاقة؛ اكتب فقط كيف تُحسب القيمة التي ادعاها المستخدم من بقية أرقامه.
- expr تعبير بالأرقام فقط (+ - * / وأقواس) يعطي القيمة الصحيحة للمقدار الذي ذكر المستخدم قيمته في stated.
- انتبه للاتجاه: «X ريال أي Y دولار بسعر صرف R ريال للدولار» تعني expr = "X / R".
أعد JSON فقط: {"claims": [{"text": "العبارة كما وردت", "expr": "...", "stated": رقم, "unit": "وحدة المقدار"}]}
مثال: «ميزانية 500 ريال (أي 200 دولار بسعر 3.75)» -> {"claims": [{"text": "500 ريال أي 200 دولار بسعر 3.75", "expr": "500 / 3.75", "stated": 200, "unit": "دولار"}]}
إن لم توجد علاقة حسابية صريحة فأعد {"claims": []}."""


def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("\u066c", "").replace("\u202f", "").strip())
    except (TypeError, ValueError):
        return None


def _exact(expr: str) -> float | None:
    """Evaluate a plain arithmetic expression with the calculator's whitelist (no code runs)."""
    import ast
    from .tools import _calc_eval
    e = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", str(expr or "")).replace("×", "*").replace("÷", "/")
    if not e or len(e) > 200 or not re.fullmatch(r"[\d\s.+\-*/()]+", e):
        return None
    try:
        v = _calc_eval(ast.parse(e, mode="eval").body, {})
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def grade_claims(claims) -> list[dict]:
    """The model only transcribes the user's arithmetic; the result is computed exactly, never by the model."""
    out = []
    for c in (claims if isinstance(claims, list) else [])[:8]:
        if not isinstance(c, dict):
            continue
        stated, correct = _num(c.get("stated")), _exact(c.get("expr"))
        if stated is None or correct is None:
            continue
        ok = abs(correct - stated) <= max(abs(correct) * 0.01, 0.5)
        out.append({"text": str(c.get("text") or "")[:200], "expr": str(c.get("expr"))[:200], "stated": stated,
                    "correct": round(correct, 2), "unit": str(c.get("unit") or "")[:30], "ok": ok})
    return out


async def check_goal_numbers(llm: LLM, goal: str) -> list[dict]:
    if not re.search(r"[\d\u0660-\u0669]{2,}", goal):
        return []
    try:
        p = await llm.chat_json([{"role": "system", "content": NUMCHECK}, {"role": "user", "content": goal[:GOAL_CHARS]}],
                                temperature=0.0, max_tokens=600)
    except (ValueError, LLMError):
        return []
    return grade_claims(p.get("claims"))


def _fmt(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def checks_note(checks: list[dict] | None) -> str:
    bad = [c for c in (checks or []) if not c["ok"]]
    if not bad:
        return ""
    lines = [f"- ورد في الطلب «{c['text']}»: القيمة المذكورة {_fmt(c['stated'])} {c['unit']}، والصحيح {c['expr']} = "
             f"{_fmt(c['correct'])} {c['unit']}." for c in bad]
    return ("تنبيه حسابي (أعاد النظام حساب أرقام الطلب بالحاسبة الدقيقة): في الطلب خطأ حسابي.\n" + "\n".join(lines) +
            "\nاستخدم القيمة الصحيحة، ولا تكرر القيمة الخاطئة إلا لتذكر أنها صُححت، وصرّح بالتصحيح للمستخدم.")


_N = r"\d{1,3}(?:[ \u202f\u00a0,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_OP = r"(?:\s*[+−×*/÷]\s*|\s+-\s+)"  # a bare hyphen (5-12) is a range, not a minus
_EQ = re.compile(rf"(?<![\d.])((?:{_N})\s*%?(?:{_OP}\(?\s*(?:{_N})\s*%?\s*\)?)+)\s*=\s*\**\s*({_N})(\s*%)?")


_QUOTED_WRONG = re.compile(r"خطأ|خاطئ|غير صحيح|الصحيح|تصحيح|صُ?ح[ّ]?ح|بدلًا من|بدلاً من|بدل")


def _plain(n: str) -> str:
    return re.sub(r"[ \u202f\u00a0,]", "", n)


def audit_arithmetic(text: str, limit: int = 40) -> list[dict]:
    """Every 'a + b ... = c' written in an output, recomputed exactly. Models (and verifiers) miss these:
    on production a plan summed six items to 120,000 when they make 110,000, and the verifier gave it 8/10."""
    out = []
    for m in list(_EQ.finditer(text or ""))[:limit]:
        left, right, pct = m.group(1), m.group(2), m.group(3)
        expr = re.sub(_N, lambda x: _plain(x.group(0)), left).replace("−", "-")
        expr = expr.replace("%", "") if pct else re.sub(r"\s*%", "/100", expr)  # 10 % × 120 000 = 12 000
        got = _exact(expr)
        stated = _num(_plain(right))
        if got is None or stated is None:
            continue
        near = (text or "")[max(0, m.start() - 80):m.end() + 80]
        if _QUOTED_WRONG.search(near):  # the output is quoting a wrong figure in order to correct it
            continue
        if abs(got - stated) > max(abs(got) * 0.005, 0.01):
            out.append({"expr": left.strip()[:160], "stated": stated, "correct": round(got, 2)})
    return out


def apply_audit(v: dict, output: str) -> dict:
    """Arithmetic errors found by the exact audit fail the output whatever the model-verifier said."""
    errs = audit_arithmetic(output)
    if not errs:
        return v
    issues = [f"خطأ حسابي تحقق منه النظام بالحاسبة: {e['expr']} = {_fmt(e['stated'])} في الناتج، والصحيح {_fmt(e['correct'])}"
              for e in errs[:4]]
    return {**v, "pass": False, "score": min(float(v.get("score") or 0), 4.0), "issues": issues + list(v.get("issues") or []),
            "fix": "صحّح الحسابات المذكورة بأداة calculate وحدّث كل ما يعتمد عليها. " + str(v.get("fix") or ""),
            "arithmetic": errs}


def pick_best(attempts: list[dict]) -> int | None:
    """Index of the attempt to keep when none passed: highest verifier score, then most pages really
    read, then the earliest (retries under pressure tend to fill gaps with invented figures)."""
    scored = [(i, a) for i, a in enumerate(attempts) if a.get("output") and "verdict" in a]
    if not scored:
        return None
    return max(scored, key=lambda x: (float(x[1]["verdict"].get("score") or 0), len(x[1].get("read") or []), -x[0]))[0]


class Orchestrator:
    def __init__(self, run: Run, llm: LLM, persist=None, prior: dict | None = None):
        self.run, self.llm, self.persist = run, llm, persist  # persist(): async -> bool, saves the run
        self.prior = prior  # stored data of a stopped run to continue: its plan and verified outputs are reused

    async def execute(self):
        r = self.run
        try:
            await r.emit("run_started", {"goal": r.goal, "llm": r.llm_public, "max_agents": r.max_agents,
                                         "memory": bool(r.memory_space)})
            existing = await self.recall()
            if r.goal_checks is None:  # a resumed run keeps the checks it already made
                r.goal_checks = await check_goal_numbers(self.llm, r.goal)
                if r.goal_checks:
                    await r.emit("goal_checked", {"checks": r.goal_checks})
            goal = r.goal + (f"\n\n---\n{self.note}" if self.note else "") + (f"\n\n---\n{r.context}" if r.context else "")
            if r.memory["recalled"]:
                goal += "\n\n---\n" + memory.render_context(r.memory["recalled"])
            out = await self.orchestrate(goal, depth=0, prefix="")
            r.result = out["output"]
            r.verdict = out.get("verdict", {})
            status = "done" if out["ok"] else ("partial" if out.get("partial") else
                                               "cancelled" if out.get("rejected") else "failed")
            if r.memory_space and status in ("done", "partial") and not out.get("rejected"):
                await self.remember(existing)  # before status flips, so clients see saved memory with the result
            r.status = status
        except asyncio.CancelledError:
            if r.status == "interrupted":  # server shutdown, not the user
                await r.emit("run_interrupted", {})
            else:
                r.status = "cancelled"
                await r.emit("run_cancelled", {})
        except Exception as e:  # noqa: BLE001
            r.status, r.error = "failed", str(e)
            await r.emit("run_error", {"error": str(e)})
        finally:
            r.usage = self.llm.usage.as_dict()
            r.finished_at = time.time()
            # Save BEFORE announcing the end, so "finished" is never shown for a run that was not stored.
            if self.persist:
                r.saved = await self._persist_final()
            await r.emit("run_finished", {"status": r.status, "usage": r.usage, "agents_spawned": r.agents_spawned,
                                          "saved": r.saved})

    @property
    def note(self) -> str:
        return checks_note(self.run.goal_checks)

    def anchored(self) -> str:
        """The request as every agent and per-task verifier sees it: the user's words, any arithmetic correction,
        and, for a follow-up, what the previous turn produced (so agents build on it instead of redoing it)."""
        r = self.run
        prev = r.context[:FOLLOW_UP_AGENT_CHARS] + ("\n[...]" if len(r.context) > FOLLOW_UP_AGENT_CHARS else "") if r.context else ""
        return anchor(r.goal) + (f"\n\n{self.note}" if self.note else "") + (f"\n\n{prev}" if prev else "")

    async def _persist_final(self) -> bool:
        for attempt in range(3):
            try:
                if await self.persist(final=True):
                    return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5 * (attempt + 1))
        return False

    async def recall(self) -> list[dict]:
        r = self.run
        if not r.memory_space:
            return []
        r.memory["enabled"] = True
        try:
            existing = await memory.STORE.list(r.memory_space)
        except Exception as e:  # noqa: BLE001  memory must never break a run
            r.memory["error"] = f"recall: {str(e)[:200]}"
            await r.emit("memory_error", {"stage": "recall", "error": str(e)[:200]})
            return []
        r.memory["recalled"] = memory.recall(r.goal, existing, scenario=r.scenario)
        await r.emit("memory_recalled", {"count": len(r.memory["recalled"]), "total": len(existing),
                                         "items": [{"kind": i["kind"], "nature": i["nature"], "subject": i.get("subject"),
                                                    "content": i["content"][:160]} for i in r.memory["recalled"]]})
        return existing

    async def remember(self, existing: list[dict]):
        r = self.run
        try:
            res = await memory.extract_and_save(self.llm, r.memory_space, r.id, r.goal, r.result, existing,
                                                scenario=r.scenario)
        except Exception as e:  # noqa: BLE001  the run's answer is kept; the failure is recorded and shown
            r.memory["error"] = f"save: {str(e)[:200]}"
            await r.emit("memory_error", {"stage": "save", "error": str(e)[:200]})
            return
        if res.get("error"):
            r.memory["error"] = f"extract: {res['error'][:200]}"
            await r.emit("memory_error", {"stage": "extract", "error": res["error"][:200]})
            return
        r.memory["saved"], r.memory["superseded"] = res["saved"], res["superseded"]
        await r.emit("memory_saved", {"count": len(res["saved"]), "superseded": len(res["superseded"]),
                                      "scenario": res.get("scenario", False),
                                      "items": [{"kind": i["kind"], "nature": i["nature"], "subject": i.get("subject"),
                                                 "content": i["content"][:160]} for i in res["saved"]]})

    async def orchestrate(self, goal: str, depth: int, prefix: str) -> dict:
        r = self.run
        p = await self._reuse_plan() if depth == 0 and self.prior else None
        if p:
            return await self._continue(goal, p)
        await r.emit("planning", {"scope": prefix or "root", "depth": depth})
        p = await plan(self.llm, goal, depth, r.can_exec, follow_up=bool(r.context))
        tasks = p["tasks"]
        for t in tasks:
            t["id"] = prefix + t["id"]
            t["depends_on"] = [prefix + d for d in t["depends_on"]]
            t.update(status="pending", attempts=[], output="", depth=depth, parent=prefix.rstrip(".") or None)
            r.tasks[t["id"]] = t
        await r.emit("plan", {"scope": prefix or "root", "summary": p["summary"], "simple": p["simple"],
                              "tasks": [{k: t[k] for k in ("id", "title", "role", "depends_on", "decompose")} for t in tasks]})

        if depth == 0 and r.approve_plan and not (p["simple"] and len(tasks) == 1):
            tasks = await self._await_approval(tasks)
            if not tasks:
                return {"ok": False, "output": "رفضتَ الخطة، فلم يُنفَّذ شيء.", "rejected": True}

        if p["simple"] and len(tasks) == 1:
            await self.run_task(tasks[0], goal, verify_it=False)
            return {"ok": tasks[0]["status"] == "done", "output": tasks[0]["output"]}

        await self.run_dag(tasks)
        return await self.synthesize(goal, tasks, final=(depth == 0))

    async def _await_approval(self, tasks: list[dict]) -> list[dict]:
        """Wait for the owner's decision on the root plan. Approve runs it (minus dropped tasks, with the
        owner's note given to every agent); reject or no answer in time runs nothing."""
        r = self.run
        r.awaiting, r._decision = "plan", asyncio.Event()
        await r.emit("plan_awaiting_approval", {"timeout_s": APPROVAL_TIMEOUT})
        if self.persist:
            await self.persist()
        try:
            await asyncio.wait_for(r._decision.wait(), APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            r.plan_decision = {"action": "timeout", "at": time.time()}
        finally:
            r.awaiting = None
        d = r.plan_decision or {}
        await r.emit("plan_decision", {k: d.get(k) for k in ("action", "dropped", "note")})
        if d.get("action") != "approve":
            for t in tasks:
                t["status"] = "skipped"
            return []
        dropped = set(d.get("dropped") or [])
        keep = [t for t in tasks if t["id"] not in dropped]
        for t in tasks:
            if t["id"] in dropped:
                r.tasks.pop(t["id"], None)
        for t in keep:
            t["depends_on"] = [x for x in t["depends_on"] if x not in dropped]
            if d.get("note"):
                t["instructions"] += f"\n\nملاحظة المستخدم عند اعتماد الخطة (ملزمة): {d['note']}"
        return keep

    async def _reuse_plan(self) -> dict | None:
        """The root plan of the stopped run, with every task that passed verification kept as is.
        Anything else (running, failed, skipped, pending) is reset and runs again. Sub-teams of a task
        that did not finish are re-planned, since their partial state cannot be trusted."""
        prior_tasks = (self.prior or {}).get("tasks") or {}
        root = [t for t in prior_tasks.values() if not t.get("parent")]
        if not root:
            return None
        ids = {t["id"] for t in root}
        reused, tasks = [], []
        for old in root:
            t = {k: old.get(k) for k in ("id", "title", "role", "instructions", "acceptance", "decompose")}
            t["depends_on"] = [d for d in (old.get("depends_on") or []) if d in ids]
            if t["role"] not in ROLES or not t["instructions"]:
                return None  # unknown shape: plan from scratch rather than guess
            keep = old.get("status") == "done" and bool(old.get("output"))
            t.update(status="done" if keep else "pending", output=old.get("output", "") if keep else "",
                     attempts=list(old.get("attempts") or []) if keep else [], depth=0, parent=None,
                     reused=keep)
            tasks.append(t)
            if keep:
                reused.append(t["id"])
                if t["decompose"]:  # keep the finished sub-team visible
                    for sub in prior_tasks.values():
                        if str(sub.get("id", "")).startswith(t["id"] + "."):
                            self.run.tasks[sub["id"]] = sub
        for t in tasks:
            self.run.tasks[t["id"]] = t
        simple = any(e.get("type") == "plan" and e.get("scope") == "root" and e.get("simple")
                     for e in (self.prior.get("events") or []))
        self.run.resumed = {"from": self.prior.get("id"), "reused": reused}
        await self.run.emit("resumed", {"from": self.prior.get("id"), "reused": reused,
                                        "remaining": [t["id"] for t in tasks if t["status"] == "pending"]})
        return {"tasks": tasks, "simple": simple and len(tasks) == 1}

    async def _continue(self, goal: str, p: dict) -> dict:
        tasks = p["tasks"]
        if p["simple"]:
            t = tasks[0]
            if t["status"] == "pending":
                await self.run_task(t, goal, verify_it=False)
            return {"ok": t["status"] == "done", "output": t["output"]}
        await self.run_dag(tasks)
        return await self.synthesize(goal, tasks, final=True)

    async def run_dag(self, tasks: list[dict]):
        pending = {t["id"]: t for t in tasks if t["status"] == "pending"}  # reused tasks are already done
        running: dict[asyncio.Task, str] = {}
        try:
            await self._dag_loop(pending, running)
        finally:  # on cancel or error, stop sibling agents too: none may outlive the run (or its closed client)
            for t in running:
                t.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)

    async def _dag_loop(self, pending: dict, running: dict):
        while pending or running:
            for tid, t in list(pending.items()):
                deps = [self.run.tasks[d] for d in t["depends_on"]]
                if any(d["status"] == "skipped" or (d["status"] == "failed" and not d["output"]) for d in deps):
                    t["status"] = "skipped"
                    pending.pop(tid)
                    await self.run.emit("task_skipped", {"task": tid, "reason": "مهمة يعتمد عليها لم تُنتج شيئًا"})
                elif all(d["status"] in ("done", "failed") for d in deps):
                    pending.pop(tid)
                    ctx = "\n\n".join(
                        f"### نتيجة {d['id']} ({d['title']})"
                        + (" — تحذير: لم تجتز التحقق، استخدمها بحذر وصرّح بأي تقدير" if d["status"] == "failed" else "")
                        + f"\n{d['output']}" for d in deps)
                    running[asyncio.create_task(self.run_task(t, ctx))] = tid
            if not running:
                for t in pending.values():  # unreachable (should not happen after sanitize)
                    t["status"] = "skipped"
                break
            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                running.pop(d)
                if d.exception():
                    raise d.exception()

    async def run_task(self, t: dict, context: str, verify_it: bool = True):
        r = self.run
        t["status"] = "running"
        if t.get("decompose"):
            await r.emit("task_delegated", {"task": t["id"], "title": t["title"]})
            sub = await self.orchestrate(f"{t['instructions']}\n\nمعيار القبول: {t['acceptance']}\n\nسياق:\n{context}",
                                         depth=t["depth"] + 1, prefix=t["id"] + ".")
            t["output"] = sub["output"]
            t["status"] = "done" if sub["ok"] else "failed"
            await r.emit("task_finished", {"task": t["id"], "status": t["status"]})
            return
        role = ROLES[t["role"]]
        feedback = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if r.agents_spawned >= r.max_agents:
                t["status"] = "failed"
                await r.emit("task_finished", {"task": t["id"], "status": "failed", "reason": "نفدت ميزانية الوكلاء"})
                return
            r.agents_spawned += 1
            agent_id = f"{role.id}#{r.agents_spawned}"
            await r.emit("agent_spawned", {"task": t["id"], "agent": agent_id, "role": role.name, "attempt": attempt})

            async def emit(type_, data, _a=agent_id):
                await r.emit(type_, {"task": t["id"], "agent": _a, **data})

            instr = t["instructions"] + (f"\n\nمحاولة سابقة رُفضت من المدقق. صحّح التالي:\n{feedback}" if feedback else "")
            ctx = (f"طلب المستخدم الأصلي (للسياق؛ نفّذ مهمتك أنت فقط والتزم بموضوعه وأرقامه بعد أي تصحيح حسابي مذكور):\n{self.anchored()}"
                   + (f"\n\n{context}" if context else ""))
            try:
                res = await run_agent(self.llm, role, instr, ctx, emit, r.can_exec)
            except LLMError as e:
                t["attempts"].append({"agent": agent_id, "error": str(e)})
                await r.emit("agent_error", {"task": t["id"], "agent": agent_id, "error": str(e)})
                continue
            t["output"] = res["output"]
            read = res.get("read") if "fetch_url" in role.tools else None
            v = await verify(self.llm, t, res["output"], self.anchored(), read) if verify_it else {"pass": True, "score": None, "issues": []}
            if verify_it:
                v = apply_audit(v, res["output"])
            t["attempts"].append({"agent": agent_id, "steps": res["steps"], "confidence": res["confidence"],
                                  "sources": res["sources"], "read": res.get("read") or [], "verdict": v,
                                  "output": res["output"]})
            await r.emit("verified", {"task": t["id"], "agent": agent_id, **v})
            if v["pass"]:
                t["status"] = "done"
                await r.emit("task_finished", {"task": t["id"], "status": "done", "attempts": attempt})
                return
            feedback = "\n".join(f"- {i}" for i in v["issues"]) + (f"\nالمطلوب: {v['fix']}" if v.get("fix") else "")
        t["status"] = "failed"
        best = pick_best(t["attempts"])
        if best is not None:  # not simply the last one: a later attempt can be worse (e.g. invents figures)
            t["output"], t["best_attempt"] = t["attempts"][best]["output"], best + 1
        await r.emit("task_finished", {"task": t["id"], "status": "failed", "attempts": len(t["attempts"]),
                                       "kept_attempt": t.get("best_attempt")})

    async def synthesize(self, goal: str, tasks: list[dict], final: bool) -> dict:
        parts = []
        for t in tasks:
            state = {"done": "مُتحقق منها", "failed": "لم تجتز التحقق", "skipped": "لم تُنفذ"}.get(t["status"], t["status"])
            why = ""
            if t["status"] == "failed":
                k = t["attempts"][t["best_attempt"] - 1] if t.get("best_attempt") else (t["attempts"] or [{}])[-1]
                issues = (k.get("verdict") or {}).get("issues") or []
                why = ("\nأسباب رفض المدقق: " + "؛ ".join(issues[:3])) if issues else ""
            parts.append(f"## {t['id']} — {t['title']} [{state}]{why}\n{t['output'] or '(لا ناتج)'}")
        ok = all(t["status"] == "done" for t in tasks)
        if not any(t["status"] == "done" for t in tasks):
            if not final or not any(t["output"] for t in tasks):
                return {"ok": False, "output": "فشلت كل المهام ولم يُنتج الفريق نتيجة يمكن الوثوق بها.\n\n" + "\n\n".join(parts)}
            # Nothing passed, but rejected drafts exist: give the user one readable draft that says so up front,
            # instead of a dump of raw outputs. The run still ends "failed" and nothing is saved to memory.
            await self.run.emit("synthesizing", {"final": final, "unverified": True})
            try:
                answer = await self.llm.chat([{"role": "system", "content": SYNTH + DRAFT_RULE}, {"role": "user", "content":
                                              f"طلب المستخدم:\n{goal}\n\nنتائج الفريق (كلها رُفضت من المدقق):\n\n" + "\n\n".join(parts)}],
                                             temperature=0.2, max_tokens=4000)
            except LLMError:
                return {"ok": False, "output": "فشلت كل المهام ولم يُنتج الفريق نتيجة يمكن الوثوق بها.\n\n" + "\n\n".join(parts)}
            return {"ok": False, "output": answer, "verdict": {"pass": False, "score": None, "draft": True,
                                                               "issues": ["لم تجتز أي مهمة التحقق؛ هذا الجواب مسودة غير متحقق منها."]}}
        await self.run.emit("synthesizing", {"final": final})
        msgs = [{"role": "system", "content": SYNTH},
                {"role": "user", "content": f"طلب المستخدم:\n{goal}\n\nنتائج الفريق:\n\n" + "\n\n".join(parts)}]
        answer = await self.llm.chat(msgs, temperature=0.3, max_tokens=4000)
        if not final:
            return {"ok": ok, "partial": True, "output": answer}
        check = {"id": "final", "instructions": goal + (
                     "\n\nيجب أن يذكر الجواب التصحيح الحسابي صراحةً." if self.note else ""),
                 "acceptance": "يجيب عن طلب المستخدم كاملًا ومتسقًا مع نتائج الفريق بلا ادعاءات مختلقة"}
        v = apply_audit(await verify(self.llm, check, answer), answer)
        await self.run.emit("final_check", v)
        if not v["pass"] and v.get("issues"):
            msgs += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "المدقق رفض الجواب:\n" + "\n".join(v["issues"]) + f"\n{v.get('fix', '')}\nأعد كتابة الجواب النهائي مصححًا."}]
            answer = await self.llm.chat(msgs, temperature=0.2, max_tokens=4000)
            v = apply_audit(await verify(self.llm, check, answer), answer)
            await self.run.emit("final_check", v)
        return {"ok": ok and v["pass"], "partial": v["pass"] or any(t["status"] == "done" for t in tasks),
                "output": answer, "verdict": v}
