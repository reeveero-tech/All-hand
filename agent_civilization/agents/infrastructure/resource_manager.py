#!/usr/bin/env python3
"""Resource Manager Agent - tracks and allocates computational resources."""

import logging
from typing import Dict, Any
from agent_civilization.core.agents.base_agent import BaseAgent, Message, CommunicationHub

logger = logging.getLogger('resource_manager')


class ResourceManagerAgent(BaseAgent):
    """Manages physical resources (CPU, memory, network) across the system."""

    def __init__(self, name: str, hub: CommunicationHub, total_cpu: int = 1000, 
                 total_memory: int = 16000, **kwargs):
        super().__init__(name, hub, **kwargs)
        self.available_cpu = total_cpu
        self.available_memory = total_memory
        self.allocations: Dict[str, Dict[str, int]] = {}

    async def _execute_work(self, work_item: dict):
        action = work_item.get("action")
        if action == "allocate":
            agent_name = work_item.get("agent")
            cpu_req = work_item.get("cpu", 0)
            mem_req = work_item.get("memory", 0)
            await self._allocate_resources(agent_name, cpu_req, mem_req)
        elif action == "release":
            agent_name = work_item.get("agent")
            await self._release_resources(agent_name)

    async def _handle_message(self, message: Message):
        if message.content.get("action") == "request_allocation":
            agent_name = message.sender
            cpu_req = message.content.get("cpu", 0)
            mem_req = message.content.get("memory", 0)
            await self._allocate_resources(agent_name, cpu_req, mem_req)

    async def _allocate_resources(self, agent_name: str, cpu: int, memory: int):
        """Allocate resources to an agent if available."""
        if cpu <= self.available_cpu and memory <= self.available_memory:
            self.available_cpu -= cpu
            self.available_memory -= memory
            self.allocations[agent_name] = {"cpu": cpu, "memory": memory}
            logger.info(f"Allocated {cpu} CPU, {memory}MB to {agent_name}")
        else:
            logger.warning(f"Insufficient resources for {agent_name}")

    async def _release_resources(self, agent_name: str):
        """Release previously allocated resources."""
        if agent_name in self.allocations:
            alloc = self.allocations.pop(agent_name)
            self.available_cpu += alloc["cpu"]
            self.available_memory += alloc["memory"]
            logger.info(f"Released resources from {agent_name}")

    def get_status(self) -> Dict[str, Any]:
        """Get current resource status."""
        return {
            "available_cpu": self.available_cpu,
            "available_memory": self.available_memory,
            "active_allocations": len(self.allocations)
        }