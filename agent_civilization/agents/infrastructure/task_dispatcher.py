#!/usr/bin/env python3
"""
Task Dispatcher Agent - distributes work based on agent capabilities.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub

logger = logging.getLogger('task_dispatcher')


class TaskDispatcherAgent(BaseAgent):
    """Dispatches tasks to agents based on their capabilities and load."""

    def __init__(self, name: str, hub: CommunicationHub, **kwargs):
        super().__init__(name, hub, **kwargs)
        self.agent_capabilities: Dict[str, List[str]] = {}
        self.agent_load: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def _handle_message(self, message: Message):
        content = message.content
        action = content.get("action")

        async with self._lock:
            if action == "register":
                agent_name = message.sender
                capabilities = content.get("capabilities", [])
                self.agent_capabilities[agent_name] = capabilities
                self.agent_load[agent_name] = 0
                logger.info(f"Registered {agent_name} with capabilities: {capabilities}")

            elif action == "load_update":
                agent_name = message.sender
                load = content.get("load", 0)
                self.agent_load[agent_name] = load

            elif action == "task_complete":
                agent_name = message.sender
                if agent_name in self.agent_load:
                    self.agent_load[agent_name] = max(0, self.agent_load[agent_name] - 1)

            elif action == "dispatch_task":
                task_type = content.get("task_type")
                task_id = content.get("task_id", "unknown")
                data = content.get("data", {})

                selected = self._select_least_loaded(task_type)
                if selected:
                    self.agent_load[selected] = self.agent_load.get(selected, 0) + 1
                    task_msg = Message(
                        sender=self.name,
                        recipient=selected,
                        content={
                            "task_id": task_id,
                            "type": task_type,
                            "data": data
                        }
                    )
                    await self.communication_hub.send_message(task_msg)
                    logger.info(f"Dispatched {task_id} to {selected}")
                else:
                    logger.warning(f"No agent for task type: {task_type}")

    def _find_eligible_agents(self, task_type: str) -> List[str]:
        candidates = []
        for name, caps in self.agent_capabilities.items():
            if task_type in caps or "all" in caps:
                candidates.append(name)
        return candidates

    def _select_least_loaded(self, task_type: str) -> Optional[str]:
        candidates = self._find_eligible_agents(task_type)
        if not candidates:
            return None
        return min(candidates, key=lambda a: self.agent_load.get(a, 0))
