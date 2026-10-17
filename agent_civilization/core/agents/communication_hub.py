#!/usr/bin/env python3
"""
Communication hub for inter-agent messaging.
"""

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set
import asyncio
import logging
from dataclasses import dataclass

if TYPE_CHECKING:
    from agent_civilization.core.agents.base_agent import BaseAgent, Message

logger = logging.getLogger('communication_hub')


@dataclass
class DeliveryResult:
    """Result of message delivery attempt."""
    success: bool
    recipient: str
    error: Optional[str] = None


class CommunicationHub:
    """Central communication hub for agent message routing."""

    def __init__(self):
        self.agents: Dict[str, 'BaseAgent'] = {}
        self._lock = asyncio.Lock()
        self._subscribers: Dict[str, Set[Callable]] = {}
        self._stats = {
            "messages_sent": 0,
            "messages_delivered": 0,
            "messages_broadcast": 0,
            "failed_deliveries": 0
        }

    async def register_agent(self, agent: 'BaseAgent', name: Optional[str] = None) -> bool:
        """Register an agent with the communication hub."""
        agent_name = name or agent.name
        async with self._lock:
            if agent_name in self.agents:
                logger.warning(f"Agent {agent_name} already registered")
                return False
            self.agents[agent_name] = agent
            logger.info(f"Agent {agent_name} registered successfully")
            return True

    async def unregister_agent(self, agent_name: str) -> bool:
        """Unregister an agent."""
        async with self._lock:
            if agent_name in self.agents:
                del self.agents[agent_name]
                logger.info(f"Agent {agent_name} unregistered")
                return True
            return False

    async def send_message(self, message: 'Message') -> DeliveryResult:
        """Send message to target agent."""
        self._stats["messages_sent"] += 1
        recipient = message.recipient

        if recipient == "broadcast":
            return await self._broadcast_internal(message)

        async with self._lock:
            if recipient not in self.agents:
                self._stats["failed_deliveries"] += 1
                logger.warning(f"Message targeted at unregistered agent: {recipient}")
                return DeliveryResult(success=False, recipient=recipient, error="Agent not registered")

            agent = self.agents[recipient]
            agent.deliver_message(message)
            self._stats["messages_delivered"] += 1
            logger.debug(f"Message delivered from {message.sender} to {recipient}")
            return DeliveryResult(success=True, recipient=recipient)

    async def _broadcast_internal(self, message: 'Message') -> DeliveryResult:
        """Internal broadcast implementation."""
        delivered = 0
        async with self._lock:
            recipients = list(self.agents.keys())

        for recipient_name in recipients:
            async with self._lock:
                agent = self.agents.get(recipient_name)
            if agent:
                try:
                    agent.deliver_message(message)
                    delivered += 1
                except Exception as e:
                    logger.error("Broadcast delivery error to %s: %s", recipient_name, type(e).__name__)  # R-160 (G-159-1)

        self._stats["messages_broadcast"] += 1
        self._stats["messages_delivered"] += delivered
        logger.info(f"Broadcast from {message.sender} delivered to {delivered} agents")
        return DeliveryResult(success=True, recipient="broadcast")

    async def broadcast(self, message: 'Message'):
        """Broadcast message to all registered agents."""
        await self.send_message(message)

    def get_registered_agents(self) -> List[str]:
        """Get list of registered agent names."""
        return list(self.agents.keys())

    def get_stats(self) -> Dict[str, Any]:
        """Get hub statistics."""
        return self._stats.copy()

    def agent_exists(self, name: str) -> bool:
        """Check if agent is registered."""
        return name in self.agents

    async def subscribe(self, event: str, callback: Callable):
        """Subscribe to hub events."""
        if event not in self._subscribers:
            self._subscribers[event] = set()
        self._subscribers[event].add(callback)

    async def unsubscribe(self, event: str, callback: Callable):
        """Unsubscribe from hub events."""
        if event in self._subscribers:
            self._subscribers[event].discard(callback)
