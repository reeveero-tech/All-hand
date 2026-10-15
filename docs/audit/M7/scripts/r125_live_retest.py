#!/usr/bin/env python3
"""R-125 live evidence: a real failure is remembered, the cause is removed, and
self_developer's cycle retests it and retires it — all through the production
path (route() -> execute_plan, live keyless LLM).
1. LLM disabled (AGOS_LLM_KEYLESS=0, no key): "draft a message to the team about the outage" fails.
2. self_developer cycle while the LLM is still off: the failure is retested, still fails, attributed, kept.
3. LLM re-enabled: self_developer cycle again: retested, now succeeds, failure memories retired.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
os.environ["AGOS_MEMORY"] = "1"
os.environ.pop("AGOS_MEMORY_FILE", None)

from agent_civilization.agents.real import create_civilization  # noqa: E402
from agent_civilization.core import experience  # noqa: E402
from agent_civilization.pipeline.orchestrator import execute_plan  # noqa: E402
from agent_civilization.routing.auto_router import route  # noqa: E402

GOAL = "draft a message to the team about the outage"


def failures():
    return sum(1 for e in experience.memory()._memories.values()
               if e.metadata.get("goal") == GOAL and not e.metadata.get("ok"))


async def cycle(civ, label):
    res = await execute_plan(route("self improve: inspect your own specializations and evolve"), civ)
    ex = next(s for s in res["stages"] if s["name"] == "execute")
    print(f"[{label}] self_developer ok={ex['ok']}: {ex['output'][:400]}")
    print(f"[{label}] remembered failures for the goal: {failures()}")


async def main():
    civ = create_civilization(1)
    os.environ["AGOS_LLM_KEYLESS"] = "0"
    os.environ.pop("OPENROUTER_API_KEY", None)
    res = await execute_plan(route(GOAL), civ)
    bad = next((s for s in res["stages"] if not s["ok"]), None)
    print(f"[1 llm off] request failed={bad is not None}: {bad and bad['output'][:160]}")
    print(f"[1 llm off] remembered failures for the goal: {failures()}")
    await cycle(civ, "2 llm off")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    await cycle(civ, "3 llm on")


asyncio.run(main())
