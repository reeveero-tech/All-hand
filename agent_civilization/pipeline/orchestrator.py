#!/usr/bin/env python3
"""
Pipeline Orchestrator — runs the auto-routed plan across specialist agents.

Given a plan produced by `auto_router.route()`, the orchestrator:
  - Picks the least-loaded idle agent for each stage's specialization.
  - Feeds each stage the original goal + the previous stage's output.
  - Records real timings, agent identity, and outputs.
  - Returns a full execution report.

Real work happens inside RealSpecialistAgent (LLM + real tools). The
orchestrator is intentionally thin — it is the coordinator, not a simulator.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


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
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return {"sql": sql}
    if "mean" in out and "n" in out and "samples" in out:
        return {"analysis": out}
    return None


async def execute_plan(plan: Dict[str, Any], civilization: Dict[str, list], data: Optional[Any] = None) -> Dict[str, Any]:
    """Run the plan; return {"stages": [...], "final": str, "elapsed_s": float}."""
    started = time.time()
    context_snippets: List[str] = []
    stage_results: List[Dict[str, Any]] = []
    goal = plan.get("goal") or next(
        (st.get("prompt") for st in plan["stages"] if st.get("name") == "execute"), "")
    artifact: Optional[Dict[str, Any]] = None
    plan_view = [{"name": st.get("name"), "specialization": st.get("specialization"),
                  "reason": st.get("reason")} for st in plan["stages"]]

    for stage in plan["stages"]:
        spec = stage["specialization"]
        pool = civilization.get(spec)
        if not pool:
            stage_results.append({
                **stage,
                "agent": None,
                "ok": False,
                "output": f"لا يوجد وكيل متاح لتخصص {spec}",
                "elapsed_s": 0.0,
            })
            continue

        agent = await _pick(pool)

        # Feed the agent the current stage's prompt + prior context.
        prompt = stage["prompt"]
        if context_snippets:
            prompt = (
                prompt
                + "\n\n=== Previous stages summary ===\n"
                + "\n---\n".join(context_snippets[-3:])
            )

        t0 = time.time()
        try:
            # R-114: the stage also sees the user's goal, the plan it belongs
            # to, and the artifact (e.g. code) produced by an earlier stage.
            res = await agent.execute({
                "task_id": f"stage-{uuid.uuid4().hex[:8]}",
                "prompt": prompt,
                "goal": goal,
                "stage": stage["name"],
                "plan_stages": plan_view if stage["name"] == "plan" else None,
                "context": list(context_snippets[-3:]),
                "artifact": artifact,
                "data": data,
            })
            produced = _artifact_of(res)
            if produced:
                artifact = produced
            elapsed = time.time() - t0
            output_text = ""
            stage_ok = True
            if isinstance(res, dict):
                stage_ok = bool(res.get("ok"))
                r = res.get("result") if "result" in res else res
                if isinstance(r, dict):
                    # R-116: a failed stage reports its error, not its partial tool_output
                    output_text = ((r.get("error") or r.get("answer") or r.get("tool_output")) if not stage_ok
                                   else (r.get("answer") or r.get("tool_output") or r.get("error"))) or ""
                    if not isinstance(output_text, str):
                        output_text = str(output_text)
            output_text = (output_text or "").strip()

            stage_results.append({
                **stage,
                "agent": agent.name,
                "ok": stage_ok,
                "output": output_text,
                "elapsed_s": round(elapsed, 3),
            })
            if output_text:
                context_snippets.append(f"[{stage['name']}@{agent.name}] {output_text[:800]}")
        except Exception as e:  # noqa: BLE001
            stage_results.append({
                **stage,
                "agent": agent.name,
                "ok": False,
                "output": f"stage error: {e}",
                "elapsed_s": round(time.time() - t0, 3),
            })

    final = ""
    for s in reversed(stage_results):
        if s.get("ok") and s.get("output"):
            final = s["output"]
            break

    return {
        "primary": plan["primary"],
        "reason": plan["reason"],
        "stages": stage_results,
        "final": final,
        "elapsed_s": round(time.time() - started, 3),
    }
