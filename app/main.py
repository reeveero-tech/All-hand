"""HTTP API + static UI."""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import hmac
import json
import os
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assumptions, export, intake, memory, store, tools
from . import report as report_mod
from .research import browser, documents, evidence
from .research import manager as research_manager
from .agents import ROLES
from .llm import LLM, LLMError, resolve_config
from .llm import holds as llm_holds
from .orchestrator import DEFAULT_MAX_AGENTS, Orchestrator, Run

try:  # make /proc/<pid>/environ unreadable to sandboxed child code running as the same user
    ctypes.CDLL("libc.so.6").prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 0
except OSError:
    pass

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_LIVE = 200
MAX_CONCURRENT_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "4"))

@asynccontextmanager
async def lifespan(_app):
    yield
    # Shutdown (deploy/restart): record honestly that live runs were interrupted. If this process still
    # finishes a run before it is killed, the final save overwrites this snapshot.
    await shutdown_runs()


async def shutdown_runs(wait: float = 10):
    """Mark live runs interrupted, stop them, and save them as interrupted (never as user-cancelled)."""
    live = [r for r in RUNS.values() if r.status == "running"]
    for run in live:
        run.status = "interrupted"
    tasks = [TASKS[r.id] for r in live if r.id in TASKS]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=wait)  # their own final save runs here
    for run in live:
        if run.saved is not True:
            await store.save({**run.full(), "owner": run.owner, "heartbeat_at": time.time()})


app = FastAPI(title="All-hand", version="3.1.0", lifespan=lifespan)
RUNS: "OrderedDict[str, Run]" = OrderedDict()
TASKS: dict[str, asyncio.Task] = {}
STARTED = time.time()


class RunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=20000)
    provider: str = "free"  # free | byok | platform
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    access_code: str | None = None
    max_agents: int | None = Field(default=None, ge=1, le=500)
    memory_space: str | None = None  # private random id kept in the browser; None = no memory
    follow_up_of: str | None = None  # id of one of your finished runs: continue that conversation
    resume_from: str | None = None  # id of one of your stopped runs: reuse its plan and verified outputs
    scenario: bool = False  # test / hypothetical run: memory keeps it, but never as facts about the user
    approve_plan: bool = False  # show the plan and wait for the owner's approval before any agent works
    clarify: bool = True  # ask before planning when the request misses essentials (product, budget); see app/intake.py


class PlanDecision(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    drop: list[str] = Field(default_factory=list, max_length=50)  # task ids to leave out
    note: str | None = Field(default=None, max_length=2000)  # an instruction every agent must follow


class Clarification(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=10)  # question key -> the owner's answer
    assume: bool = False  # continue without the missing details: the team chooses, justifies and labels assumptions


class MemoryEdit(BaseModel):
    content: str | None = Field(default=None, max_length=400)
    nature: str | None = None


_RUN_ID = re.compile(r"[0-9a-f]{12}")


def _owner(x_client_key: str | None) -> str:
    """Every run belongs to the browser that created it. The client keeps a random secret
    (X-Client-Key); the server keeps only its SHA-256, so nobody else can list or read the run."""
    if not memory.valid_space(x_client_key):
        raise HTTPException(400, "مطلوب ترويسة X-Client-Key صالحة.")
    return hashlib.sha256(x_client_key.encode()).hexdigest()


def _owns(data_owner: str | None, owner: str) -> bool:
    return bool(data_owner) and hmac.compare_digest(str(data_owner), owner)


def _live(run_id: str, owner: str) -> Run:
    run = RUNS.get(run_id)
    if not run or not _owns(run.owner, owner):
        raise HTTPException(404, "غير موجود")  # same answer whether the run exists or not
    return run


RESUMABLE = {"interrupted", "failed", "partial", "cancelled", "paused_quota", "paused_provider"}
CONTEXT_CHARS = 8000  # how much of the previous answer a follow-up carries


async def _own_run(run_id: str, owner: str) -> dict:
    """Data of one of the caller's runs, live or stored. Same 404 for missing and foreign runs."""
    if run_id in RUNS:
        return _live(run_id, owner).full()
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(404, "غير موجود")
    data = await store.load(run_id)
    if not data or not _owns(data.get("owner"), owner):
        raise HTTPException(404, "غير موجود")
    return {**data, "status": store.effective_status(data, live=False)}


def follow_up_context(prev: dict) -> str:
    """What a follow-up needs from the previous turn: the request, its status, and its answer
    (or, if it has no answer, the outputs of the subtasks that passed verification)."""
    answer = (prev.get("result") or "").strip()
    if not answer:
        answer = "\n\n".join(f"### {t.get('title')}\n{t.get('output')}" for t in (prev.get("tasks") or {}).values()
                              if t.get("status") == "done" and t.get("output"))
    note = {"done": "", "partial": " (اكتملت جزئيًا)", "failed": " (فشلت)", "cancelled": " (أوقفها المستخدم)",
            "interrupted": " (انقطعت قبل اكتمالها)", "paused_quota": " (متوقفة مؤقتًا: نفدت حصة النموذج)",
            "paused_provider": " (متوقفة مؤقتًا: المزود غير متاح)"}.get(prev.get("status"), "")
    if (prev.get("verdict") or {}).get("draft"):
        note += " — نتيجتها مسودة لم يتحقق منها المدقق، فلا تعامل أرقامها كحقائق"
    if len(answer) > CONTEXT_CHARS:
        answer = answer[:CONTEXT_CHARS] + "\n\n[... اختُصرت بقية النتيجة السابقة]"
    return (f"سياق المحادثة السابقة (هذا طلب متابعة للمهمة {prev.get('id')}{note}):\n"
            f"الطلب السابق: {prev.get('goal')}\n\nالنتيجة السابقة:\n{answer or '(لا توجد نتيجة)'}")


def known_rejected(prev: dict) -> dict:
    """Figures the previous run's own final check proved wrong: implausible values anywhere they appear,
    wrong totals and equation results where they are stated as such."""
    v = prev.get("verdict") or {}
    totals = [float(e["stated"]) for e in v.get("arithmetic") or [] if isinstance(e.get("stated"), (int, float))]
    values = []
    for i in v.get("plausibility") or []:
        if i.get("severity") == "error":
            values += [float(x) for x in i.get("values") or [] if isinstance(x, (int, float))]
    return {"values": values[:30], "totals": totals[:30]}


async def _resumable(run_id: str, owner: str) -> dict:
    """Stored data of the caller's own stopped run."""
    data = await _own_run(run_id, owner)
    if data.get("status") == "running":
        raise HTTPException(409, "هذه المهمة ما زالت تعمل.")
    if data.get("status") not in RESUMABLE:
        raise HTTPException(409, "لا يمكن استكمال مهمة اكتملت أو ما زالت تعمل.")
    return data


def _running() -> int:
    return sum(1 for r in RUNS.values() if r.status == "running")


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - STARTED), "runs_live": _running(),
            "history": "supabase" if store.enabled() else "memory",
            "search": {k: v for k, v in tools.STATS.items()},  # counters and the last provider error (no secrets)
            "providers": tools.registry.health(),
            "model_holds": llm_holds(),  # provider base URL -> {kind, until}: calls refused locally, not sent  # per provider: breaker state, requests, results, errors
            "research": {"manager": research_manager.ENABLED, "documents": documents.available(),
                         "document_stats": dict(documents.STATS), "budget": asdict(research_manager.Budget()),
                         "browser_worker": browser.status()}}


@app.get("/api/roles")
async def roles():
    return [{"id": r.id, "name": r.name, "mission": r.mission, "tools": list(r.tools)} for r in ROLES.values()]


@app.post("/api/runs", status_code=202)
async def create_run(req: RunRequest, x_client_key: str | None = Header(default=None)):
    owner = _owner(x_client_key)
    if _running() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(429, "الخادم مشغول بمهام أخرى، حاول بعد قليل.")
    try:
        cfg = resolve_config(req.provider, req.api_key, req.model, req.base_url, req.access_code)
    except LLMError as e:
        raise HTTPException(400, str(e)) from e
    if req.memory_space is not None and not memory.valid_space(req.memory_space):
        raise HTTPException(400, "معرّف مساحة الذاكرة غير صالح.")
    if req.resume_from and req.follow_up_of:
        raise HTTPException(400, "اختر الاستكمال أو المتابعة، لا الاثنين.")
    prior = await _resumable(req.resume_from, owner) if req.resume_from else None
    prev = await _own_run(req.follow_up_of, owner) if req.follow_up_of else None
    if prev and prev.get("status") == "running":
        raise HTTPException(409, "انتظر حتى تنتهي المهمة السابقة ثم تابع.")
    cap = {"free": 25, "platform": 500}.get(cfg.provider, DEFAULT_MAX_AGENTS)
    run = Run(goal=prior["goal"] if prior else req.goal.strip(), llm_public=cfg.public(), max_agents=min(req.max_agents or cap, cap),
              can_exec=cfg.provider == "platform" or os.environ.get("ALLOW_CODE_EXEC") == "1",
              memory_space=req.memory_space, owner=owner, approve_plan=req.approve_plan and not prior, clarify=req.clarify,
              scenario=req.scenario or any(bool(d and d.get("scenario")) for d in (prior, prev)))  # tests stay tests
    if prior:  # resume keeps the conversation it belonged to
        run.follow_up_of, run.thread_id, run.context = prior.get("follow_up_of"), prior.get("thread_id"), prior.get("context") or ""
        run.goal_checks = prior.get("goal_checks")
        run.prior_check = prior.get("prior_check")
        run.delta = prior.get("delta")
        run.brief = prior.get("brief")  # an unanswered intake question is asked again; an answered one is not
    elif prev:
        run.follow_up_of = prev["id"]
        run.thread_id = prev.get("thread_id") or prev["id"]
        run.context = follow_up_context(prev)
        run.research_reuse = prev.get("research_state")  # read evidence stays valid; same owner (checked above)
        kr = known_rejected(prev)
        run.prior_check = {"from": prev["id"], "known_rejected": kr["values"], "known_rejected_totals": kr["totals"],
                           "prev_figures": (prev.get("verdict") or {}).get("figures_checked")}
        change = assumptions.budget_change(run.goal)
        if change and any(not t.get("parent") and t.get("output") for t in (prev.get("tasks") or {}).values()):
            # only the budget changes: keep plan, research and independent results; recompute what depends on it
            run.delta = {"from": prev["id"], "change": change, "old": assumptions.prior_budget(prev)}
            run._base = prev
            run.research_reuse = None
    RUNS[run.id] = run
    while len(RUNS) > MAX_LIVE:
        old_id, old = next(iter(RUNS.items()))
        if old.status == "running":
            break
        RUNS.pop(old_id)

    async def persist(final: bool = False) -> bool:
        if llm_ref and hasattr(getattr(llm_ref[0], "usage", None), "as_dict"):
            run.usage = llm_ref[0].usage.as_dict()  # every save carries the consumption so far, not only the last one
        return await store.save({**run.full(), "owner": run.owner, "heartbeat_at": time.time()})

    async def heartbeat():
        while run.status == "running":
            await asyncio.sleep(store.HEARTBEAT)
            if run.status == "running":
                await persist()

    llm_ref: list = []

    async def go():
        llm = LLM(cfg)
        llm_ref.append(llm)
        if prior and hasattr(getattr(llm, "usage", None), "load"):  # a resumed run continues the count of the run it resumes
            llm.usage.load(prior.get("usage"))
        beat = asyncio.create_task(heartbeat()) if store.enabled() else None
        try:
            await persist()  # visible in history (and detectable as interrupted) from the first second
            await Orchestrator(run, llm, persist=persist if store.enabled() else None,
                               prior=prior).execute()
        finally:
            if beat:
                beat.cancel()
            await llm.close()
            TASKS.pop(run.id, None)

    TASKS[run.id] = asyncio.create_task(go())
    return {"id": run.id, "status": run.status}


@app.get("/api/runs")
async def list_runs(x_client_key: str | None = Header(default=None)):
    """The owner's runs: live ones from this process plus stored history (so a restart loses nothing)."""
    owner = _owner(x_client_key)
    live = {r.id: r.summary() for r in reversed(RUNS.values()) if _owns(r.owner, owner)}
    stored = await store.list_for_owner(owner)
    runs = dict(live)
    for row in stored or []:
        if row["id"] not in runs:
            row["status"] = store.effective_status(row, live=False)
            runs[row["id"]] = {k: v for k, v in row.items() if k != "heartbeat_at"}
    out = sorted(runs.values(), key=lambda r: -(float(r.get("created_at") or 0)))
    hist = "unavailable" if stored is None else ("supabase" if store.enabled() else "memory")
    return JSONResponse(out, headers={"X-History": hist})


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, x_client_key: str | None = Header(default=None)):
    owner = _owner(x_client_key)
    if run_id in RUNS:
        return _live(run_id, owner).full()
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(404, "غير موجود")
    data = await store.load(run_id)
    if not data or not _owns(data.get("owner"), owner):  # rows saved before ownership have no owner: private
        raise HTTPException(404, "غير موجود")
    data.pop("owner", None)
    data["status"] = store.effective_status(data, live=False)
    data.pop("heartbeat_at", None)
    data.pop("deliverable", None)  # the file itself is served by /export; its record (size, checksum) stays
    return data


@app.get("/api/runs/{run_id}/export")
async def export_run(run_id: str, x_client_key: str | None = Header(default=None)):
    """The run's final file (only the owner; same 404 otherwise). The delivered file is served as stored when its
    checksum still matches; a run without one (stopped before delivery) gets a report rebuilt from its saved data."""
    data = await _own_run(run_id, _owner(x_client_key))
    data.pop("owner", None)
    rec = next((f for f in data.get("files") or [] if f.get("verified")), None)
    stored = data.get("deliverable") or ""
    if rec and report_mod.verify_file(rec, stored, []):
        body, source, name = stored, "stored", rec["name"]
    else:
        body, source, name = export.to_markdown(data), "rebuilt", f"all-hand-{run_id}.md"
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-AGOS-File": source,
                             "X-AGOS-File-SHA256": hashlib.sha256(body.encode("utf-8")).hexdigest()})


@app.get("/api/runs/{run_id}/evidence")
async def run_evidence(run_id: str, x_client_key: str | None = Header(default=None)):
    """The run's evidence ledger, rebuilt from its recorded tool results (only the owner; same 404 otherwise):
    each URL with the furthest stage it really reached (discovered, opened, extracted, relevant, cited),
    who found it, failure reasons, page dates and table counts."""
    data = await _own_run(run_id, _owner(x_client_key))
    led = evidence.build(data.get("events") or [], data.get("result") or "")
    st = data.get("research_state") or {}
    if st:  # what the research manager planned and how each facet ended, and the quality gate's verdict
        led["research"] = {"facets": st.get("facets"), "subject": st.get("subject"), "country": st.get("country"),
                           "status": st.get("status"), "used": st.get("used"), "budget": st.get("budget"),
                           "rounds": st.get("rounds"), "searches": st.get("searches"), "conflicts": st.get("conflicts"),
                           "readings": [{k: r.get(k) for k in ("id", "url", "title", "publisher", "published", "facets", "method",
                                                                "kind", "completeness", "pages", "tables_total", "figures", "class",
                                                                "useful", "from_previous")}
                                        for r in (st.get("readings") or {}).values()],
                           "failed": list((st.get("failed") or {}).values())}
        led["gate"] = data.get("research_gate")
    return led


@app.post("/api/runs/{run_id}/plan")
async def decide_plan(run_id: str, d: PlanDecision, x_client_key: str | None = Header(default=None)):
    """The owner approves (optionally dropping tasks and adding a note) or rejects a plan that is waiting."""
    run = _live(run_id, _owner(x_client_key))
    if run.awaiting != "plan" or not run._decision or run._decision.is_set():
        raise HTTPException(409, "لا توجد خطة تنتظر قرارك في هذه المهمة.")
    root = {t["id"] for t in run.tasks.values() if not t.get("parent")}
    drop = [x for x in d.drop if x in root]
    if d.action == "approve" and len(drop) >= len(root):
        raise HTTPException(400, "لا يمكن اعتماد خطة بلا مهام؛ ارفضها بدلًا من ذلك.")
    run.plan_decision = {"action": d.action, "dropped": drop, "note": (d.note or "").strip() or None, "at": time.time()}
    run._decision.set()
    return {"ok": True, "decision": run.plan_decision}


@app.post("/api/runs/{run_id}/clarify")
async def clarify_run(run_id: str, c: Clarification, x_client_key: str | None = Header(default=None)):
    """The owner answers the intake questions, or chooses to continue with labelled assumptions."""
    run = _live(run_id, _owner(x_client_key))
    if run.awaiting != "clarification" or not run._decision or run._decision.is_set():
        raise HTTPException(409, "لا توجد أسئلة تنتظر إجابتك في هذه المهمة.")
    answers = {k: v.strip() for k, v in c.answers.items() if k in intake.QUESTIONS and v and v.strip()}
    if any(len(v) > 300 for v in answers.values()):
        raise HTTPException(400, "الإجابة طويلة؛ 300 حرف لكل سؤال كحد أقصى.")
    missing = [k for k in (run.brief or {}).get("blocking") or [] if k not in answers]
    if missing and not c.assume:
        raise HTTPException(400, "أجب عن: " + "، ".join(intake.LABEL[k] for k in missing) + "، أو اختر المتابعة بافتراضات.")
    run.clarification = {"action": "answer", "answers": answers, "assume": c.assume, "at": time.time()}
    run._decision.set()
    return {"ok": True}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, x_client_key: str | None = Header(default=None)):
    _live(run_id, _owner(x_client_key))
    t = TASKS.get(run_id)
    if not t:
        raise HTTPException(404, "لا توجد مهمة جارية بهذا المعرف")
    t.cancel()
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str, request: Request, after: int = -1, x_client_key: str | None = Header(default=None)):
    run = _live(run_id, _owner(x_client_key))

    async def gen():
        q = run.subscribe()
        try:
            for ev in list(run.events):
                if ev["seq"] > after:
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            last = run.events[-1]["seq"] if run.events else -1
            while run.status == "running" or not q.empty():
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev["seq"] > last:
                    last = ev["seq"]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev["type"] == "run_finished":
                    return
        finally:
            run.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.exception_handler(memory.MemoryUnavailable)
@app.exception_handler(httpx.HTTPError)
async def _memory_down(request: Request, exc: Exception):
    return JSONResponse({"detail": "تعذر الوصول إلى الذاكرة؛ لم يُنفذ الطلب. حاول لاحقًا."}, status_code=502)


def _space(x_memory_space: str | None) -> str:
    if not memory.valid_space(x_memory_space):
        raise HTTPException(400, "مطلوب ترويسة X-Memory-Space صالحة.")
    return x_memory_space


@app.get("/api/memory")
async def memory_list(x_memory_space: str | None = Header(default=None)):
    items = await memory.STORE.list(_space(x_memory_space))
    return {"count": len(items), "items": items}


@app.delete("/api/memory/{item_id}")
async def memory_delete(item_id: str, x_memory_space: str | None = Header(default=None)):
    return {"deleted": await memory.STORE.delete(_space(x_memory_space), [item_id])}


@app.patch("/api/memory/{item_id}")
async def memory_edit(item_id: str, body: MemoryEdit, x_memory_space: str | None = Header(default=None)):
    """User correction. Editing content means the user vouches for it (user_confirmed) unless they pick a nature."""
    space = _space(x_memory_space)
    changes = {}
    if body.content is not None:
        content = memory._clean(body.content)
        if not content:
            raise HTTPException(400, "المحتوى فارغ أو يشبه مفتاحًا سريًا.")
        changes.update(content=content, nature="user_confirmed")
    if body.nature is not None:
        if body.nature not in memory.USER_SETTABLE:
            raise HTTPException(400, "طبيعة غير مسموحة. المتاح: تأكيد، افتراض، بيانات اختبار.")
        changes["nature"] = body.nature
    if not changes:
        raise HTTPException(400, "لا يوجد تعديل.")
    item = await memory.STORE.update(space, item_id, changes)
    if not item:
        raise HTTPException(404, "العنصر غير موجود.")
    return item


@app.delete("/api/memory")
async def memory_clear(x_memory_space: str | None = Header(default=None)):
    return {"deleted": await memory.STORE.clear(_space(x_memory_space))}


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
