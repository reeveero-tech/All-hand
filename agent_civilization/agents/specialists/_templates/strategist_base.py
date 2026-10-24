#!/usr/bin/env python3
"""
strategist base template - Plans strategies
Agent ID: set per generated class
"""

import logging
from typing import Dict, Any
from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub

logger = logging.getLogger('strategist_base')

class StrategistAgentBaseAgent(BaseAgent):
    def __init__(self, name: str, hub: CommunicationHub, agent_id: int = -1, **kw):
        super().__init__(name, hub, **kw)
        self.agent_id = agent_id
        self.specialization = "strategist"
        self.capabilities = ["strategist", "all"]
        self.stats = {"tasks": 0, "errors": 0, "msgs": 0}
        logger.info(f"Agent {name} started")

    async def _process_task(self, work):
        from agent_civilization.core.agents.real_dispatcher import dispatch
        payload = work if isinstance(work, dict) else {"prompt": str(work)}
        result = await dispatch(self.specialization, payload)
        if result is None:
            return {"ok": False, "error": f"no real dispatcher for {self.specialization}"}
        return result
    async def _execute_work(self, work: Dict[str, Any]):
        try:
            tid = work.get("task_id", "unknown")
            logger.info(f"Agent {self.name} processing {tid}")
            # R-170 (batch 7): requirements captured before the handler runs;
            # the result passes the completion gate (AGOS_OUTPUT_VALIDATORS)
            # and is counted by its verdict — a failed result was counted as
            # tasks_completed before.
            from agent_civilization.core.output_validators import (
                apply_output_verification, requirements_for)
            requirements = requirements_for(self.specialization, work)
            result = await self._process_task(work)
            ok = bool(isinstance(result, dict) and result.get("ok"))
            ok, result = apply_output_verification(requirements, ok, result)
            self.stats["tasks"] += 1
            if ok:
                self.performance_metrics["tasks_completed"] += 1
            else:
                self.performance_metrics["tasks_failed"] += 1
            r = Message(sender=self.name, recipient=work.get("original_sender","dispatcher"),
                       content={"action": "task_complete", "task_id": tid, "result": result,
                               "agent_id": self.agent_id, "spec": self.specialization})
            await self.communication_hub.send_message(r)
        except Exception as e:
            # R-160 (G-159-1): exception TYPE only — the raw text is untrusted.
            logger.error("Agent %s error: %s", self.name, type(e).__name__)
            self.stats["errors"] += 1
            self.performance_metrics["tasks_failed"] += 1
    async def _handle_message(self, msg: Message):
        self.stats["msgs"] += 1
        c = msg.content
        if c.get("action") == "status_request":
            s = Message(sender=self.name, recipient=msg.sender, content={"action":"status","status":self.get_status()})
            await self.communication_hub.send_message(s)
        elif c.get("action") == "task":
            self.add_work({"task_id": c.get("task_id","?"), "data": c.get("data",{}), "original_sender": msg.sender})
    def get_status(self) -> Dict[str, Any]:
        b = super().get_status()
        return {**b, "agent_id": self.agent_id, "specialization": self.specialization, "stats": self.stats.copy()}
