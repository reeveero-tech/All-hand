"""
Server additions — routes exposed by AGOS Civilization 4.0.

These are appended to `server.py`; they add:
  - POST /civilization/execute   : full auto-routed pipeline (no user spec choice)
  - GET  /civilization/registry  : the curated OSS agents registry
  - GET  /civilization/plan      : preview the plan for a prompt (no execution)

The user does NOT choose a specialization; the civilization decides.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from agent_civilization.registry import AGENTS, as_list, total_agents
from agent_civilization.routing import route as auto_route
from agent_civilization.pipeline import execute_plan


class ExecuteRequest(BaseModel):
    message: str
    data: Optional[Any] = None


def register_civilization_routes(app, civilization, tasks_store, prune_tasks):
    """Attach civilization routes to an existing FastAPI app."""

    @app.get("/civilization/registry")
    async def registry():
        return {
            "count": len(AGENTS),
            "total_addressable": total_agents(sum(len(v) for v in civilization.values())),
            "agents": as_list(),
        }

    @app.post("/civilization/plan")
    async def preview_plan(req: ExecuteRequest):
        plan = auto_route(req.message)
        return {"plan": plan}

    @app.post("/civilization/execute")
    async def execute(req: ExecuteRequest):
        if not req.message.strip():
            raise HTTPException(400, "message is required")
        plan = auto_route(req.message)
        task_id = str(uuid.uuid4())
        tasks_store[task_id] = {
            "id": task_id,
            "message": req.message,
            "plan": plan,
            "status": "running",
            "created_at": time.time(),
        }
        prune_tasks()
        result = await execute_plan(plan, civilization, data=req.data)
        tasks_store[task_id]["status"] = "completed"
        tasks_store[task_id]["result"] = result
        return {"task_id": task_id, **result}
