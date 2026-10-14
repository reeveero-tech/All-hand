#!/usr/bin/env python3
"""
Data Worker Agent — an LLM-backed data-processing executor on the live path (R-104).

Before R-104 this class was completely unconnected: no importer anywhere in the
repository (tests included), and its ``_process_data`` masked LLM failure by
returning ``{"status": "processed", "answer": ""}`` when ``get_llm().complete``
reported ``ok: False`` — the same class of dishonesty R-102 removed from
``real_dispatcher``.  R-104 (D-047) repairs rather than deletes or archives:

1. ``_process_data`` is now honest — it propagates the LLM's real failure
   (``ok``/``error``/``model``) instead of a silent empty success.
2. ``process_text`` exposes the same LLM call as a plain async function, so it
   can be wired into ``real_dispatcher._data_processor`` for text-only input
   (where the deterministic numeric aggregation has nothing to do) — the class
   is now reachable from the live dispatch path and exercised by a test.
3. The class keeps its message-passing role (``_handle_message``/``_execute_work``)
   unchanged for any future hub wiring.

Scope limit (stated honestly): the LLM call requires ``OPENROUTER_API_KEY``;
without it, the handler reports the real failure with the exact missing-key
error. The deterministic numeric aggregation stays authoritative for numeric
input (unchanged from R-102).
"""

import logging
from typing import Any, Dict

from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub

logger = logging.getLogger('data_worker')

SYSTEM_PROMPT = (
    "You are a data processing worker agent. Process the input and return results."
)


async def process_text(prompt: str) -> Dict[str, Any]:
    """LLM-backed text processing (no hub, no agent identity) — the live-path entry point."""
    from agent_civilization.core.llm import get_llm
    llm = get_llm()
    result = await llm.complete(SYSTEM_PROMPT, str(prompt))
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "LLM failed",
                "model": result.get("model")}
    return {"ok": True, "answer": result.get("content", ""),
            "model": result.get("model")}


class DataWorkerAgent(BaseAgent):
    """LLM-backed worker agent that processes data tasks via the LLM (R-104: wired into the live path)."""

    def __init__(self, name: str, hub: CommunicationHub,
                 worker_id: int = 0, **kwargs):
        super().__init__(name, hub, **kwargs)
        self.worker_id = worker_id
        self.processed_count = 0
        self.capabilities = ["data_processing", "all"]

    async def _execute_work(self, work_item: Dict[str, Any]):
        task_id = work_item.get("task_id", "unknown")
        data = work_item.get("data", {})

        logger.info(f"Worker {self.name} processing task {task_id}")

        try:
            result = await self._process_data(data)
            # R-104b: only count success when the LLM actually succeeded (ok is
            # absent for legacy dict returns, so treat missing ok as failure).
            succeeded = bool(result.get("ok", False)) if isinstance(result, dict) else False
            if succeeded:
                self.processed_count += 1
                self.performance_metrics["tasks_completed"] += 1
            else:
                self.performance_metrics["tasks_failed"] += 1

            sender = work_item.get("original_sender", "dispatcher")
            result_msg = Message(
                sender=self.name,
                recipient=sender,
                content={
                    "action": "task_complete",
                    "task_id": task_id,
                    "result": result,
                    "worker_id": self.worker_id
                }
            )
            await self.communication_hub.send_message(result_msg)

        except Exception as e:
            logger.error(f"Worker {self.name} error: {e}")
            self.performance_metrics["tasks_failed"] += 1

    async def _process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process data using the LLM; propagate the LLM's real failure (R-104 honesty fix)."""
        prompt = str(data.get("input", ""))
        out = await process_text(prompt)
        return {
            "ok": out.get("ok", False),
            "status": "processed" if out.get("ok") else "failed",
            "answer": out.get("answer", ""),
            "error": out.get("error"),
            "model": out.get("model"),
            "worker": self.name,
        }

    async def _handle_message(self, message: Message):
        content = message.content
        action = content.get("action")

        if action == "status_request":
            status = self.get_status()
            response = Message(
                sender=self.name,
                recipient=message.sender,
                content={"action": "status_response", "status": status}
            )
            await self.communication_hub.send_message(response)

        elif action == "task":
            self.add_work({
                "task_id": content.get("task_id", "unknown"),
                "data": content.get("data", {}),
                "original_sender": message.sender
            })

    def get_status(self) -> Dict[str, Any]:
        base_status = super().get_status()
        return {
            **base_status,
            "worker_id": self.worker_id,
            "processed_count": self.processed_count,
            "capabilities": self.capabilities
        }
