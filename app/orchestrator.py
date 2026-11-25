"""The orchestrator: plan -> dispatch specialist agents in parallel -> verify -> retry -> synthesize.

Large subtasks can be delegated to a sub-team (a nested orchestration), so one request can
spawn many agents, bounded by a per-run agent budget. Every step is streamed as an event.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field

from . import memory
from .agents import ROLES, run_agent
from .llm import LLM, LLMError

MAX_DEPTH = 2
MAX_TASKS_PER_PLAN = 10
MAX_ATTEMPTS = 3
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
    resumed: dict | None = None  # {"from": run id, "reused": [task ids]} when continuing a stopped run
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
                "tasks_total": len(self.tasks),
                "tasks_done": sum(1 for t in self.tasks.values() if t["status"] == "done"),
                "tasks_failed": sum(1 for t in self.tasks.values() if t["status"] in ("failed", "skipped"))}

    def full(self) -> dict:
        return {**self.summary(), "llm": self.llm_public, "max_agents": self.max_agents, "tasks": self.tasks,
                "result": self.result, "verdict": self.verdict, "memory": self.memory, "usage": self.usage, "error": self.error, "saved": self.saved,
                "resumed": self.resumed, "events": self.events}


ROLE_LIST = "\n".join(f"- {r.id}: {r.name} — {r.mission}" for r in ROLES.values())

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
- decompose=true فقط لمهمة ضخمة تحتاج فريقًا فرعيًا كاملًا{decompose_note}.
- لا تضف مهمة "تجميع نهائي"؛ المنسق يجمع النتائج بنفسه.
- إن أُرفق «سياق من مهام سابقة» فاستخدم ما يتصل بالطلب فقط: انقل المعطيات ذات الصلة (أرقام، قرارات، تفضيلات) صراحةً إلى تعليمات المهام لأن الوكلاء لا يرون هذا السياق، ولا تُعِد بحث ما هو معروف إلا إن احتاج تحديثًا.
- اكتب بلغة المستخدم."""

VERIFIER = """أنت مدقق صارم ومستقل. قيّم ناتج وكيل مقابل المهمة ومعيار القبول.
افحص: هل أنجز المطلوب كاملًا؟ هل فيه ادعاءات بلا دليل أو أرقام مختلقة أو تناقضات أو كود لن يعمل؟
أعد JSON فقط: {"pass": bool, "score": 0-10, "issues": ["مشكلة محددة"], "fix": "ما يجب تصحيحه بالضبط"}
pass=true فقط إذا score>=7 ولا توجد مشكلة جوهرية.
ملاحظة: إذا كانت البيانات المطلوبة غير متاحة فعلًا، فالناتج الذي يصرّح بذلك بوضوح ويقدّم تقديرًا معقولًا موسومًا بأنه تقدير (مع منهجه) يُعدّ مقبولًا. الذي يُرفض هو الاختلاق أو الإيهام بالدقة. ويُرفض أيضًا ترك المهمة بلا إنجاز بحجة نقص البيانات إن كان التقدير الموسوم ممكنًا.
تحقق من صحة المعادلات والحسابات بنفسك؛ الخطأ الحسابي مشكلة جوهرية."""

SYNTH = """أنت المنسق. اجمع نتائج فريقك في جواب نهائي واحد متماسك للمستخدم بلغته وبتنسيق Markdown.
- لا تضف معلومات غير موجودة في النتائج.
- احتفظ بالروابط والمصادر.
- إن فشلت مهام أو لم تُتحقق، اذكر ذلك بصدق في قسم قصير "حدود هذا الجواب".
- إذا كان في النتائج تقدير موسوم، استخدمه لتقديم جواب عملي (أرقام وسيناريوهات) مع إبقاء وسم التقدير."""


def _sanitize_plan(plan: dict, depth: int) -> list[dict]:
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
        deps = [str(d).replace(".", "_") for d in (t.get("depends_on") or []) if str(d).replace(".", "_") in seen]
        tasks.append({"id": tid, "title": str(t.get("title") or tid)[:120], "role": role,
                      "instructions": str(t.get("instructions") or t.get("title") or ""),
                      "acceptance": str(t.get("acceptance") or "ينجز التعليمات كاملة بدقة"),
                      "depends_on": deps, "decompose": bool(t.get("decompose")) and depth < MAX_DEPTH - 1})
        seen.add(tid)
    if not tasks:
        raise ValueError("الخطة بلا مهام صالحة")
    return tasks


async def plan(llm: LLM, goal: str, depth: int) -> dict:
    note = "" if depth == 0 else " (غير مسموح في هذا المستوى)"
    sys = PLANNER.format(roles=ROLE_LIST, max_tasks=MAX_TASKS_PER_PLAN if depth == 0 else 5, decompose_note=note)
    p = await llm.chat_json([{"role": "system", "content": sys}, {"role": "user", "content": goal}], max_tokens=3000)
    return {"simple": bool(p.get("simple")) and depth == 0, "summary": str(p.get("summary", "")),
            "tasks": _sanitize_plan(p, depth)}


async def verify(llm: LLM, task: dict, output: str) -> dict:
    try:
        v = await llm.chat_json([{"role": "system", "content": VERIFIER}, {"role": "user", "content":
                                 f"المهمة: {task['instructions']}\nمعيار القبول: {task['acceptance']}\n\nالناتج:\n{output[:12000]}"}],
                                temperature=0.0, max_tokens=800)
    except (ValueError, LLMError) as e:
        return {"pass": False, "score": 0, "issues": [f"تعذر التحقق: {e}"], "fix": ""}
    score = float(v.get("score") or 0)
    return {"pass": bool(v.get("pass")) and score >= 7, "score": score,
            "issues": [str(x) for x in (v.get("issues") or [])][:6], "fix": str(v.get("fix") or "")}


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
            goal = r.goal
            if r.memory["recalled"]:
                goal += ("\n\n---\nسياق من مهام سابقة لهذا المستخدم (من ذاكرة المنسق؛ استخدمه إن كان ذا صلة وتجاهله إن لم يكن):\n"
                         + memory.render_context(r.memory["recalled"]))
            out = await self.orchestrate(goal, depth=0, prefix="")
            r.result = out["output"]
            r.verdict = out.get("verdict", {})
            status = "done" if out["ok"] else ("partial" if out.get("partial") else "failed")
            if r.memory_space and status in ("done", "partial"):
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
            await r.emit("memory_error", {"stage": "recall", "error": str(e)[:200]})
            return []
        r.memory["recalled"] = memory.recall(r.goal, existing)
        await r.emit("memory_recalled", {"count": len(r.memory["recalled"]), "total": len(existing),
                                         "items": [{"kind": i["kind"], "content": i["content"][:160]} for i in r.memory["recalled"]]})
        return existing

    async def remember(self, existing: list[dict]):
        r = self.run
        try:
            res = await memory.extract_and_save(self.llm, r.memory_space, r.id, r.goal, r.result, existing)
        except Exception as e:  # noqa: BLE001
            await r.emit("memory_error", {"stage": "save", "error": str(e)[:200]})
            return
        r.memory["saved"], r.memory["superseded"] = res["saved"], res["superseded"]
        await r.emit("memory_saved", {"count": len(res["saved"]), "superseded": len(res["superseded"]),
                                      "items": [{"kind": i["kind"], "content": i["content"][:160]} for i in res["saved"]]})

    async def orchestrate(self, goal: str, depth: int, prefix: str) -> dict:
        r = self.run
        p = await self._reuse_plan() if depth == 0 and self.prior else None
        if p:
            return await self._continue(goal, p)
        await r.emit("planning", {"scope": prefix or "root", "depth": depth})
        p = await plan(self.llm, goal, depth)
        tasks = p["tasks"]
        for t in tasks:
            t["id"] = prefix + t["id"]
            t["depends_on"] = [prefix + d for d in t["depends_on"]]
            t.update(status="pending", attempts=[], output="", depth=depth, parent=prefix.rstrip(".") or None)
            r.tasks[t["id"]] = t
        await r.emit("plan", {"scope": prefix or "root", "summary": p["summary"], "simple": p["simple"],
                              "tasks": [{k: t[k] for k in ("id", "title", "role", "depends_on", "decompose")} for t in tasks]})

        if p["simple"] and len(tasks) == 1:
            await self.run_task(tasks[0], goal, verify_it=False)
            return {"ok": tasks[0]["status"] == "done", "output": tasks[0]["output"]}

        await self.run_dag(tasks)
        return await self.synthesize(goal, tasks, final=(depth == 0))

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
            try:
                res = await run_agent(self.llm, role, instr, context, emit, r.can_exec)
            except LLMError as e:
                t["attempts"].append({"agent": agent_id, "error": str(e)})
                await r.emit("agent_error", {"task": t["id"], "agent": agent_id, "error": str(e)})
                continue
            t["output"] = res["output"]
            v = await verify(self.llm, t, res["output"]) if verify_it else {"pass": True, "score": None, "issues": []}
            t["attempts"].append({"agent": agent_id, "steps": res["steps"], "confidence": res["confidence"],
                                  "sources": res["sources"], "verdict": v})
            await r.emit("verified", {"task": t["id"], "agent": agent_id, **v})
            if v["pass"]:
                t["status"] = "done"
                await r.emit("task_finished", {"task": t["id"], "status": "done", "attempts": attempt})
                return
            feedback = "\n".join(f"- {i}" for i in v["issues"]) + (f"\nالمطلوب: {v['fix']}" if v.get("fix") else "")
        t["status"] = "failed"
        await r.emit("task_finished", {"task": t["id"], "status": "failed", "attempts": len(t["attempts"])})

    async def synthesize(self, goal: str, tasks: list[dict], final: bool) -> dict:
        parts = []
        for t in tasks:
            state = {"done": "مُتحقق منها", "failed": "لم تجتز التحقق", "skipped": "لم تُنفذ"}.get(t["status"], t["status"])
            parts.append(f"## {t['id']} — {t['title']} [{state}]\n{t['output'] or '(لا ناتج)'}")
        ok = all(t["status"] == "done" for t in tasks)
        if not any(t["status"] == "done" for t in tasks):
            return {"ok": False, "output": "فشلت كل المهام ولم يُنتج الفريق نتيجة يمكن الوثوق بها.\n\n" + "\n\n".join(parts)}
        await self.run.emit("synthesizing", {"final": final})
        msgs = [{"role": "system", "content": SYNTH},
                {"role": "user", "content": f"طلب المستخدم:\n{goal}\n\nنتائج الفريق:\n\n" + "\n\n".join(parts)}]
        answer = await self.llm.chat(msgs, temperature=0.3, max_tokens=4000)
        if not final:
            return {"ok": ok, "partial": True, "output": answer}
        check = {"id": "final", "instructions": goal,
                 "acceptance": "يجيب عن طلب المستخدم كاملًا ومتسقًا مع نتائج الفريق بلا ادعاءات مختلقة"}
        v = await verify(self.llm, check, answer)
        await self.run.emit("final_check", v)
        if not v["pass"] and v.get("issues"):
            msgs += [{"role": "assistant", "content": answer},
                     {"role": "user", "content": "المدقق رفض الجواب:\n" + "\n".join(v["issues"]) + f"\n{v.get('fix', '')}\nأعد كتابة الجواب النهائي مصححًا."}]
            answer = await self.llm.chat(msgs, temperature=0.2, max_tokens=4000)
            v = await verify(self.llm, check, answer)
            await self.run.emit("final_check", v)
        return {"ok": ok and v["pass"], "partial": v["pass"] or any(t["status"] == "done" for t in tasks),
                "output": answer, "verdict": v}
