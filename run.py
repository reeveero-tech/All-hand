#!/usr/bin/env python3
"""AGOS Agent Civilization direct runner.

The REPL uses the same auto-router and multi-stage orchestrator as the API:
the user gives only a goal; the civilization decides roles and execution order.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from agent_civilization.agents.real import SPECIALIZATIONS, create_civilization
from agent_civilization.core.llm import get_llm
from agent_civilization.pipeline import execute_plan
from agent_civilization.registry import total_agents
from agent_civilization.routing import route as auto_route

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("agos.runner")


async def interactive() -> None:
    civ = create_civilization(agents_per_spec=2)
    llm = get_llm()
    local_count = sum(len(v) for v in civ.values())
    print("\n" + "=" * 70)
    print("  AGOS Agent Civilization - Auto-routed Direct Mode")
    print(f"  LLM: {'configured (' + llm.model + ')' if llm.available else 'not configured; deterministic tools active'}")
    print(f"  Loaded local workers: {local_count} across {len(SPECIALIZATIONS)} specializations")
    print(f"  Addressable civilization scale: {total_agents(local_count)} agents/frameworks")
    print("  Commands: status | plan <goal> | quit")
    print("=" * 70 + "\n")

    while True:
        try:
            user_input = (await asyncio.to_thread(input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            break
        if user_input.lower() == "status":
            completed = sum(a.stats["tasks_completed"] for agents in civ.values() for a in agents)
            failed = sum(a.stats["tasks_failed"] for agents in civ.values() for a in agents)
            print(f"  completed={completed} failed={failed} local_workers={local_count}")
            continue
        show_plan_only = user_input.lower().startswith("plan ")
        goal = user_input[5:].strip() if show_plan_only else user_input
        plan = auto_route(goal)
        print("  plan>", " -> ".join(f"{s['name']}:{s['specialization']}" for s in plan["stages"]))
        if show_plan_only:
            continue
        result = await execute_plan(plan, civ)
        print(f"  final> {result.get('final') or 'completed with no final text'}\n")

    print("\nGoodbye!")


async def main() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, lambda: None)
        except NotImplementedError:
            continue
    await interactive()


if __name__ == "__main__":
    asyncio.run(main())
