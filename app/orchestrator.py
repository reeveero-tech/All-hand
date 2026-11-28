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

from .research import evidence
from .research import manager as research_manager
from .research import quality_gate
from .research import relevance as research_relevance
from . import assumptions, export, finmodel, intake, memory, quality
from . import report as report_mod
from .agents import ROLES, run_agent
from .llm import FREE_PROMPT_CHARS, LLM, LLMError, ModelPaused, _clip, extract_objects, looks_truncated
from .llm import phase as llm_phase

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
    awaiting: str | None = None  # "plan" or "clarification" while waiting for the owner (status stays "running")
    clarify: bool = True  # ask the owner when the request leaves out what the team cannot work without (app/intake.py)
    brief: dict | None = None  # intake brief: what the request states, what it misses, and the owner's answers
    clarification: dict | None = None  # the owner's reply to the intake questions: {"answers", "assume", "at"}
    plan_decision: dict | None = None  # what the owner decided: {"action", "dropped", "note", "at"}
    scenario: bool = False  # user marked this run as a test/hypothetical: nothing it learns is about the user
    research: dict | None = None  # per-run measurements: searches, results, pages really read, sources in the answer
    research_state: dict | None = None  # the research manager's state: facets, candidates, readings, budget used (resumable)
    research_reuse: dict | None = None  # follow-up: the previous turn's research state, whose readings stay valid
    research_gate: dict | None = None  # the research quality gate's verdict on the final answer
    prior_check: dict | None = None  # follow-up: the previous answer's figures, checked and classified
    goal_checks: list | None = None  # arithmetic the user stated in the request, recomputed exactly (None: not checked)
    pause: dict | None = None  # why the run stopped and waits: {"kind", "phase", "step", "message", "resume_after", "at"}
    checkpoint: dict = field(default_factory=dict)  # where the run is: phase, next step, what is saved (see RUN_LIFECYCLE.md)
    reviews: dict = field(default_factory=dict)  # input fingerprint -> verdict: an unchanged input is not reviewed twice
    report: dict | None = None  # the final report's saved sections and how each was produced
    files: list = field(default_factory=list)  # delivered files: name, format, bytes, sha256, verified
    deliverable: str = ""  # the delivered file's content (stored, so it can be served after a restart)
    delta: dict | None = None  # assumption follow-up: {"from", "change", "old", "affected", "reused", "sections_changed"}
    _base: dict | None = field(default=None, repr=False)  # the previous run's data a delta follow-up builds on (not stored twice)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    status: str = "running"  # running | done | partial | failed | cancelled | interrupted (server shutdown)
    #                          | needs_input (the request missed essentials and the owner did not answer in time)
    #                          | paused_quota (model quota spent) | paused_provider (provider down, rate limit, key refused)
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
                "resumed": self.resumed, "approve_plan": self.approve_plan, "brief": self.brief, "plan_decision": self.plan_decision, "context": self.context, "scenario": self.scenario, "goal_checks": self.goal_checks,
                "research": self.research, "prior_check": self.prior_check, "research_state": self.research_state,
                "research_gate": self.research_gate, "pause": self.pause, "checkpoint": self.checkpoint,
                "reviews": self.reviews, "report": self.report, "files": self.files, "deliverable": self.deliverable,
                "delta": self.delta,
                "events": self.events}


ROLE_LIST = "\n".join(f"- {r.id}: {r.name} — {r.mission}" for r in ROLES.values())
NO_EXEC = " (تشغيل الكود معطّل على هذا الخادم: لا تختر هذا الدور؛ للحسابات اختر analyst الذي يملك حاسبة دقيقة)"


def role_list(can_exec: bool = True) -> str:
    return "\n".join(f"- {r.id}: {r.name} — {r.mission}" + ("" if can_exec or "run_python" not in r.tools or len(r.tools) > 1
                                                          else NO_EXEC) for r in ROLES.values())


FOLLOW_UP_PLAN = ("هذا طلب متابعة، ونتيجة المهمة السابقة مرفقة أدناه ويراها كل وكيل. لا تخطط لإعادة البحث أو التحليل الذي أُنجز؛ "
                  "خطط فقط لما يتطلبه التغيير الذي طلبه المستخدم الآن (غالبًا مهمة إلى ثلاث مهام)، ولا تضف بحثًا جديدًا إلا إن طلبه صراحةً "
                  "أو كانت النتيجة السابقة تخلو تمامًا مما يلزم.\n"
                  "إن غيّر المستخدم معطًى (كالميزانية) فاكتب في تعليمات كل مهمة القيمة الجديدة وأنها تحل محل القديمة، "
                  "واطلب إعادة حساب كل ما يتأثر بها. التزم بـ«سجل معلومات النتيجة السابقة»: ما وُسم مرفوضًا لا يُستخدم، "
                  "وتقديرات الطلب والإيرادات تُعاد مراجعة أساسها قبل البناء عليها؛ إن كان أساسها مرفوضًا فخطط لمهمة تعيد تقديرها بمصدر.\n\n")

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
- اختصر: التعليمات في 350 حرفًا كحد أقصى، ومعيار القبول في 150 حرفًا (الرد الطويل يُقطع).
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
افحص معقولية الأرقام لا صحة حسابها فقط: عائد استثمار بآلاف بالمئة، أو طلب سنوي يتجاوز ما يستهلكه سكان البلد، أو إيرادات بمليارات من ميزانية صغيرة، كلها مشكلة جوهرية حتى لو وُسمت «تقديرًا».
الحقل الموسوم «غير متاح» لأن بياناته لا تُنشر علنًا ليس نقصًا جوهريًا ما دام الناتج قدّم ما يمكن إثباته. الرقم الدقيق بلا مصدر مفتوح أسوأ من «غير متاح»."""

SUMMARY_RULE = """
- هذه المرة لا تكتب التقرير كله: أقسام الفريق ستُلحق بعد ما تكتبه كاملة كما هي (كل قسم بمصادره)، وأقسام الحدود والمصادر يبنيها النظام.
- اكتب فقط: سطر عنوان للتقرير (#)، ثم «## الملخص التنفيذي» (أهم النتائج بأرقامها كما وردت في الأقسام بوسومها وروابطها)، ثم «## التوصيات وخطوات التنفيذ».
- لا تضف رقمًا غير موجود في الأقسام، ولا تحسب رقمًا جديدًا. إن تعارض قسمان فاذكر التعارض.
- إن كان قسم «لم يجتز المراجعة» فلا تبنِ عليه توصية حاسمة واذكر ذلك.
- لا تتجاوز نحو 2000 حرف."""

FIX_RULE = """
- المدقق رفض جزءًا من التقرير. أعد كتابة هذا الجزء وحده مصححًا بالتنسيق نفسه، وأبقِ كل ما لم يُعترض عليه كما هو بوسومه وروابطه.
- إن كان رقم غير معقول مبنيًا على تقدير لا تملك بيانات لتصحيحه، فاحذفه وما بُني عليه وصرّح بأن البيانات لا تكفي بدل اختلاق بديل.
- أعد الجزء المصحح فقط، بلا مقدمة."""

SYNTH = """أنت المنسق. اجمع نتائج فريقك في جواب نهائي واحد متماسك للمستخدم بلغته وبتنسيق Markdown.
- لا تضف معلومات غير موجودة في النتائج.
- احتفظ بالروابط والمصادر.
- إن فشلت مهام أو لم تُتحقق، اذكر ذلك بصدق في قسم قصير "حدود هذا الجواب".
- إذا كان في النتائج تقدير موسوم، استخدمه لتقديم جواب عملي (أرقام وسيناريوهات) مع إبقاء وسم التقدير.
- إن أُرفق سياق من ذاكرة المنسق فاحترم وسم كل معلومة: انسب للمستخدم («مشروعك»، «قررتَ») فقط ما وُسم بأنه صرّح به أو أكده. ما سواه قدّمه كما هو: «في مهمة سابقة ورد أن...» أو «بافتراض أن...» أو «في السيناريو الاختباري...». لا تطلب من المستخدم بيانات حقيقية (كالسيولة) عن مشروع لم يصرّح أنه مشروعه. إن احتجت معلومة مصدرها غير مؤكد فاذكر ذلك أو اطلب التأكيد.
- أبقِ وسم كل رقم مهم كما ورد في النتائج: (المصدر: رابط) أو (من المستخدم) أو (محسوب) أو (تقدير) أو (افتراض). لا تحذف الروابط. لا تسِم رقمًا «من المستخدم» إلا إن ذكره المستخدم نفسه في طلبه؛ ما ورد في دراسة سابقة يوسم (من الدراسة السابقة) مع وسمه الأصلي.
- إن نقصت بيانات لازمة لقرار موثوق فأضف قسمًا قصيرًا «بيانات مطلوبة» يحدد ما يلزم ومن أين يمكن الحصول عليه.
- إن أُرفق «سجل معلومات النتيجة السابقة» فلا تستخدم ما وُسم فيه مرفوضًا، واذكر في «حدود هذا الجواب» ما استُبعد ولماذا."""

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


_ASSEMBLY = re.compile(r"تجميع|دمج\s+(?:النتائج|الأقسام|المخرجات)|الملف\s+النهائي|التقرير\s+النهائي|إعداد\s+الملف|"
                       r"\b(?:compile|assemble|consolidate|merge)\b|final\s+(?:report|file|deliverable|document)", re.I)


def drop_assembly(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Remove root tasks that only assemble the others into a final file. The planner is told not to add one,
    yet the free model still does (6a03d324e329: «t5 تجميع الملف النهائي», 2 attempts, both rejected, and no
    calculator in its tools). The Final Report Builder (app/report.py) assembles by code. A task that depends on a
    dropped one inherits its dependencies, so the DAG stays valid. Kept when it is the plan's only real work."""
    drop = [t for t in tasks if _ASSEMBLY.search(t.get("title") or "") and t.get("depends_on")]
    if not drop or len(tasks) - len(drop) < 2:
        return tasks, []
    ids = {t["id"] for t in drop}
    by_id = {t["id"]: t for t in tasks}

    def resolve(deps, seen=()):
        out = []
        for d in deps:
            if d in ids and d not in seen:
                out += resolve(by_id[d]["depends_on"], (*seen, d))
            elif d not in ids:
                out.append(d)
        return list(dict.fromkeys(out))
    keep = [t for t in tasks if t["id"] not in ids]
    for t in keep:
        t["depends_on"] = resolve(t["depends_on"])
    return keep, [t["id"] for t in drop]


async def plan(llm: LLM, goal: str, depth: int, can_exec: bool = True, follow_up: bool = False) -> dict:
    note = "" if depth == 0 else " (غير مسموح في هذا المستوى)"
    sys = PLANNER.format(roles=role_list(can_exec), max_tasks=MAX_TASKS_PER_PLAN if depth == 0 else 5, decompose_note=note)
    user = (FOLLOW_UP_PLAN if follow_up and depth == 0 else "") + goal
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
    last: Exception | None = None
    for attempt in range(2):  # production a085661bb506: one reply had no task list and the whole run failed
        try:
            p = _unwrap_plan(await llm.chat_json(msgs, temperature=0.2 if attempt == 0 else 0.0, max_tokens=3000))
            if not p.get("tasks"):  # a cut reply: keep every complete task object it did finish
                found = [o_ for o_ in extract_objects(getattr(llm, "last_text", "") or "") if o_.get("id") and o_.get("role")]
                uniq = list({str(t_["id"]): t_ for t_ in found}.values())
                if len(uniq) >= 2:
                    p = {"simple": False, "summary": p.get("summary", ""), "tasks": uniq}
            return {"simple": bool(p.get("simple")) and depth == 0, "summary": str(p.get("summary", "")),
                    "tasks": _sanitize_plan(p, depth, can_exec)}
        except ValueError as e:
            last = e
            msgs = msgs[:2] + [{"role": "user", "content": "تذكير: أعد كائن JSON واحدًا فيه المفتاح \"tasks\" قائمة مهام غير فارغة "
                                                           "بالشكل المطلوب في التعليمات، بلا أي نص آخر."}]
    raise last  # type: ignore[misc]


def _unwrap_plan(p: dict) -> dict:
    """Accept a task list the model nested under another key (e.g. {"plan": {"tasks": [...]}} or "steps")."""
    if isinstance(p.get("tasks"), list) and p["tasks"]:
        return p
    for k in ("steps", "subtasks", "المهام"):
        if isinstance(p.get(k), list) and p[k]:
            return {**p, "tasks": p[k]}
    for v in p.values():
        if isinstance(v, dict):
            inner = _unwrap_plan(v)
            if inner.get("tasks"):
                return {**p, **inner}
    return p


GOAL_CHARS = 2000
FOLLOW_UP_AGENT_CHARS = 6000


def anchor(goal: str) -> str:
    """The user's own request, shown to every agent and verifier so a task can't drift off-topic."""
    g = goal.strip()
    return g[:GOAL_CHARS] + ("…" if len(g) > GOAL_CHARS else "")


async def verify(llm: LLM, task: dict, output: str, goal: str = "", read: list | None = None,
                 evidence_text: str = "") -> dict:
    head = (f"طلب المستخدم الأصلي (ارفض الناتج إن خرج عن موضوعه أو منتجه أو أرقامه، أو أعاد رقمًا ثبت خطؤه في "
            f"التنبيه الحسابي دون تصحيحه):\n{_clip(goal, 4500)}\n\n"
            "تنبيه: هذا ناتج مهمة فرعية واحدة من خطة فيها مهام أخرى. قيّمه مقابل «المهمة» و«معيار القبول» أدناه فقط، "
            "ولا تعدّ غياب أجزاء الطلب التي لا تطلبها هذه المهمة نقصًا.\n\n") if goal else ""
    if read is not None:  # what the agent really opened, so claimed-but-unread links and figures can be judged
        head += ("صفحات فتحها الوكيل فعلًا بأداة القراءة: " + ("، ".join(read[:15]) if read else "لا شيء") +
                 "\nأي رقم دقيق (حصة سوقية، حجم سوق، سعر) منسوب لرابط ليس في هذه القائمة ولا في سياق المهمة يُعدّ غير مثبت؛ "
                 "وإن قُدّم كحقيقة فهو اختلاق جوهري.\n\n")
    if evidence_text:  # what the system itself read: the reviewer can check each figure against it
        head += ("أدلة قرأها النظام بنفسه وسُلّمت للوكيل ([E#] رقم الدليل ورابطه). الرقم الموجود هنا والمنسوب إلى دليله أو رابطه "
                 "رقم موثق؛ والاستشهاد بـ [E#] مع رابطه استشهاد صحيح. الرقم غير الموجود هنا ولا في الصفحات أعلاه غير مثبت. "
                 "سطر «موجود في نص المصدر» فحص آلي: الاسم أو الرقم ورد فعلًا في النص المستخرج من ذلك المصدر فلا تعدّه غير موثق، "
                 "وسطر «غير موجود في نص المصدر المستخرج» يعني أنه لم يرد فيه:\n"
                 + evidence_text + "\n\n")
    try:
        v = await llm.chat_json([{"role": "system", "content": VERIFIER}, {"role": "user", "content":
                                 head + f"المهمة: {task['instructions'][:3000]}\nمعيار القبول: {task['acceptance'][:800]}\n\nالناتج:\n"
                                 + _clip(output, max(3000, min(12000, FREE_PROMPT_CHARS - len(VERIFIER) - len(head)
                                                               - min(len(task['instructions']), 3000) - 1200)))}],
                                temperature=0.0, max_tokens=800)
    except ModelPaused:
        raise  # a review that could not run is neither a pass nor a rejection: the run pauses and reviews later
    except (ValueError, LLMError) as e:
        return {"pass": False, "score": 0, "issues": [f"تعذر التحقق: {e}"], "fix": "", "unreviewed": True}
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
    if len(re.findall(r"[\d\u0660-\u0669][\d\u0660-\u0669,.٬٫]*", goal)) < 2:
        return []  # one number cannot form a stated relation (total, conversion, share): no call needed
    try:
        p = await llm.chat_json([{"role": "system", "content": NUMCHECK}, {"role": "user", "content": goal[:GOAL_CHARS]}],
                                temperature=0.0, max_tokens=600)
    except ModelPaused:
        raise
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


_TOTAL = re.compile(r"إجمالي|الإجمالي|المجموع|مجموع|total", re.I)


def _cell_number(cell: str) -> float | None:
    found = re.findall(_N, cell.replace("**", ""))
    return _num(_plain(found[0])) if len(found) == 1 else None


def audit_tables(text: str) -> list[dict]:
    """A Markdown table row labelled 'total' must equal the sum of the rows above it, column by column
    (production: six budget lines making 110 000 under a total row of 120 000)."""
    out, block = [], []
    lines = (text or "").splitlines() + [""]
    for k, line in enumerate(lines):
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        rows = [r for r in block[1:] if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
        block = []
        if rows:
            out += _total_line_after(rows, lines[k:k + 4])
        start = 0  # a total covers the data rows since the previous total
        for i, row in enumerate(rows):
            if not row or not _TOTAL.search(row[0]):
                continue
            above, start = rows[start:i], i + 1
            if len(above) < 2:
                continue
            for j in range(1, len(row)):
                total = _cell_number(row[j])
                vals = [_cell_number(r[j]) if j < len(r) else None for r in above]
                if total is None or any(v is None for v in vals):
                    continue
                got = sum(vals)
                if abs(got - total) > max(abs(got) * 0.01, 0.1):
                    out.append({"expr": f"صف الإجمالي في جدول: {' + '.join(_fmt(v) for v in vals)}",
                                "stated": total, "correct": round(got, 2)})
    return out


def _total_line_after(rows: list[list[str]], after: list[str]) -> list[dict]:
    """'**المجموع**: 120 000 ريال (100 %)' written just under a table with no total row (production: the
    rows made 112 800 and 94 %). Each figure is checked against the column sum of similar size."""
    if any(_TOTAL.search(r[0]) for r in rows if r):
        return []
    line = next((x for x in after if x.strip()), "")
    if not _TOTAL.search(line):
        return []
    sums = []
    for j in range(1, max(len(r) for r in rows)):
        vals = [_cell_number(r[j]) if j < len(r) else None for r in rows]
        if len(vals) >= 2 and all(v is not None for v in vals):
            sums.append((vals, sum(vals)))
    out = []
    for n in [_num(_plain(x)) for x in re.findall(_N, line.replace("**", ""))]:
        if not n or any(abs(t - n) <= max(abs(t) * 0.01, 0.1) for _, t in sums):
            continue
        near = [(vals, t) for vals, t in sums if 0.7 <= t / n <= 1.3]
        if near:
            vals, t = min(near, key=lambda x: abs(x[1] - n))
            out.append({"expr": "مجموع الجدول: " + " + ".join(_fmt(v) for v in vals), "stated": n, "correct": round(t, 2)})
    return out


def apply_audit(v: dict, output: str) -> dict:
    """Arithmetic errors found by the exact audit fail the output whatever the model-verifier said."""
    errs, seen = [], set()
    for e in audit_arithmetic(output) + audit_tables(output):  # the same table repeated is one error
        if (e["expr"], e["stated"]) not in seen:
            seen.add((e["expr"], e["stated"]))
            errs.append(e)
    if not errs:
        return v
    issues = [f"خطأ حسابي تحقق منه النظام بالحاسبة: {e['expr']} = {_fmt(e['stated'])} في الناتج، والصحيح {_fmt(e['correct'])}"
              for e in errs[:4]]
    return {**v, "pass": False, "score": min(float(v.get("score") or 0), 4.0), "issues": issues + list(v.get("issues") or []),
            "fix": "صحّح الحسابات المذكورة بأداة calculate وحدّث كل ما يعتمد عليها. " + str(v.get("fix") or ""),
            "arithmetic": errs}


def unreviewed(a: dict) -> bool:
    """An attempt whose output no reviewer judged: the review call failed (quota, provider). Older runs recorded
    such a failure as a rejection with score 0 and «تعذر التحقق»; it is not one."""
    v = a.get("verdict") or {}
    if not v or a.get("error"):
        return False
    return bool(v.get("unreviewed")) or (not v.get("score") and any(str(i).startswith("تعذر التحقق") for i in v.get("issues") or []))


def feedback_of(v: dict) -> str:
    return "\n".join(f"- {i}" for i in v.get("issues") or []) + (f"\nالمطلوب: {v['fix']}" if v.get("fix") else "")


def improving(attempts: list[dict]) -> bool:
    """Is another attempt worth its calls? Not when the last reviewed attempt scored no better than an earlier one:
    the reviewer's bar stays the same, and the task ends failed (with its best attempt) instead of passing."""
    scores = [float((a.get("verdict") or {}).get("score") or 0) for a in attempts if a.get("verdict") and not unreviewed(a)]
    return len(scores) < 2 or scores[-1] > max(scores[:-1])


def pick_best(attempts: list[dict]) -> int | None:
    """Index of the attempt to keep when none passed: highest verifier score, then most pages really
    read, then the earliest (retries under pressure tend to fill gaps with invented figures)."""
    scored = [(i, a) for i, a in enumerate(attempts) if a.get("output") and "verdict" in a]
    if not scored:
        return None
    return max(scored, key=lambda x: (float(x[1]["verdict"].get("score") or 0), len(x[1].get("read") or []), -x[0]))[0]


_URL = re.compile(r"https?://[^\s)\]>»\"'|،,]+")
_TAGGED = re.compile(r"https?://|المصدر|مصدر:|من المستخدم|محسوب|تقدير|افتراض|غير متاح|=")


def research_stats(r: Run) -> dict:
    """Measured from what really happened in the run, not from what agents claim."""
    calls = [e for e in r.events if e.get("type") == "tool_call" and e.get("tool") == "web_search"]
    results = [e for e in r.events if e.get("type") == "tool_result" and e.get("tool") == "web_search"]
    providers: dict = {}
    for e in results:
        p = (e.get("provider") or "لا نتائج").split(" — ")[0]
        providers[p] = providers.get(p, 0) + 1
    read = []
    for e in r.events:
        if e.get("type") == "tool_result":
            for u in e.get("read") or []:
                if u not in read:
                    read.append(u)
        elif e.get("type") == "evidence" and e.get("stage") == "opened" and not e.get("failure") and e.get("url"):
            if e["url"] not in read:  # opened and extracted by the research manager itself
                read.append(e["url"])
    norm = lambda u: u.rstrip("/.").lower()  # noqa: E731
    in_answer = list(dict.fromkeys(u.rstrip(".") for u in _URL.findall(r.result or "")))
    read_n = {norm(u) for u in read}
    numeric = [ln for ln in (r.result or "").splitlines()
               if not ln.lstrip().startswith("#") and re.search(r"\d", re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", _URL.sub("", ln)))
               and not re.fullmatch(r"[\s|:\-]*", ln)]
    return {"search_calls": len(calls), "search_ok": sum(1 for e in results if e.get("results")),
            "results": sum(int(e.get("results") or 0) for e in results), "providers": providers,
            "pages_read": len(read), "read": read[:40],
            "fetch_calls": sum(1 for e in r.events if e.get("type") == "tool_call" and e.get("tool") == "fetch_url"),
            "sources_in_answer": sum(1 for u in in_answer if norm(u) in read_n),
            "links_not_read": [u for u in in_answer if norm(u) not in read_n][:20],
            "numeric_lines": len(numeric), "numeric_lines_untagged": sum(1 for ln in numeric if not _TAGGED.search(ln)),
            "research_tasks": sum(1 for t in r.tasks.values() if t.get("role") == "researcher"),
            "evidence": _evidence_summary(r),
            "relevance": research_relevance.summary(((r.research_state or {}).get("readings") or {}).values())}


def _evidence_summary(r: Run) -> dict:
    """Funnel from the evidence ledger: how many URLs reached each stage, why the others failed."""
    led = evidence.build(r.events, r.result or "")
    return {k: led[k] for k in ("counts", "failures", "duplicate_content", "independent_domains_read", "headline_only",
                                "by_facet", "summary_only", "cited_as_summary")}


PAUSE_MESSAGE = {
    "quota_exhausted": "نفدت حصة النموذج لهذا اليوم. العمل المنجز محفوظ كله (الخطة والأدلة ونتائج الوكلاء والمراجعات)، "
                       "والمهمة متوقفة مؤقتًا تنتظر توفر النموذج. استكملها بعد عودة الحصة فتبدأ من الخطوة التي توقفت عندها.",
    "rate_limited": "المزود يرفض الطلبات مؤقتًا لكثرتها. العمل المنجز محفوظ، والمهمة متوقفة مؤقتًا؛ استكملها بعد قليل.",
    "provider_unavailable": "مزود النموذج لا يستجيب الآن. العمل المنجز محفوظ، والمهمة متوقفة مؤقتًا؛ استكملها حين يعود.",
    "timeout": "انتهت مهلة مزود النموذج مرات متتالية. العمل المنجز محفوظ، والمهمة متوقفة مؤقتًا؛ استكملها لاحقًا.",
    "authentication_error": "المزود رفض مفتاح النموذج. العمل المنجز محفوظ؛ صحّح المفتاح ثم استكمل المهمة.",
}


class Orchestrator:
    def __init__(self, run: Run, llm: LLM, persist=None, prior: dict | None = None):
        self.run, self.llm, self.persist = run, llm, persist  # persist(): async -> bool, saves the run
        self.prior = prior  # stored data of a stopped run to continue: its plan and verified outputs are reused
        self.rm: research_manager.ResearchManager | None = None
        self.step = "start"  # the step being worked on, recorded if the model stops answering
        self.research_prior: dict | None = None  # a delta follow-up's research, reused as is (nothing reopened)
        if prior:  # reviews of unchanged inputs stay valid across a resume
            run.reviews = {**(prior.get("reviews") or {}), **run.reviews}

    async def mark(self, phase: str, next_step: str, save: bool = True, **extra):
        """Record where the run is and save it. Called after each phase or task that succeeds, so a stop at any
        point loses at most the step in progress."""
        r = self.run
        usage = getattr(self.llm, "usage", None)
        tasks = {tid: {"status": t.get("status"), "attempts": len(t.get("attempts") or []),
                       "settled": bool(t.get("settled")), "unreviewed": bool(t.get("unreviewed")),
                       "reused": bool(t.get("reused")), "fp": report_mod.fp(t.get("instructions"), t.get("output"))}
                 for tid, t in r.tasks.items()}
        rs = r.research_state or {}
        r.checkpoint = {
            "version": 1, "goal": r.goal, "phase": phase, "next_step": next_step, "status": r.status,
            "plan": [{k: t.get(k) for k in ("id", "title", "role", "depends_on")} for t in r.tasks.values() if not t.get("parent")],
            "research": {"done": bool(rs.get("done")), "facets": dict(rs.get("facets") or {}),  # facet -> critical|supporting
                         "facet_status": {k: (v.get("status") if isinstance(v, dict) else v) for k, v in (rs.get("status") or {}).items()},
                         "readings": len(rs.get("readings") or {}), "candidates": len(rs.get("candidates") or {}),
                         "used": rs.get("used")},
            "analysis": tasks, "reviews": len(r.reviews), "goal_checks": r.goal_checks,
            "report": {"sections": [{k: x.get(k) for k in ("id", "status", "fp", "by")} for x in (r.report or {}).get("sections") or []],
                       "assembled": bool((r.report or {}).get("assembled_fp"))},
            "files": [{k: f.get(k) for k in ("name", "bytes", "sha256", "verified")} for f in r.files],
            "calls": usage.as_dict() if hasattr(usage, "as_dict") else {}, "stop_reason": (r.pause or {}).get("kind"),
            "saved_at": time.time(), **extra}
        if save and self.persist:
            await self.persist()

    async def review(self, task: dict, output: str, goal: str = "", read: list | None = None, evidence_text: str = "") -> dict:
        """verify(), once per distinct input. The fingerprint covers everything the reviewer sees (task, output,
        request, pages read, evidence, documented gaps): change any of it and the part is reviewed again."""
        key = report_mod.fp("review", task.get("instructions"), task.get("acceptance"), output, goal, sorted(read or []) if read is not None else None,
                            evidence_text)
        hit = self.run.reviews.get(key)
        if hit and not hit.get("unreviewed"):
            if hasattr(getattr(self.llm, "usage", None), "avoid"):
                self.llm.usage.avoid(1, "review")
            await self.run.emit("review_reused", {"task": task.get("id"), "score": hit.get("score"), "pass": hit.get("pass")})
            return dict(hit)
        v = await verify(self.llm, task, output, goal, read, evidence_text)
        if v.get("unreviewed"):  # the reviewer's reply was unusable: that is not a rejection, ask once more
            v = await verify(self.llm, task, output, goal, read, evidence_text)
        if not v.get("unreviewed"):
            self.run.reviews[key] = dict(v)
        return v

    async def execute(self):
        r = self.run
        try:
            await r.emit("run_started", {"goal": r.goal, "llm": r.llm_public, "max_agents": r.max_agents,
                                         "memory": bool(r.memory_space)})
            if not r.context and not r.delta:  # a follow-up builds on a request that was already understood
                if r.brief is None and not self.prior:  # runs from before the intake step are not re-asked
                    r.brief = intake.brief(r.goal)
                    await r.emit("intake_brief", {k: r.brief[k] for k in ("kind", "stated", "missing", "blocking")})
                if r.brief and r.brief.get("blocking") and "resolved" not in r.brief and r.clarify:
                    if not await self._await_clarification():
                        r.status = "needs_input"
                        r.result = ("لم يبدأ العمل: الطلب ينقصه ما لا يمكن العمل دونه ("
                                    + "، ".join(intake.LABEL[k] for k in r.brief["blocking"]) +
                                    "). أضف هذه المعلومات إلى الطلب وابدأ من جديد، أو اختر المتابعة بافتراضات موسومة.")
                        return
            existing = await self.recall()
            if r.goal_checks is None:  # a resumed run keeps the checks it already made
                with llm_phase("intake"):
                    r.goal_checks = await check_goal_numbers(self.llm, r.goal)
                if r.goal_checks:
                    await r.emit("goal_checked", {"checks": r.goal_checks})
            if r.context:
                with llm_phase("intake"):
                    await self.check_prior()
            goal = r.goal + (f"\n\n---\n{self.note}" if self.note else "") + (f"\n\n---\n{self.prior_note}" if self.prior_note else "") \
                + (f"\n\n---\n{r.context}" if r.context else "")
            rejected = (r.prior_check or {}).get("rejected") or []
            totals = (r.prior_check or {}).get("rejected_totals") or []
            if (rejected or totals) and r.memory["recalled"]:  # a remembered figure proven wrong is not handed back as a finding
                keep = [i for i in r.memory["recalled"]
                        if quality.classify_line(i.get("content", ""), [], rejected, totals) != "rejected"]
                dropped = [i for i in r.memory["recalled"] if i not in keep]
                if dropped:
                    r.memory["recalled"], r.memory["excluded"] = keep, [{"content": i["content"][:160]} for i in dropped]
                    await r.emit("memory_excluded", {"count": len(dropped), "reason": "تحتوي أرقامًا ثبت خطؤها في النتيجة السابقة",
                                                     "items": r.memory["excluded"]})
            if r.memory["recalled"]:
                goal += "\n\n---\n" + memory.render_context(r.memory["recalled"])
            out = await self.orchestrate(goal, depth=0, prefix="")
            r.result = out["output"]
            r.verdict = out.get("verdict", {})
            status = "done" if out["ok"] else ("partial" if out.get("partial") else
                                               "cancelled" if out.get("rejected") else "failed")
            if self.rm and r.result:
                read = quality_gate.read_set(r.research_state, [u for e in r.events if e.get("type") == "tool_result"
                                                                for u in e.get("read") or []])
                r.result = self.rm.link_ids(r.result)
                r.result, unread = research_manager.annotate_unread(r.result, read)
                if unread:
                    await r.emit("unread_links_marked", {"count": len(unread), "urls": unread[:20]})
            r.research = research_stats(r)
            await r.emit("research_stats", r.research)
            if self.rm:
                led = evidence.build(r.events, r.result or "")
                r.research_gate = quality_gate.evaluate(r.research_state, led, r.result or "")
                await r.emit("research_gate", {k: r.research_gate[k] for k in ("verdict", "gaps", "checks")})
                if status == "done" and r.research_gate["verdict"] != "sufficient":
                    # research that ran without error is not research that sufficed
                    status = "partial"
                    r.verdict = {**r.verdict, "pass": False, "research_gate": r.research_gate["verdict"],
                                 "issues": ["أدلة البحث غير كافية لهذا الطلب: " + "؛ ".join(r.research_gate["gaps"][:6])]
                                 + list(r.verdict.get("issues") or [])}
            if status == "done" and r.research["research_tasks"] and not r.research["pages_read"]:
                # a study whose researchers opened nothing is not a verified study, whatever the verifiers said
                status = "partial"
                r.verdict = {**r.verdict, "pass": False, "issues": ["لم تُقرأ أي صفحة مصدر فعليًا في مهام البحث؛ الأرقام غير مثبتة."]
                             + list(r.verdict.get("issues") or [])}
            if r.result and not out.get("rejected"):
                status = await self.deliver(status)
            bad_figures = [i for i in r.verdict.get("plausibility") or [] if i.get("severity") == "error"]
            if r.memory_space and status in ("done", "partial") and not out.get("rejected"):
                if r.verdict.get("arithmetic") or bad_figures:  # a proven error must not become a remembered "finding"
                    r.memory["skipped"] = "أخطاء حسابية في الجواب النهائي" if r.verdict.get("arithmetic") else "أرقام غير معقولة في الجواب النهائي"
                    await r.emit("memory_skipped", {"reason": r.memory["skipped"]})
                else:
                    with llm_phase("memory"):
                        await self.remember(existing)  # before status flips, so clients see saved memory with the result
            r.status = status
        except ModelPaused as e:
            await self._paused(e)
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

    async def deliver(self, status: str) -> str:
        """Create the final file, store it with the run, and check it really exists before calling the run done.
        A run whose file could not be created or verified is not done (it stays resumable as partial)."""
        r = self.run
        self.step = "delivery"
        r.finished_at = time.time()
        name = f"agos-report-{r.id}.md"
        try:
            content = export.to_markdown({**r.full(), "status": status, "deliverable": "", "files": []})
            rec = report_mod.file_record(name, content)
            ok = report_mod.verify_file(rec, content, [r.result.splitlines()[0] if r.result else "", r.goal.splitlines()[0][:80]])
        except Exception as e:  # noqa: BLE001  a broken export must not lose the report itself
            ok, rec, content = False, {"name": name, "error": f"{type(e).__name__}: {str(e)[:200]}"}, ""
        rec["verified"] = ok
        r.files = [rec]
        r.deliverable = content if ok else ""
        await r.emit("file_created" if ok else "file_failed", {k: rec.get(k) for k in ("name", "bytes", "sha256", "verified", "error")})
        if not ok and status == "done":
            status = "partial"
            r.verdict = {**r.verdict, "issues": ["تعذر إنشاء الملف النهائي أو التحقق منه؛ التقرير محفوظ ويمكن إعادة بنائه."]
                         + list(r.verdict.get("issues") or [])}
        await self.mark("delivery", "done" if ok else "rebuild file", delivered=ok)
        return status

    async def _paused(self, e: ModelPaused):
        """The model stopped answering (quota spent, provider down, key refused). Nothing done so far is lost: the
        run keeps its plan, research, every agent's output and review, and waits to be resumed from this point."""
        r = self.run
        r.status = "paused_quota" if e.kind in ("quota_exhausted",) else "paused_provider"
        done = [t["id"] for t in r.tasks.values() if t.get("status") == "done"]
        usage = getattr(self.llm, "usage", None)
        r.pause = {"kind": e.kind, "http_status": e.status, "phase": (getattr(usage, "last_error", None) or {}).get("phase"),
                   "step": self.step, "message": PAUSE_MESSAGE.get(e.kind, PAUSE_MESSAGE["provider_unavailable"]),
                   "provider_message": str(e)[:300], "resume_after": e.resume_after, "at": time.time(),
                   "kept": {"tasks_done": done, "readings": len(((r.research_state or {}).get("readings") or {}))}}
        r.error = ""
        await self.mark(self.step.split(":")[0], f"resume at {self.step}", save=False)
        await r.emit("run_paused", {k: r.pause[k] for k in ("kind", "phase", "step", "resume_after", "message", "kept")})

    @property
    def note(self) -> str:
        return checks_note(self.run.goal_checks)

    @property
    def prior_note(self) -> str:
        pc = self.run.prior_check or {}
        return quality.render_ledger(pc.get("ledger") or [], pc.get("issues") or [])

    async def check_prior(self):
        """Follow-up: check the previous answer's figures and classify each line, once (a resumed run keeps it).
        What was proven wrong there is marked rejected, so it is not carried forward as a fact."""
        r = self.run
        pc = dict(r.prior_check or {})
        if "ledger" in pc:
            return
        answer = r.context.split("النتيجة السابقة:\n", 1)[-1]
        if isinstance(pc.get("prev_figures"), dict):  # the previous run's final check already examined this answer
            p = {"issues": pc["prev_figures"].get("issues") or [], "figures": pc["prev_figures"].get("figures") or {}}
            if hasattr(getattr(self.llm, "usage", None), "avoid"):
                self.llm.usage.avoid(1, "intake")
        else:
            p = await quality.check_plausibility(self.llm, answer)
        rejected = [float(x) for x in pc.get("known_rejected") or []]
        rejected += [x for i in p["issues"] if i["severity"] == "error" for x in i.get("values") or []]
        user_text = r.goal + "\n" + r.context.split("النتيجة السابقة:", 1)[0]
        answer_totals = [e["stated"] for e in audit_arithmetic(answer) + audit_tables(answer)]  # re-audited: older runs had no table check
        totals = [float(x) for x in pc.get("known_rejected_totals") or []] + answer_totals
        rejected, totals = list(dict.fromkeys(rejected)), list(dict.fromkeys(totals))
        pc.update(issues=p["issues"], figures=p["figures"], rejected=rejected, rejected_totals=totals,
                  ledger=quality.prior_ledger(answer, user_text, rejected, rejected_totals=totals))
        r.prior_check = pc
        counts: dict = {}
        for it in pc["ledger"]:
            counts[it["tag"]] = counts.get(it["tag"], 0) + 1
        await r.emit("prior_checked", {"counts": counts, "issues": [i["reason"] for i in p["issues"]][:6],
                                       "rejected": rejected[:10], "rejected_totals": totals[:10]})

    def reused_rejected(self, text: str) -> list[str]:
        pc = self.run.prior_check or {}
        return quality.reuses_rejected(text, pc.get("rejected") or [], pc.get("rejected_totals") or [])

    def mark_rejected(self, text: str) -> str:
        """The previous answer as agents see it, with every line that carries a rejected figure labeled in place,
        so the raw number is not copied forward as if it still stood."""
        bad = set(self.reused_rejected(text))
        if not bad:
            return text
        return "\n".join(f"[مرفوض من الدراسة السابقة — لا تستخدم أرقام هذا السطر] {ln}" if ln.strip()[:200] in bad else ln
                         for ln in text.splitlines())

    def anchored(self) -> str:
        """The request as every agent and per-task verifier sees it: the user's words, any arithmetic correction,
        and, for a follow-up, what the previous turn produced (so agents build on it instead of redoing it)."""
        r = self.run
        prev = self.mark_rejected(r.context[:FOLLOW_UP_AGENT_CHARS]) + ("\n[...]" if len(r.context) > FOLLOW_UP_AGENT_CHARS else "") if r.context else ""
        return anchor(r.goal) + (f"\n\n{self.note}" if self.note else "") + (f"\n\n{self.prior_note}" if self.prior_note else "") \
            + (f"\n\n{prev}" if prev else "")

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
        if depth == 0 and r.delta and r._base:
            p = await self._delta_plan()
            if p:
                return await self._continue(goal, p)
        await r.emit("planning", {"scope": prefix or "root", "depth": depth})
        self.step = f"plan:{prefix or 'root'}"
        with llm_phase("planning"):
            p = await plan(self.llm, goal, depth, r.can_exec, follow_up=bool(r.context))
        tasks = p["tasks"]
        if depth == 0:
            tasks, dropped = drop_assembly(tasks)
            if dropped:
                await r.emit("plan_adjusted", {"dropped": dropped, "reason": "مهمة تجميع: النظام يبني التقرير النهائي "
                                                                          "من أقسام المهام بنفسه دون وكيل"})
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

        if depth == 0:
            await self.mark("research", "research")
            await self.research(tasks)
            await self.mark("analysis", "analysis")
        await self.run_dag(tasks)
        return await self.synthesize(goal, tasks, final=(depth == 0))

    async def research(self, tasks: list[dict]):
        """Run the research manager before any analysis agent: it finds, opens and reads the sources itself.
        Agents then receive read evidence, and their own search and reading tools go through it."""
        r = self.run
        if not research_manager.ENABLED or self.rm is not None:
            return
        pending = [t for t in tasks if t["status"] == "pending"]
        researchish = any(ROLES[t["role"]].tools and set(ROLES[t["role"]].tools) & {"web_search", "fetch_url", "research_sources"}
                          for t in pending)
        if not researchish and not r.research_reuse and not self.research_prior:
            return
        prior_state = self.research_prior or ((self.prior or {}).get("research_state") if self.prior else None)
        budget = research_manager.Budget()
        if r.research_reuse and not prior_state:
            budget.max_rounds = 1  # a follow-up keeps what was read and only looks for what the change needs
        self.rm = research_manager.ResearchManager(
            r.goal, emit=r.emit, llm=self.llm, budget=budget, state=prior_state, reuse=r.research_reuse,
            persist=(lambda: self.persist()) if self.persist else None)
        r.research_state = self.rm.state
        await r.emit("research_started", {"resumed": bool(prior_state), "reused": bool(r.research_reuse),
                                          "budget": self.rm.state["budget"]})
        self.step = "research"
        try:
            if not (prior_state and prior_state.get("done")):
                with llm_phase("research"):
                    await self.rm.run()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001  research failing must not stop the run; the gate will say what is missing
            await r.emit("research_error", {"error": f"{type(e).__name__}: {str(e)[:200]}"})
        r.research_state = self.rm.to_dict()

    async def _await_clarification(self) -> bool:
        """Ask the owner what the request leaves out, before any model call. True when the team can start:
        the owner answered every blocking question, or chose to continue with labelled assumptions."""
        r = self.run
        r.awaiting, r._decision = "clarification", asyncio.Event()
        await r.emit("clarification_needed", {"questions": r.brief["missing"], "blocking": r.brief["blocking"],
                                              "timeout_s": APPROVAL_TIMEOUT})
        if self.persist:
            await self.persist()
        try:
            await asyncio.wait_for(r._decision.wait(), APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            r.clarification = {"action": "timeout", "at": time.time()}
        finally:
            r.awaiting, r._decision = None, None
        c = r.clarification or {}
        if c.get("action") == "timeout":
            await r.emit("clarification_timeout", {})
            return False
        r.goal, record = intake.apply_answers(r.goal, r.brief, c.get("answers") or {}, bool(c.get("assume")))
        r.brief = {**r.brief, "resolved": record}
        await r.emit("clarification_answered", record)
        return not record["unanswered"]

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
            attempts = list(old.get("attempts") or [])
            genuine = [a for a in attempts if not unreviewed(a)]
            keep = old.get("status") == "done" and bool(old.get("output"))
            # A task that used all its attempts on real rejections is settled: rerunning it would spend the same
            # calls for the same answer. Its best attempt is kept and shown as not verified.
            settled = (not keep and old.get("status") == "failed" and bool(old.get("output")) and not old.get("decompose")
                       and len(genuine) >= MAX_ATTEMPTS)
            t.update(status="done" if keep else "failed" if settled else "pending",
                     output=old.get("output", "") if keep or settled else "",
                     attempts=attempts if keep or settled else genuine, depth=0, parent=None, reused=keep or settled)
            if settled:
                t.update(settled=True, best_attempt=old.get("best_attempt"))
            elif not keep:
                # an output the model produced but no reviewer judged (quota ran out, or old runs where a failed
                # review counted as a rejection): it is reviewed on resume instead of being written again
                pend = old.get("unreviewed") or next(({"agent": a.get("agent"), "output": a.get("output"), "steps": a.get("steps") or [],
                                                       "confidence": a.get("confidence") or 0.4, "sources": a.get("sources") or [],
                                                       "read": a.get("read") or []}
                                                      for a in reversed(attempts) if unreviewed(a) and a.get("output")), None)
                if pend and pend.get("output") and not old.get("decompose"):
                    t["unreviewed"] = pend
            tasks.append(t)
            if keep or settled:
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
        if self.prior.get("report"):
            self.run.report = {"sections": list((self.prior["report"] or {}).get("sections") or []), "from": self.prior.get("id")}
        await self.run.emit("resumed", {"from": self.prior.get("id"), "reused": reused,
                                        "remaining": [t["id"] for t in tasks if t["status"] == "pending"],
                                        "review_only": [t["id"] for t in tasks if t.get("unreviewed")],
                                        "research_reused": bool(((self.prior or {}).get("research_state") or {}).get("done"))})
        return {"tasks": tasks, "simple": simple and len(tasks) == 1}

    async def _delta_plan(self) -> dict | None:
        """Budget follow-up: the previous plan, its research and every result that does not depend on the budget are
        kept as they are; only the dependent tasks (and what depends on them) are recomputed. No planner call."""
        r, base, d = self.run, self.run._base, self.run.delta
        root = [t for t in (base.get("tasks") or {}).values() if not t.get("parent")]
        if not root or any(t.get("role") not in ROLES or not t.get("instructions") for t in root):
            return None
        old = d.get("old") or {}
        aff = assumptions.affected(root, old.get("value"))
        ids = {t["id"] for t in root}
        tasks, reused = [], []
        for o in root:
            t = {k: o.get(k) for k in ("id", "title", "role", "instructions", "acceptance", "decompose")}
            t["depends_on"] = [x for x in (o.get("depends_on") or []) if x in ids]
            keep = o["id"] not in aff["all"] and o.get("status") in ("done", "failed") and bool(o.get("output"))
            if keep:
                t.update(status=o["status"], output=o["output"], attempts=list(o.get("attempts") or []), reused=True,
                         best_attempt=o.get("best_attempt"), settled=o.get("status") == "failed")
                reused.append(t["id"])
                if t["decompose"]:
                    for sub in (base.get("tasks") or {}).values():
                        if str(sub.get("id", "")).startswith(t["id"] + "."):
                            r.tasks[sub["id"]] = sub
            else:
                t["instructions"] = (o.get("instructions") or "") + assumptions.instruction(d["change"], d.get("old"), o.get("output") or "")
                t.update(status="pending", output="", attempts=[], budget_dependent=o["id"] in aff["all"])
            t.update(depth=0, parent=None)
            tasks.append(t)
            r.tasks[t["id"]] = t
        d.update(affected=aff, reused=reused, recomputed=[t["id"] for t in tasks if t["status"] == "pending"],
                 base_sections={x.get("id"): x.get("fp") for x in ((base.get("report") or {}).get("sections") or [])})
        if base.get("research_state"):  # what was read stays read; a budget change needs no new sources
            self.research_prior = {**base["research_state"], "done": True}
            r.research_state = self.research_prior  # the evidence travels with the new version
        await r.emit("budget_delta", {"from": d.get("from"), "old": old, "new": d["change"], "affected": aff["all"],
                                      "directly": aff["direct"], "reused": reused})
        return {"tasks": tasks, "simple": False}

    async def _continue(self, goal: str, p: dict) -> dict:
        tasks = p["tasks"]
        if p["simple"]:
            t = tasks[0]
            if t["status"] == "pending":
                await self.run_task(t, goal, verify_it=False)
            return {"ok": t["status"] == "done", "output": t["output"]}
        await self.research(tasks)
        await self.mark("analysis", "analysis")
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
        last = next((a.get("verdict") for a in reversed(t["attempts"]) if a.get("verdict")), None)
        feedback = feedback_of(last) if last and not last.get("pass") else ""
        for attempt in range(len(t["attempts"]) + 1, MAX_ATTEMPTS + 1):
            pend = t.pop("unreviewed", None)
            if pend:  # resumed: this output was written before the stop and only needs its review
                agent_id, calls, res = pend.get("agent") or f"{role.id}#resumed", [], {
                    "output": pend["output"], "steps": pend.get("steps") or [], "confidence": pend.get("confidence") or 0.4,
                    "sources": pend.get("sources") or [], "read": pend.get("read") or []}
                if hasattr(getattr(self.llm, "usage", None), "avoid"):
                    self.llm.usage.avoid(1, "analysis")
                await r.emit("review_resumed", {"task": t["id"], "agent": agent_id, "attempt": attempt})
                t["output"] = res["output"]
            else:
                res = await self._attempt(t, role, attempt, context, feedback)
                if res is None:
                    if r.agents_spawned >= r.max_agents and t["status"] == "failed":
                        return
                    continue
                agent_id, calls = res.pop("_agent"), res.pop("_calls")
                if finmodel.is_financial(t):
                    self._fin_apply(t, res)
                    await r.emit("financial_model", {"task": t["id"], "ok": not t["fin"]["errors"], "missing": t["fin"]["missing"],
                                                     "errors": t["fin"]["errors"][:3], "problems": t["fin"]["problems"][:5],
                                                     "raw": t["fin"].get("raw")})
            read = res.get("read") if {"fetch_url", "web_search", "world_bank"} & set(role.tools) else None
            if read is not None and self.rm is not None:  # evidence the system read and handed to this agent
                read = list(dict.fromkeys(list(read) + [x["url"] for x in self.rm.evidence_for(t["title"] + " " + t["instructions"])]))
            ev_text = (self.rm.verifier_evidence(t["output"], t["title"] + " " + t["instructions"],
                                                 2500 if self._pack_chars() <= 5000 else 5000)
                       if self.rm is not None else "")
            if self.rm is not None and self.rm.gaps():
                ev_text += ("\n\nنواقص بحث وثّقها النظام نفسه (غياب هذه البيانات ليس تقصيرًا من الوكيل: اقبل «غير متاح في الأدلة "
                            "المقروءة» لها ولا ترفض الناتج لغيابها، وارفض أي رقم مخمَّن وُضع مكانها):\n"
                            + "\n".join(f"- {g}" for g in self.rm.gaps()[:8]))
            fin = t.get("fin") if finmodel.is_financial(t) else None
            if fin and not fin["errors"]:
                ev_text += "\n\n" + finmodel.REVIEW_NOTE
            self.step = f"review:{t['id']}:{attempt}"
            try:
                with llm_phase("review"):
                    v = await self.review(t, t["output"], self.anchored(), read, ev_text) if verify_it else {"pass": True, "score": None, "issues": []}
            except ModelPaused:
                t["status"] = "pending"
                t["unreviewed"] = {"agent": agent_id, "output": t["output"], "steps": res["steps"], "confidence": res["confidence"],
                                   "sources": res["sources"], "read": res.get("read") or [], "attempt": attempt}
                raise
            if verify_it:
                v = apply_audit(v, t["output"])
                v = quality.apply_rejected(v, self.reused_rejected(t["output"] + "\n" + "\n".join(calls)))
                v = self.apply_stale(v, t["output"])
                if fin:
                    v = finmodel.verdict(v, fin)
            t["attempts"].append({"agent": agent_id, "steps": res["steps"], "confidence": res["confidence"],
                                  "sources": res["sources"], "read": res.get("read") or [], "verdict": v,
                                  "output": t["output"]})
            await r.emit("verified", {"task": t["id"], "agent": agent_id, **v})
            if v["pass"]:
                t["status"] = "done"
                await r.emit("task_finished", {"task": t["id"], "status": "done", "attempts": attempt})
                await self.mark("analysis", f"tasks pending: {self._pending_ids()}")
                return
            feedback = feedback_of(v)
            if not improving(t["attempts"]):
                await r.emit("retries_stopped", {"task": t["id"], "attempts": attempt,
                                                 "reason": "لم يتحسن تقييم المدقق في المحاولة الأخيرة؛ إعادة المحاولة لن تغير النتيجة غالبًا"})
                break
        t["status"] = "failed"
        t["settled"] = True
        best = pick_best(t["attempts"])
        if best is not None:  # not simply the last one: a later attempt can be worse (e.g. invents figures)
            t["output"], t["best_attempt"] = t["attempts"][best]["output"], best + 1
        await r.emit("task_finished", {"task": t["id"], "status": "failed", "attempts": len(t["attempts"]),
                                       "kept_attempt": t.get("best_attempt")})
        await self.mark("analysis", f"tasks pending: {self._pending_ids()}")

    def _fin_budget(self) -> float | None:
        ch = assumptions.budget_change(self.run.goal)
        if ch:
            return ch["new"]
        found = assumptions.amounts(self.run.goal)
        return found[0]["value"] if found else None

    def _fin_apply(self, t: dict, res: dict) -> dict:
        """Financial task: the code computes the model from the agent's tagged inputs (see app/finmodel.py)."""
        chk = finmodel.Checker(self.rm.evidence_text if self.rm is not None else None, self.run.goal)
        info = finmodel.apply(res, chk, self._fin_budget())
        t["output"] = info["output"]
        t["fin"] = {"errors": info["errors"], "missing": bool(info.get("missing")), "computed": info["computed"],
                    "problems": (info["model"] or {}).get("problems") or [], "raw": info.get("raw")}
        return info

    def _pending_ids(self) -> list[str]:
        return [tid for tid, t in self.run.tasks.items() if t.get("status") in ("pending", "running")]

    async def _attempt(self, t: dict, role, attempt: int, context: str, feedback: str) -> dict | None:
        """One agent writes the task. None: no output this attempt (agent budget spent, or the model call failed)."""
        r = self.run
        if r.agents_spawned >= r.max_agents:
            t["status"] = "failed"
            await r.emit("task_finished", {"task": t["id"], "status": "failed", "reason": "نفدت ميزانية الوكلاء"})
            return None
        r.agents_spawned += 1
        agent_id = f"{role.id}#{r.agents_spawned}"
        await r.emit("agent_spawned", {"task": t["id"], "agent": agent_id, "role": role.name, "attempt": attempt})
        calls: list[str] = []

        async def emit(type_, data, _a=agent_id, _c=calls):
            if type_ == "tool_call" and data.get("tool") == "calculate":
                _c.append(str((data.get("input") or {}).get("program", "")))
            await r.emit(type_, {"task": t["id"], "agent": _a, **data})

        instr = t["instructions"] + (finmodel.INSTRUCTIONS if finmodel.is_financial(t) else "") \
            + (f"\n\nمحاولة سابقة رُفضت من المدقق. صحّح التالي:\n{feedback}" if feedback else "")
        ctx = (f"طلب المستخدم الأصلي (للسياق؛ نفّذ مهمتك أنت فقط والتزم بموضوعه وأرقامه بعد أي تصحيح حسابي مذكور):\n{self.anchored()}"
               + (f"\n\n{context}" if context else ""))
        hook = None
        if self.rm is not None:
            if set(role.tools) & {"web_search", "fetch_url", "research_sources", "world_bank"}:
                ctx += "\n\n" + self.rm.pack(t["title"] + " " + t["instructions"], self._pack_chars())
            else:
                idx = self.rm.index()
                if idx:
                    ctx += "\n\nفهرس الأدلة التي قرأها النظام (للاستشهاد بالروابط الصحيحة فقط):\n" + idx

            async def hook(name, args, allowed, _t=t["id"]):
                return await self.rm.agent_tool(name, args, allowed, _t)
        self.step = f"agent:{t['id']}:{attempt}"
        try:
            with llm_phase("analysis"):
                res = await run_agent(self.llm, role, instr, ctx, emit, r.can_exec, tool=hook)
        except ModelPaused:
            t["status"] = "pending"  # not failed: it resumes at this attempt
            raise
        except LLMError as e:
            t["attempts"].append({"agent": agent_id, "error": str(e)})
            await r.emit("agent_error", {"task": t["id"], "agent": agent_id, "error": str(e)})
            return None
        t["output"] = self.rm.link_ids(res["output"]) if self.rm is not None else res["output"]
        return {**res, "_agent": agent_id, "_calls": calls}

    def _pack_chars(self) -> int:
        free = getattr(getattr(self.llm, "cfg", None), "provider", "") == "free"
        return 5000 if free else 10000

    async def _long(self, msgs: list[dict], temperature: float) -> str:
        with llm_phase("finalization"):
            return await self._long_raw(msgs, temperature)

    async def _long_raw(self, msgs: list[dict], temperature: float) -> str:
        if hasattr(self.llm, "chat_long"):
            return await self.llm.chat_long(msgs, temperature=temperature, max_tokens=4000, parts=5)
        return await self.llm.chat(msgs, temperature=temperature, max_tokens=4000)

    async def final_verdict(self, check: dict, answer: str) -> dict:
        self.step = "final_review"
        key = report_mod.fp("final", check, answer, self.reused_rejected(answer))
        hit = self.run.reviews.get(key)
        if hit:  # the same assembled report was already audited (resume after a stop during delivery)
            if hasattr(getattr(self.llm, "usage", None), "avoid"):
                self.llm.usage.avoid(2, "final_review")
            await self.run.emit("review_reused", {"task": "final", "score": hit.get("score"), "pass": hit.get("pass")})
            return dict(hit)
        with llm_phase("final_review"):
            v = await self._final_verdict(check, answer)
        if not v.get("unreviewed"):
            self.run.reviews[key] = dict(v)
        return v

    def apply_stale(self, v: dict, text: str) -> dict:
        """A budget follow-up must not keep results on the old budget next to the new one."""
        d = self.run.delta or {}
        if not d.get("change"):
            return v
        bad = assumptions.stale_lines(text, (d.get("old") or {}).get("value"), d["change"].get("new"))
        if not bad:
            return v
        return {**v, "pass": False, "score": min(float(v.get("score") or 0), 5.0), "stale": bad[:5],
                "issues": [f"ما زال يستخدم الميزانية السابقة {assumptions.fmt(d['old']['value'])} كقيمة حالية: «{bad[0]}»؛ "
                           "أعد حسابه بالميزانية الجديدة أو اذكر أنها السابقة."] + list(v.get("issues") or [])}

    async def _final_verdict(self, check: dict, answer: str) -> dict:
        v = self.apply_stale(apply_audit(await verify(self.llm, check, answer), answer), answer)
        r = self.run
        mis = quality.false_user_tags(answer, r.goal + "\n" + (r.context or "").split("النتيجة السابقة:", 1)[0])
        if mis:  # production 5e071b86edaa: budget shares the user never gave were labeled «من المستخدم»
            v = {**v, "pass": False, "score": min(v.get("score") or 0, 5),
                 "issues": list(v.get("issues") or []) + ["وُسمت أرقام بأنها «من المستخدم» والمستخدم لم يذكرها: " + " ؛ ".join(mis[:3])
                                                         + " — صحّح الوسم (محسوب/افتراض/من الدراسة السابقة)."]}
        if looks_truncated(answer):  # production 5e071b86edaa: an answer cut mid-table passed with 10/10
            v = {**v, "pass": False, "score": min(v.get("score") or 0, 4),
                 "issues": list(v.get("issues") or []) + ["الجواب مقطوع قبل اكتماله (ينتهي وسط جدول أو جملة)."]}
        v = quality.apply_rejected(v, self.reused_rejected(answer))
        p = await quality.check_plausibility(self.llm, answer)
        return {**quality.apply_plausibility(v, p), "figures_checked": {"issues": p.get("issues") or [], "figures": p.get("figures") or {}}}

    async def _save_section(self, sec: dict):
        r = self.run
        secs = [x for x in (r.report or {}).get("sections") or [] if x["id"] != sec["id"]] + [sec]
        r.report = {**(r.report or {}), "sections": secs}
        await self.mark("finalization", f"section saved: {sec['id']}")

    def _saved_section(self, sid: str, key: str) -> dict | None:
        for x in (self.run.report or {}).get("sections") or []:
            if x.get("id") == sid and x.get("fp") == key and x.get("text"):
                return x
        return None

    async def write_summary(self, goal: str, body: list[dict], issues: list[str] | None = None, prev: str = "") -> dict:
        """The one part of the report the model writes: summary and recommendations, from digests of the saved
        sections. Reused as is when its inputs have not changed (resume)."""
        r = self.run
        g = _clip(goal, 4000)
        idx = self.rm.index(1500) if self.rm is not None else ""
        gaps = self.rm.gaps() if self.rm is not None else []
        share = max(600, (FREE_PROMPT_CHARS - len(SYNTH) - len(SUMMARY_RULE) - len(g) - len(idx) - 1500) // max(1, len(body)))
        parts = [f"## {x['title']} [{'مُتحقق منه' if x['status'] == 'verified' else 'لم يجتز المراجعة'}]\n{report_mod.digest(x['text'], share)}"
                 for x in body]
        key = report_mod.fp("summary", g, [x["fp"] for x in body], idx, gaps, issues or [])
        saved = self._saved_section("summary", key)
        if saved:
            if hasattr(getattr(self.llm, "usage", None), "avoid"):
                self.llm.usage.avoid(1, "finalization")
            await r.emit("section_reused", {"section": "summary"})
            return saved
        user = (f"طلب المستخدم:\n{g}\n\n" + (f"فهرس الأدلة المقروءة:\n{idx}\n\n" if idx else "")
                + ("نواقص البحث:\n" + "\n".join(f"- {x}" for x in gaps) + "\n\n" if gaps else "")
                + "ملخص أقسام الفريق (النص الكامل يُلحق بالتقرير):\n\n" + "\n\n".join(parts))
        msgs = [{"role": "system", "content": SYNTH + SUMMARY_RULE}, {"role": "user", "content": user}]
        if issues:
            msgs += [{"role": "assistant", "content": prev},
                     {"role": "user", "content": "ملاحظات المدقق على التقرير:\n" + "\n".join(issues) + "\n" + FIX_RULE}]
        self.step = "finalization:summary"
        with llm_phase("finalization"):
            text = await self.llm.chat(msgs, temperature=0.2, max_tokens=1800)
        sec = {"id": "summary", "kind": "summary", "title": "الملخص التنفيذي والتوصيات", "status": "verified", "text": text.strip(),
               "sources": report_mod.urls(text), "fp": key, "by": "model", "at": time.time()}
        await self._save_section(sec)
        return sec

    async def fix_section(self, sec: dict, issues: list[str]) -> dict:
        """Revise one rejected task section, not the whole report."""
        self.step = f"finalization:{sec['id']}"
        msgs = [{"role": "system", "content": SYNTH + FIX_RULE},
                {"role": "user", "content": f"القسم «{sec['title']}»:\n\n{_clip(sec['text'], 9000)}\n\nملاحظات المدقق:\n" + "\n".join(issues)}]
        with llm_phase("finalization"):
            text = await self.llm.chat(msgs, temperature=0.2, max_tokens=2500)
        text = self.rm.link_ids(text) if self.rm is not None else text
        new = {**sec, "text": text.strip(), "sources": report_mod.urls(text), "by": "model:revised",
               "fp": report_mod.fp("task-revised", sec["fp"], issues), "at": time.time(), "revised_from": sec["fp"]}
        await self._save_section(new)
        return new

    def _delta_section(self, body: list[dict]) -> dict:
        """What changed in this version, by code: the assumption, what was recomputed, what was kept."""
        d = self.run.delta
        old, new = d.get("old") or {}, d["change"]
        unit = new.get("unit") or old.get("unit") or ""
        title = {t["id"]: t.get("title") or t["id"] for t in self.run.tasks.values()}
        base = d.get("base_sections") or {}
        changed = [x["id"] for x in body if base.get(x["id"]) != x["fp"]]
        d["sections_changed"] = changed
        lines = [f"- الافتراض المتغير: الميزانية السابقة {assumptions.fmt(old['value']) + ' ' + unit if old.get('value') else '(لم تُذكر سابقًا)'} "
                 f"← الجديدة {assumptions.fmt(new['new'])} {unit} (من المستخدم).",
                 "- أُعيد حسابه لأنه يعتمد على الميزانية: " + ("، ".join(title.get(i, i) for i in d.get("recomputed") or []) or "لا شيء"),
                 "- بقي كما هو بمصادره (لا يعتمد على الميزانية، ولم يُعد بحثه): " + ("، ".join(title.get(i, i) for i in d.get("reused") or []) or "لا شيء"),
                 "- أقسام تغيّر نصها: " + ("، ".join(x["title"] for x in body if x["id"] in changed) or "لا شيء"),
                 "- البحث والأدلة: أُعيد استخدامها من النسخة السابقة دون فتح أي مصدر من جديد."]
        text = "\n".join(lines)
        return {"id": "delta", "kind": "delta", "title": "ما تغيّر في هذه النسخة", "status": "verified", "text": text,
                "sources": [], "fp": report_mod.fp("delta", text), "by": "code", "at": time.time()}

    async def build_report(self, goal: str, tasks: list[dict]) -> dict:
        """Final Report Builder (app/report.py): skeleton from the plan, sections from saved reviewed outputs,
        the model only for the summary, then one audit of the assembled report and, if rejected, a revision of
        the sections the rejection is about (not a rewrite of everything)."""
        r = self.run
        ok = all(t["status"] == "done" for t in tasks)
        body = report_mod.task_sections(tasks)
        for sec in body:
            await self._save_section(sec)
        read = quality_gate.read_set(r.research_state, [u for e in r.events if e.get("type") == "tool_result"
                                                         for u in e.get("read") or []]) if self.rm is not None else set()
        gaps = self.rm.gaps() if self.rm is not None else []
        summary = await self.write_summary(goal, body)
        fin = any((t.get("fin") or {}).get("computed") for t in tasks)
        check = {"id": "final", "instructions": goal + (
                     "\n\nيجب أن يذكر الجواب التصحيح الحسابي صراحةً." if self.note else "") + "\n\n" + report_mod.SYSTEM_NOTE
                 + ("\n" + finmodel.REVIEW_NOTE if fin else ""),
                 "acceptance": "يجيب عن طلب المستخدم كاملًا ومتسقًا مع نتائج الفريق بلا ادعاءات مختلقة"}

        def compose(summary, body):
            limits = report_mod.limits_section(tasks, gaps, self.note)
            srcs = report_mod.sources_section([summary, *body], read)
            extra = [self._delta_section(body)] if r.delta and r.delta.get("change") else []
            secs = [summary, *extra, *body, limits, srcs]
            return report_mod.assemble(secs), secs

        answer, secs = compose(summary, body)
        v = await self.final_verdict(check, answer)
        await self.run.emit("final_check", v)
        revised: list[str] = []
        if not v["pass"] and v.get("issues"):
            targets = report_mod.locate(v["issues"], v, body)
            await r.emit("report_revision", {"sections": targets})
            for sid in targets[:2]:
                if sid == "summary":
                    summary = await self.write_summary(goal, body, v["issues"], summary["text"])
                else:
                    i = next(k for k, x in enumerate(body) if x["id"] == sid)
                    body[i] = await self.fix_section(body[i], v["issues"])
                revised.append(sid)
            answer, secs = compose(summary, body)
            v = await self.final_verdict(check, answer)
            await self.run.emit("final_check", v)
        r.report = {"sections": secs, "assembled_fp": report_mod.fp(answer), "revised": revised,
                    "verdict": {k: v.get(k) for k in ("pass", "score")}, "built_at": time.time()}
        await self.mark("finalization", "deliver")
        return {"ok": ok and v["pass"], "partial": v["pass"] or any(t["status"] == "done" for t in tasks),
                "output": answer, "verdict": v}

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
                with llm_phase("finalization"):
                    answer = await self.llm.chat([{"role": "system", "content": SYNTH + DRAFT_RULE}, {"role": "user", "content":
                                              f"طلب المستخدم:\n{goal}\n\nنتائج الفريق (كلها رُفضت من المدقق):\n\n" + "\n\n".join(parts)}],
                                                 temperature=0.2, max_tokens=4000)
            except ModelPaused:
                raise
            except LLMError:
                return {"ok": False, "output": "فشلت كل المهام ولم يُنتج الفريق نتيجة يمكن الوثوق بها.\n\n" + "\n\n".join(parts)}
            return {"ok": False, "output": answer, "verdict": {"pass": False, "score": None, "draft": True,
                                                               "issues": ["لم تجتز أي مهمة التحقق؛ هذا الجواب مسودة غير متحقق منها."]}}
        await self.run.emit("synthesizing", {"final": final})
        self.step = "finalization"
        if final:
            return await self.build_report(goal, tasks)
        if getattr(getattr(self.llm, "cfg", None), "provider", "") == "free" and parts:  # every result keeps a fair share instead of losing the middle
            g = _clip(goal, 5000)
            share = max(800, (FREE_PROMPT_CHARS - len(SYNTH) - len(g) - 600) // len(parts))
            g_parts = [_clip(p_, share) for p_ in parts]
        else:
            g, g_parts = goal, parts
        if final and self.rm is not None:
            idx = self.rm.index(2000)
            gaps = self.rm.gaps()
            if idx or gaps:
                g += ("\n\n---\nفهرس الأدلة التي قرأها النظام فعلًا (استشهد بهذه الروابط وحدها؛ أي رابط آخر سيُوسم «لم يقرأه النظام»):\n" + idx
                      + ("\n\nنواقص البحث (اذكرها في «حدود هذا الجواب» ولا تملأها بأرقام):\n" + "\n".join(f"- {x}" for x in gaps) if gaps else ""))
        msgs = [{"role": "system", "content": SYNTH},
                {"role": "user", "content": f"طلب المستخدم:\n{g}\n\nنتائج الفريق:\n\n" + "\n\n".join(g_parts)}]
        answer = await self._long(msgs, 0.3)
        if not final:
            return {"ok": ok, "partial": True, "output": answer}
        check = {"id": "final", "instructions": goal + (
                     "\n\nيجب أن يذكر الجواب التصحيح الحسابي صراحةً." if self.note else ""),
                 "acceptance": "يجيب عن طلب المستخدم كاملًا ومتسقًا مع نتائج الفريق بلا ادعاءات مختلقة"}
        v = await self.final_verdict(check, answer)
        await self.run.emit("final_check", v)
        if not v["pass"] and v.get("issues"):
            msgs += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "المدقق رفض الجواب:\n" + "\n".join(v["issues"]) + f"\n{v.get('fix', '')}\nأعد كتابة الجواب النهائي مصححًا. "
                      "إن كان رقم غير معقول مبنيًا على تقدير لا تملك بيانات لتصحيحه، فاحذفه وما بُني عليه وصرّح بأن البيانات لا تكفي بدل اختلاق بديل."}]
            answer = await self._long(msgs, 0.2)
            v = await self.final_verdict(check, answer)
            await self.run.emit("final_check", v)
        return {"ok": ok and v["pass"], "partial": v["pass"] or any(t["status"] == "done" for t in tasks),
                "output": answer, "verdict": v}
