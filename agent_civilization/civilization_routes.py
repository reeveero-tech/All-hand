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

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from agent_civilization.registry import AGENTS, as_list, total_agents
from agent_civilization.routing import route as auto_route
from agent_civilization.pipeline import execute_plan


class ExecuteRequest(BaseModel):
    message: str
    data: Optional[Any] = None
    # SEC-06 (R-145): explicit per-request consent to the public (keyless) LLM
    # provider — strict bool, default False (a missing field is refusal).
    allow_public_llm: bool = Field(False, strict=True)


def register_civilization_routes(app, civilization, tasks_store, prune_tasks, verify_api_key=None,
                                 require_principal=None):
    """Attach civilization routes to an existing FastAPI app.

    When `verify_api_key` is provided (the server's own helper), the routes
    enforce the API key exactly like the /api routes (R-004).

    SEC-02: /civilization/execute stores a task and runs memory for a tenant,
    so it needs `require_principal` (the server's identity check). Without it
    the route refuses every request — it never runs without a tenant.
    """
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def _guard(api_key):
        if verify_api_key is not None:
            return verify_api_key(api_key)
        return None

    @app.get("/civilization/registry")
    async def registry_route(api_key: Optional[str] = Security(api_key_header)):
        _guard(api_key)
        return {
            "count": len(AGENTS),
            "total_addressable": total_agents(sum(len(v) for v in civilization.values())),
            "agents": as_list(),
        }

    @app.post("/civilization/plan")
    async def preview_plan(req: ExecuteRequest, api_key: Optional[str] = Security(api_key_header)):
        _guard(api_key)
        plan = auto_route(req.message)
        return {"plan": plan}

    @app.post("/civilization/execute")
    async def execute(req: ExecuteRequest, api_key: Optional[str] = Security(api_key_header)):
        if require_principal is None:
            raise HTTPException(401, "authentication is not configured for this route")
        principal = require_principal(api_key)
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
            "tenant_id": principal.tenant_id,
        }
        prune_tasks()
        # SEC-06 (R-145): this request's consent governs only the LLM calls made
        # while executing its plan (execute_plan may reach the public keyless
        # provider); reset right after — it never leaks to another request.
        from agent_civilization.core.llm import reset_public_llm_consent, set_public_llm_consent
        token = set_public_llm_consent(req.allow_public_llm)
        try:
            result = await execute_plan(plan, civilization, data=req.data)
        except BaseException:
            # R-160 (task lifecycle contract v1): the record never stays
            # "running" after the handler exits (error or cancellation); the
            # HTTP outcome is unchanged (the exception propagates).
            rec = tasks_store.get(task_id)
            if rec is not None and rec.get("status") == "running":
                rec.update(status="failed", finished_at=time.time(),
                           result={"error": "execution did not complete"})
            raise
        finally:
            reset_public_llm_consent(token)
        tasks_store[task_id]["status"] = "completed"
        tasks_store[task_id]["result"] = result
        return {"task_id": task_id, **result}
