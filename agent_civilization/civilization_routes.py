"""
Server additions — routes exposed by AGOS Civilization 4.0.

These are appended to `server.py`; they add:
  - POST /civilization/execute   : full auto-routed pipeline (no user spec choice)
  - GET  /civilization/registry  : the curated OSS agents registry
  - POST /civilization/plan      : preview the plan for a prompt (no execution)
  - GET  /civilization/runs/{run_id}         : a DAG run report (R-165)
  - POST /civilization/runs/{run_id}/resume  : resume it, with new approvals (R-165)

The user does NOT choose a specialization; the civilization decides.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from agent_civilization.registry import AGENTS, as_list, total_agents
from agent_civilization.routing import route as auto_route
from agent_civilization.pipeline import dag, execute_plan
from agent_civilization.core import coordination

_STAGE_NAME = r"^[A-Za-z0-9_.-]{1,64}$"
# SEC-08: same bound as TaskRequest.prompt / ChatRequest.message (server.py).
MAX_MESSAGE_CHARS = 20000


class ExecuteRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    data: Optional[Any] = None
    # SEC-06 (R-145): explicit per-request consent to the public (keyless) LLM
    # provider — strict bool, default False (a missing field is refusal).
    allow_public_llm: bool = Field(False, strict=True)
    # R-165 (D-084): stages approved by name. A sensitive stage (code
    # execution, external request, self-modification) runs only when listed;
    # otherwise the run stops it at "awaiting_approval" (resume later).
    approve_stages: List[str] = Field(default_factory=list, max_length=dag.MAX_STAGES)


class ResumeRequest(BaseModel):
    """R-165: POST /civilization/runs/{run_id}/resume."""
    approve_stages: List[str] = Field(default_factory=list, max_length=dag.MAX_STAGES)
    allow_public_llm: bool = Field(False, strict=True)


class CoordinateRequest(BaseModel):
    """R-166 (D-085, amended D-086): POST /civilization/coordinate.

    A fan-out/fan-in coordination round: the message becomes one subtask per
    requested specialization (default: the specializations of the auto-routed
    plan), distributed fairly across the real civilization agents, executed,
    gathered with loss detection, and reduced into a report with metrics.
    Sensitive specializations (dag.SENSITIVE) are NOT executed without an
    explicit approval in ``approve_stages`` — they are reported as
    ``pending_approvals`` (the R-165 gate cannot be bypassed via this route).
    """
    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)
    specializations: Optional[List[str]] = None
    participants_per_specialty: int = Field(1, ge=1, le=50)
    approve_stages: List[str] = Field(default_factory=list, max_length=dag.MAX_STAGES)
    allow_public_llm: bool = Field(False, strict=True)


def record_status(result) -> str:
    """R-170 batch 19 (G-160-3, D-104): task-record status for a DAG report.

    ``completed`` iff the run's aggregate status is exactly ``"completed"``
    (every stage succeeded — dag._aggregate); failed / timed_out /
    budget_exhausted / cancelled / awaiting_approval / missing → ``failed``.
    The record vocabulary {running, completed, failed} is unchanged; the
    precise run state stays in ``result["status"]``."""
    if isinstance(result, dict) and result.get("status") == "completed":
        return "completed"
    return "failed"


def register_civilization_routes(app, civilization, tasks_store, prune_tasks, verify_api_key=None,
                                 require_principal=None, rate_limiter=None, admit=None):
    """Attach civilization routes to an existing FastAPI app.

    When `verify_api_key` is provided (the server's own helper), the routes
    enforce the API key exactly like the /api routes (R-004).

    SEC-02: /civilization/execute stores a task and runs memory for a tenant,
    so it needs `require_principal` (the server's identity check). Without it
    the route refuses every request — it never runs without a tenant.

    SEC-08: ``rate_limiter(request)`` is the server's per-client limiter (the
    one every /api/* route uses) and ``admit(tenant_id)`` its volatile task
    admission (R-164 caps + accept rate). Every route here calls the limiter
    first; execute/coordinate call ``admit`` before writing any record.
    """
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def _limit(request: Request):
        if rate_limiter is not None:
            # Log 299: the server's limiter is async (the shared SQLite store
            # runs off the event loop) — every caller awaits it.
            await rate_limiter(request)

    def _admit(tenant_id: str):
        if admit is not None:
            admit(tenant_id)

    def _finish(task_id: str, record: dict, **fields):
        # SEC-08 (D3): the record may have been evicted while the run was in
        # flight — restore it as finished instead of raising KeyError (500).
        rec = tasks_store.get(task_id)
        if rec is None:
            rec = tasks_store.setdefault(task_id, dict(record))
            prune_tasks()
        rec.update(fields)
        return rec

    def _guard(api_key):
        if verify_api_key is not None:
            return verify_api_key(api_key)
        return None

    @app.get("/civilization/registry")
    async def registry_route(request: Request, api_key: Optional[str] = Security(api_key_header)):
        await _limit(request)
        _guard(api_key)
        return {
            "count": len(AGENTS),
            "total_addressable": total_agents(sum(len(v) for v in civilization.values())),
            "agents": as_list(),
        }

    @app.post("/civilization/plan")
    async def preview_plan(request: Request, req: ExecuteRequest,
                           api_key: Optional[str] = Security(api_key_header)):
        await _limit(request)
        _guard(api_key)
        plan = auto_route(req.message)
        try:
            # R-165: the normalized DAG (depends_on, requires_approval, order, budget)
            return {"plan": dag.normalize_plan(plan)}
        except dag.PlanError as exc:
            raise HTTPException(400, f"invalid plan: {exc}") from None

    @app.post("/civilization/execute")
    async def execute(request: Request, req: ExecuteRequest,
                      api_key: Optional[str] = Security(api_key_header)):
        await _limit(request)
        if require_principal is None:
            raise HTTPException(401, "authentication is not configured for this route")
        principal = require_principal(api_key)
        if not req.message.strip():
            raise HTTPException(400, "message is required")
        _admit(principal.tenant_id)       # SEC-08: before any record is written
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
            result = await execute_plan(plan, civilization, data=req.data,
                                        approvals=set(req.approve_stages),
                                        tenant_id=principal.tenant_id, task_id=task_id)
        except dag.PlanError as exc:
            rec = tasks_store.get(task_id)
            if rec is not None:
                rec.update(status="failed", finished_at=time.time(),
                           result={"error": "invalid plan"})
            raise HTTPException(400, f"invalid plan: {exc}") from None
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
        # R-170 batch 19 (G-160-3, D-104): the record says "completed" only
        # when the DAG run did; any other outcome is "failed" (exact state in
        # result["status"]) — never a forged success in GET /api/tasks.
        _finish(task_id, {"id": task_id, "message": req.message, "plan": plan,
                          "created_at": time.time(), "tenant_id": principal.tenant_id},
                status=record_status(result), result=result, run_id=result["run_id"],
                finished_at=time.time())
        return {"task_id": task_id, **result}

    def _principal(api_key):
        if require_principal is None:
            raise HTTPException(401, "authentication is not configured for this route")
        return require_principal(api_key)

    @app.get("/civilization/runs/{run_id}")
    async def get_run(request: Request, run_id: str, api_key: Optional[str] = Security(api_key_header)):
        await _limit(request)
        principal = _principal(api_key)
        try:
            run = dag.RUNS.get(run_id, principal.tenant_id)
        except dag.RunNotFound:
            raise HTTPException(404, "run not found") from None
        return {"task_id": run.task_id, **dag.report(run)}

    @app.post("/civilization/runs/{run_id}/resume")
    async def resume_run(request: Request, run_id: str, req: ResumeRequest,
                         api_key: Optional[str] = Security(api_key_header)):
        await _limit(request)
        principal = _principal(api_key)
        from agent_civilization.core.llm import reset_public_llm_consent, set_public_llm_consent
        try:
            run = dag.RUNS.get(run_id, principal.tenant_id)
        except dag.RunNotFound:
            raise HTTPException(404, "run not found") from None
        token = set_public_llm_consent(req.allow_public_llm)
        try:
            result = await dag.resume_run(run_id, principal.tenant_id, civilization,
                                          approvals=req.approve_stages)
        except dag.RunNotFound:
            raise HTTPException(404, "run not found") from None
        except dag.RunBusy:
            raise HTTPException(409, "run is executing") from None
        finally:
            reset_public_llm_consent(token)
        rec = tasks_store.get(run.task_id) if run.task_id else None
        if rec is not None and rec.get("tenant_id") == principal.tenant_id:
            rec["result"] = result
            rec["status"] = record_status(result)      # R-170 batch 19 (D-104)
            rec["finished_at"] = time.time()
        return {"task_id": run.task_id, **result}

    @app.post("/civilization/coordinate")
    async def coordinate(request: Request, req: CoordinateRequest,
                         api_key: Optional[str] = Security(api_key_header)):
        """R-166 (D-085, amended D-086): fan-out/fan-in over the real agents.

        The message becomes one subtask per requested specialization (default:
        the specializations of the auto-routed plan), assigned fairly across
        participants, executed by real agents, gathered with loss detection and
        reduced into a report with metrics. A subtask whose participant never
        answers — or answers with ok=False — makes the round incomplete, never
        a hidden success. Sensitive specializations (dag.SENSITIVE) are not
        executed without an explicit approval and are reported as pending.
        """
        await _limit(request)
        principal = _principal(api_key)
        if not req.message.strip():
            raise HTTPException(400, "message is required")
        plan = auto_route(req.message)
        stages = plan.get("stages", []) if isinstance(plan, dict) else plan
        specs = list(dict.fromkeys(req.specializations or [st["specialization"] for st in stages]))
        unknown = [s for s in specs if not civilization.get(s)]
        if unknown:
            raise HTTPException(400, f"unknown specializations: {', '.join(sorted(set(unknown)))}")
        # D-086: the R-165 approval gate cannot be bypassed via this route —
        # sensitive specializations without an explicit approval are reported
        # as pending and never executed.
        pending = [s for s in specs if s in dag.SENSITIVE and s not in req.approve_stages]
        executable = [s for s in specs if s not in pending]
        _admit(principal.tenant_id)       # SEC-08: before any record is written
        task_id = str(uuid.uuid4())
        base_record = {
            "id": task_id,
            "message": req.message,
            "plan": plan,
            "status": "running",
            "created_at": time.time(),
            "tenant_id": principal.tenant_id,
        }
        tasks_store[task_id] = dict(base_record)
        prune_tasks()

        # participants: real agents, capped per specialty; ids are unique
        # because agent names are unique inside a pool. Only EXECUTABLE
        # specializations participate — pending (unapproved sensitive) ones
        # never receive work.
        participants: List[coordination.Participant] = []
        agent_by_pid: Dict[str, Any] = {}
        for spec in executable:
            pool = civilization.get(spec, [])
            for agent in pool[: req.participants_per_specialty]:
                pid = f"{spec}:{agent.name}"
                participants.append(coordination.Participant(participant_id=pid, specialty=spec))
                agent_by_pid[pid] = agent

        subtasks = [
            coordination.Subtask(subtask_id=f"sub-{i}", specialty=spec,
                                 payload={"message": req.message, "specialization": spec})
            for i, spec in enumerate(executable)
        ]

        from agent_civilization.core.llm import reset_public_llm_consent, set_public_llm_consent
        token = set_public_llm_consent(req.allow_public_llm)

        async def execute(msg: coordination.CoordinationMessage) -> coordination.CoordinationMessage:
            agent = agent_by_pid[msg.participant_id]
            try:
                res = await asyncio.wait_for(
                    agent.execute({"goal": req.message, "data": msg.body,
                                   "specialization": msg.body["specialization"]}),
                    timeout=60.0)
                body = res
            except asyncio.TimeoutError:
                body = {"ok": False, "error": "participant timed out"}
            except Exception as exc:  # noqa: BLE001
                body = {"ok": False, "error": f"participant error: {type(exc).__name__}"}
            return coordination.CoordinationMessage(
                message_id=f"result:{msg.subtask_id}:{msg.participant_id}",
                kind="result",
                subtask_id=msg.subtask_id,
                participant_id=msg.participant_id,
                body=body,
                attempt=msg.attempt,
            )

        try:
            if executable:
                report = await coordination.run_coordination(
                    subtasks, participants, execute)
            else:
                # everything requested is pending approval — nothing executes;
                # the round is reported as pending, never as success or as an
                # empty success
                report = coordination.CoordinationReport(
                    ok=False, incomplete_reason="awaiting approval",
                    subtasks=0, participants=0)
        except coordination.CoordinationError as exc:
            _finish(task_id, base_record, status="failed", finished_at=time.time(),
                    result={"error": f"coordination refused: {exc}"})
            raise HTTPException(400, f"coordination refused: {exc}") from None
        finally:
            reset_public_llm_consent(token)

        # D-087: the round is ok only when the engine round succeeded AND
        # nothing is pending approval — a mixed request (executed + pending)
        # is incomplete even when every executed subtask succeeded.
        ok = report.ok and not pending
        incomplete = report.incomplete_reason
        if report.ok and pending:
            incomplete = "awaiting approval"

        rec = _finish(task_id, base_record, status="completed" if ok else "failed",
                      finished_at=time.time())
        rec["result"] = {
            "ok": ok,
            "results": report.results,
            "missing": report.missing,
            "failed": report.failed,
            "pending_approvals": pending,
        }
        metrics = report.metrics()
        metrics["executed"] = len(report.results)
        metrics["ok"] = ok
        metrics["pending_approvals"] = len(pending)
        return {
            "task_id": task_id,
            "ok": ok,
            "incomplete_reason": incomplete,
            "results": report.results,
            "missing": report.missing,
            "failed": report.failed,
            "pending_approvals": pending,
            "metrics": metrics,
        }
