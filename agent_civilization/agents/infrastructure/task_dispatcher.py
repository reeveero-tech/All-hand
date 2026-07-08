#!/usr/bin/env python3
"""Task Dispatcher Agent - distributes work based on agent capabilities."""

import logging
from typing import Dict, List, Any
from agent_civilization.core.agents.base_agent import BaseAgent, Message, CommunicationHub

logger = logging.getLogger('task_dispatcher')


class TaskDispatcherAgent(BaseAgent):
    """Dispatches tasks to agents based on their capabilities and load."""

    def __init__(self, name: str, hub: CommunicationHub, **kwargs):
        super().__init__(name, hub, **kwargs)
        self.agent_capabilities: Dict[str, List[str]] = {}
        self.agent_load: Dict[str, int] = {}

    async def _execute_work(self, work_item: dict):
        task_type = work_item.get("type")
        task_data = work_item.get("data", {})
        
        eligible_agents = self._find_eligible_agents(task_type)
        if not eligible_agents:
            logger.warning(f"No agent found for task: {task_type}")
            return
            
        selected_agent = self._select_least_loaded(eligible_agents)
        logger.info(f"Dispatching {task_type} to {selected_agent}")
        
        task_msg = Message(
            sender=self.name,
            recipient=selected_agent,
            content={"task": task_type, "data": task_data},
            timestamp=0,
            message_id=""
        )
        await self.communication_hub.send_message(task_msg)

    async def _handle_message(self, message: Message):
        if message.content.get("action") == "register":
            agent_name = message.sender
            capabilities = message.content.get("capabilities", [])
            self.agent_capabilities[agent_name] = capabilities
            self.agent_load[agent_name] = 0
            logger.info(f"Registered agent {agent_name} with capabilities {capabilities}")
        elif message.content.get("action") == "load_update":
            agent_name = message.sender
            load = message.content.get("load")
            self.agent_load[agent_name] = load

    def _find_eligible_agents(self, task_type: str) -> List[str]:
        """Find all agents that can handle a given task type."""
        return [name for name, caps in self.agent_capabilities.items() 
                if task_type in caps]

    def _select_least_loaded(self, candidates: List[str]) -> str:
        """Select the agent with the lowest current load."""
        return min(candidates, key=lambda a: self.agent_load.get(a, 0))