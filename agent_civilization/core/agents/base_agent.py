#!/usr/bin/env python3
"""
Core agent framework for AI civilization system.
Defines the base agent interface and communication protocols.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging
import asyncio
from dataclasses import dataclass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('base_agent')


@dataclass
class Message:
    """Standard message format for inter-agent communication."""
    sender: str
    recipient: str
    content: Dict[str, Any]
    message_id: str
    timestamp: float
    
    def __post_init__(self):
        """Generate unique message ID if not provided."""
        if not hasattr(self, 'message_id') or not self.message_id:
            import uuid
            self.message_id = str(uuid.uuid4())


class CommunicationHub:
    """Central communication hub for agent message routing."""
    
    def __init__(self):
        """Initialize communication hub."""
        self.agents: Dict[str, 'BaseAgent'] = {}
        self.message_queue: List[Message] = []
        self.running = False
        
    def register_agent(self, agent: 'BaseAgent', name: str):
        """Register an agent with the communication hub."""
        self.agents[name] = agent
        logger.info(f"Agent {name} registered with communication hub")
        
    async def send_message(self, message: Message):
        """Send message to target agent or broadcast."""
        message.timestamp = asyncio.get_event_loop().time()
        self.message_queue.append(message)
        logger.debug(f"Message received from {message.sender}: {message.content}")
        
    async def broadcast(self, message: Message):
        """Broadcast message to all registered agents."""
        self.message_queue.append(message)
        logger.debug(f"Broadcast message from {message.sender}: {message.content}")
        
    async def process_messages(self):
        """Process messages from the queue."""
        # Process messages in batches or individually
        while self.message_queue:
            message = self.message_queue.pop(0)
            await self._deliver_message(message)
            
    async def _deliver_message(self, message: Message):
        """Deliver message to appropriate recipient."""
        if message.recipient == 'broadcast':
            # Handle broadcast case
            for agent_name, agent in self.agents.items():
                await self._deliver_to_agent(agent, message)
        elif message.recipient in self.agents:
            # Direct delivery to specific agent
            await self._deliver_to_agent(self.agents[message.recipient], message)
        else:
            logger.warning(f"Message targeted at unregistered agent: {message.recipient}")


class BaseAgent(ABC):
    """Abstract base class for all AI agents in the civilization."""
    
    def __init__(self, 
                 name: str, 
                 communication_hub: CommunicationHub,
                 capacity: int = 100,
                 **kwargs):
        """
        Initialize an agent.
        
        Args:
            name: Unique identifier for the agent
            communication_hub: Shared communication hub instance
            capacity: Maximum workload capacity
            **kwargs: Additional configuration parameters
        """
        self.name = name
        self.communication_hub = communication_hub
        self.capacity = capacity
        self.is_active = False
        self.status = "initialized"
        self.workqueue: List[dict] = []
        self.memory: Dict[str, Any] = {}
        logger.info(f"Agent {name} initialized with capacity {capacity}")
        
    def start(self):
        """Start agent execution."""
        if self.is_active:
            logger.warning(f"Agent {self.name} is already running")
            return
            
        self.is_active = True
        self.status = "running"
        logger.info(f"Agent {self.name} started")
        # Start background processing task
        asyncio.create_task(self._run())
        
    async def _run(self):
        """Main agent execution loop."""
        while self.is_active:
            try:
                # Process work queue items
                if self.workqueue:
                    work_item = self.workqueue.pop(0)
                    await self._execute_work(work_item)
                else:
                    # Process pending messages
                    await self._process_incoming_messages()
                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.001)
            except Exception as e:
                logger.error(f"Error in agent {self.name}: {e}")
                # Implement retry logic or error handling here
                await asyncio.sleep(0.1)
                
    async def _process_incoming_messages(self):
        """Process messages from communication hub."""
        if self.communication_hub.message_queue:
            # Take first message from queue
            message = self.communication_hub.message_queue[0]
            await self._handle_message(message)
            # Remove processed message
            self.communication_hub.message_queue.pop(0)
            
    @abstractmethod
    async def _execute_work(self, work_item: dict):
        """Execute a unit of work assigned to the agent."""
        pass
        
    @abstractmethod
    async def _handle_message(self, message: Message):
        """Process an incoming message."""
        pass
        
    def add_work(self, work_item: dict):
        """Add work to the agent's queue."""
        self.workqueue.append(work_item)
        logger.debug(f"Added work to agent {self.name}: {work_item}")
        
    def update_capacity(self, new_capacity: int):
        """Update agent capacity."""
        self.capacity = new_capacity
        logger.info(f"Agent {self.name} capacity updated to {new_capacity}")
        
    def shutdown(self):
        """Gracefully shut down the agent."""
        self.is_active = False
        logger.info(f"Agent {self.name} shutting down")
        
    def __repr__(self):
        return f"<BaseAgent name={self.name} status={self.status}>"