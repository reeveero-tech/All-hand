#!/usr/bin/env python3
"""
Pipeline Orchestrator — runs the auto-routed plan across specialist agents.

Given a plan produced by `auto_router.route()`, the orchestrator:
  - Picks the least-loaded idle agent for each stage's specialization.
  - Runs the plan as a DAG (R-165, ``pipeline/dag.py``): dependencies,
    typed schema-validated artifacts, per-stage retry/timeout, a plan
    deadline and budget, approvals before sensitive stages, resume.
  - Records real timings, agent identity, and outputs.
  - Returns a full execution report.

Real work happens inside RealSpecialistAgent (LLM + real tools). The
orchestrator is intentionally thin — it is the coordinator, not a simulator.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def _load(agent) -> float:
    st = getattr(agent, "stats", None) or {}
    return float(st.get("tasks_completed", 0)) + float(st.get("tasks_failed", 0))


async def _pick(pool):
    """R-114: least-loaded idle agent (fewest tasks so far), ties broken by pool
    order — so work spreads across all agents of a specialization instead of
    always landing on the first one (R-113: 14 of 1,000 agents did all the
    work). The choice is made synchronously and the agent is marked
    ``working`` immediately, so concurrent plans never pick the same agent."""
    idle = [a for a in pool if a.status == "idle"]
    candidates = idle or pool
    agent = min(candidates, key=_load)
    agent.status = "working"
    return agent


# R-114: structured artifacts produced by one stage and consumed by the next.
# Before, only the first 800 characters of a stage's *answer* were forwarded —
# e.g. code_generator's code never reached test_runner, which then failed with
# "no code to run" (R-113: 257 failures). Artifacts travel under their own key
# (``work["artifact"]``) so they never pollute the caller's ``data``.


def _artifact_of(res: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    r = res.get("result") if isinstance(res, dict) and "result" in res else res
    if not isinstance(r, dict) or not r.get("ok"):
        return None
    out = r.get("tool_output")
    if not isinstance(out, dict):
        return None
    code = out.get("code") or out.get("new_code")
    if isinstance(code, str) and code.strip():
        return {"code": code}
    # R-122: builder returns a manifest of scaffolded files (no single "code"
    # key), so review/test received nothing and failed with "no code to run".
    # Forward the main Python source file (first non-test .py with content).
    files = out.get("files")
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            path, body = str(f.get("path") or ""), f.get("content")
            name = path.rsplit("/", 1)[-1]
            if (path.endswith(".py") and not name.startswith("test_") and name != "__init__.py"
                    and isinstance(body, str) and body.strip()):
                return {"code": body, "source_file": path}
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return {"sql": sql}
    if "mean" in out and "n" in out and "samples" in out:
        return {"analysis": out}
    return None


async def execute_plan(plan: Dict[str, Any], civilization: Dict[str, list], data: Optional[Any] = None,
                       *, approvals: Optional[Iterable[str]] = None, tenant_id: Optional[str] = None,
                       task_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the plan as a DAG (R-165, D-084) and return the full report:
    {"primary", "reason", "stages": [...], "final", "elapsed_s", "run_id",
    "status", "pending_approvals", "budget", "order", "ignored_approvals"}.

    ``approvals=None`` = trusted internal caller (REPL, scripts, self-retest):
    the stage-approval gate is off. HTTP callers pass the approved stage
    names (possibly none) and their tenant; the run is then kept for
    GET /civilization/runs/{run_id} and resume. Raises ``dag.PlanError`` on a
    malformed plan (nothing runs)."""
    from agent_civilization.pipeline import dag

    return await dag.run_plan(plan, civilization, data, approvals=approvals,
                              tenant_id=tenant_id, task_id=task_id)
