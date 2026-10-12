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


async def _pick(pool):
    idle = [a for a in pool if a.status == "idle"]
    return (idle or pool)[0]


async def execute_plan(plan: Dict[str, Any], civilization: Dict[str, list], data: Optional[Any] = None) -> Dict[str, Any]:
    """Run the plan; return {"stages": [...], "final": str, "elapsed_s": float}."""
    started = time.time()
    context_snippets: List[str] = []
    stage_results: List[Dict[str, Any]] = []

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
            res = await agent.execute({
                "task_id": f"stage-{uuid.uuid4().hex[:8]}",
                "prompt": prompt,
                "data": data,
            })
            elapsed = time.time() - t0
            output_text = ""
            stage_ok = True
            if isinstance(res, dict):
                stage_ok = bool(res.get("ok"))
                r = res.get("result") if "result" in res else res
                if isinstance(r, dict):
                    output_text = r.get("answer") or r.get("tool_output") or r.get("error") or ""
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
