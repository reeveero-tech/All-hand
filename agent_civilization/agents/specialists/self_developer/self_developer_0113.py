#!/usr/bin/env python3
"""
SelfDeveloperAgent0113 - Self-improves through analysis
Agent ID: 113
"""

import logging
from typing import Dict, Any
from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub

logger = logging.getLogger('self_developer_0113')

class SelfDeveloperAgent0113(BaseAgent):
    def __init__(self, name: str, hub: CommunicationHub, agent_id: int = 113, **kw):
        super().__init__(name, hub, **kw)
        self.agent_id = agent_id
        self.specialization = "self_developer"
        self.capabilities = ["self_developer", "all"]
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
            result = await self._process_task(work)
            self.stats["tasks"] += 1
            self.performance_metrics["tasks_completed"] += 1
            r = Message(sender=self.name, recipient=work.get("original_sender","dispatcher"),
                       content={"action": "task_complete", "task_id": tid, "result": result,
                               "agent_id": self.agent_id, "spec": self.specialization})
            await self.communication_hub.send_message(r)
        except Exception as e:
            logger.error(f"Agent {self.name} error: {e}")
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

def create(hub: CommunicationHub, aid: int = 113) -> SelfDeveloperAgent0113:
    return SelfDeveloperAgent0113(name="self_developer_"+f"{aid:04d}", hub=hub, agent_id=aid)
