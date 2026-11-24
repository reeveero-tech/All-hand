"""HTTP API + static UI."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .agents import ROLES
from .llm import LLM, LLMError, resolve_config
from .orchestrator import DEFAULT_MAX_AGENTS, Orchestrator, Run

try:  # make /proc/<pid>/environ unreadable to sandboxed child code running as the same user
    ctypes.CDLL("libc.so.6").prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 0
except OSError:
    pass

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_LIVE = 200
MAX_CONCURRENT_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "4"))

app = FastAPI(title="All-hand", version="3.0.0")
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


def _running() -> int:
    return sum(1 for r in RUNS.values() if r.status == "running")


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - STARTED), "runs_live": _running(),
            "history": "supabase" if store.enabled() else "memory"}


@app.get("/api/roles")
async def roles():
    return [{"id": r.id, "name": r.name, "mission": r.mission, "tools": list(r.tools)} for r in ROLES.values()]


@app.post("/api/runs", status_code=202)
async def create_run(req: RunRequest):
    if _running() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(429, "الخادم مشغول بمهام أخرى، حاول بعد قليل.")
    try:
        cfg = resolve_config(req.provider, req.api_key, req.model, req.base_url, req.access_code)
    except LLMError as e:
        raise HTTPException(400, str(e)) from e
    cap = {"free": 25, "platform": 500}.get(cfg.provider, DEFAULT_MAX_AGENTS)
    run = Run(goal=req.goal.strip(), llm_public=cfg.public(), max_agents=min(req.max_agents or cap, cap),
              can_exec=cfg.provider == "platform" or os.environ.get("ALLOW_CODE_EXEC") == "1")
    RUNS[run.id] = run
    while len(RUNS) > MAX_LIVE:
        old_id, old = next(iter(RUNS.items()))
        if old.status == "running":
            break
        RUNS.pop(old_id)

    async def go():
        llm = LLM(cfg)
        try:
            await Orchestrator(run, llm).execute()
        finally:
            await llm.close()
            await store.save(run.full())
            TASKS.pop(run.id, None)

    TASKS[run.id] = asyncio.create_task(go())
    return {"id": run.id, "status": run.status}


@app.get("/api/runs")
async def list_runs():
    return [r.summary() for r in reversed(RUNS.values())]


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    if run_id in RUNS:
        return RUNS[run_id].full()
    data = await store.load(run_id)
    if not data:
        raise HTTPException(404, "غير موجود")
    return data


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    t = TASKS.get(run_id)
    if not t:
        raise HTTPException(404, "لا توجد مهمة جارية بهذا المعرف")
    t.cancel()
    return {"ok": True}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str, request: Request, after: int = -1):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "غير موجود")

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


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
