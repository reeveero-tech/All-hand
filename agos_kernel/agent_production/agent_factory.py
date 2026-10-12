"""
Dynamic Agent Factory

Allows the civilization to "birth" new agents dynamically based on demand.
Features:
- Agent registration with Kernel
- Capability discovery
- Self-configuration
- Agent lifecycle management
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .learning_runtime import LearningRuntime
from .memory_system import MemorySystem

logger = logging.getLogger("agos.agent_production")


class AgentType(str, Enum):
    """Types of agents."""
    STATIC = "static"      # Pre-defined agents
    DYNAMIC = "dynamic"    # Dynamically created
    SPECIALIZED = "specialized"  # Specialized for a task
    LEARNING = "learning"  # Can learn from experience


@dataclass
class AgentCapabilities:
    """Defines what an agent can do."""
    tools: List[str] = field(default_factory=list)
    specializations: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    max_concurrent_tasks: int = 1
    supports_streaming: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tools": self.tools,
            "specializations": self.specializations,
            "languages": self.languages,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "supports_streaming": self.supports_streaming,
        }


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    agent_type: AgentType = AgentType.DYNAMIC
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None  # For agent evolution
    generation: int = 1  # Agent generation
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "agent_type": self.agent_type.value,
            "capabilities": self.capabilities.to_dict(),
            "metadata": self.metadata,
            "parent_id": self.parent_id,
            "generation": self.generation,
        }


class DynamicAgent:
    """
    A dynamically created agent.
    
    Agents can be created at runtime to handle specific tasks
    or to evolve based on experience.
    """
    
    _id_counter = 0
    
    def __init__(self, config: AgentConfig,
                 learning_runtime: Optional["LearningRuntime"] = None,
                 memory_system: Optional["MemorySystem"] = None):
        """
        Initialize a dynamic agent.
        
        Args:
            config: Agent configuration
            learning_runtime: Optional LearningRuntime to record task outcomes (R-054)
            memory_system: Optional MemorySystem to consult before execution (R-054)
        """
        self.id = f"agent_{DynamicAgent._id_counter:06d}"
        DynamicAgent._id_counter += 1
        
        self.config = config
        self.created_at = datetime.utcnow()
        self.last_active = self.created_at
        self.task_count = 0
        self.success_count = 0
        self.failure_count = 0
        
        # Runtime state
        self._status = "idle"
        self._current_task = None
        self._experience: List[Dict[str, Any]] = []
        
        # R-054: الذاكرة والتعلم
        self._learning_runtime = learning_runtime
        self._memory_system = memory_system
        
        logger.info(f"Dynamic agent created: {self.id} ({config.name})")
    
    @property
    def name(self) -> str:
        """Get agent name."""
        return self.config.name
    
    @property
    def status(self) -> str:
        """Get agent status."""
        return self._status
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total
    
    @property
    def experience(self) -> List[Dict[str, Any]]:
        """Get accumulated experience."""
        return self._experience
    
    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task.
        
        Args:
            task: Task data with 'prompt' and optional 'data'
            
        Returns:
            Task result
        """
        self._status = "working"
        self._current_task = task
        self.last_active = datetime.utcnow()
        self.task_count += 1
        
        try:
            # R-054: استرجاع الذاكرة قبل التنفيذ (يفشل بصمت إن لم تُهيّأ)
            context = await self._recall_context(task)
            if context:
                task = {**task, "context": context}
            
            # R-054: اختيار النهج من التعلم قبل التنفيذ (يفشل بصمت إن لم يُهيّأ)
            approach = self._select_approach(task)
            if approach:
                task = {**task, "approach": approach}
            
            # Execute task based on capabilities
            result = await self._execute_task(task)
            
            self.success_count += 1
            self._status = "idle"
            
            # Record experience
            self._record_experience(task, result, success=True)
            
            return {"ok": True, "result": result}
            
        except Exception as e:
            self.failure_count += 1
            self._status = "idle"
            
            # Record failure experience
            self._record_experience(task, {"error": str(e)}, success=False)
            
            return {"ok": False, "error": str(e)}
        
        finally:
            self._current_task = None
    
    async def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute task based on agent capabilities.
        
        Override this method for custom agent behavior.
        """
        approach = task.get("approach", "default")
        prompt = task.get("prompt", "")
        
        # R-054: النهج يغيّر شكل النتيجة فعلياً (behavioral consumer)
        if approach == "detailed":
            memories = (task.get("context") or {}).get("memories", [])
            memory_text = "; ".join(m.get("content", "") for m in memories) if memories else "no prior memories"
            return {
                "message": f"Task executed by {self.name} (approach={approach})",
                "task": prompt,
                "memory_used": memory_text,
                "detail": "full",
            }
        
        if approach == "concise":
            return {
                "message": f"done: {self.name}",
                "task": prompt,
                "detail": "brief",
            }
        
        # Default implementation - can be overridden
        return {
            "message": f"Task executed by {self.name}",
            "task": prompt,
        }
    
    def _record_experience(
        self,
        task: Dict[str, Any],
        result: Dict[str, Any],
        success: bool,
    ) -> None:
        """Record task experience for learning."""
        self._experience.append({
            "timestamp": datetime.utcnow().isoformat(),
            "task_type": task.get("type", "unknown"),
            "success": success,
            "result_summary": self._summarize_result(result),
        })
        
        # Keep only last 100 experiences
        if len(self._experience) > 100:
            self._experience = self._experience[-100:]
        
        # R-054: تسجيل النتيجة إلى learning_runtime (إن مُهيّأ)
        # المسار الموثّق: _execute_task → _record_experience → learning_runtime
        if self._learning_runtime is not None and self._current_task is not None:
            try:
                self._learning_runtime.record_task(
                    agent_id=self.id,
                    task_type=task.get("type", "unknown"),
                    approach=task.get("approach", "default"),
                    success=success,
                    duration_ms=(datetime.utcnow() - self.last_active).total_seconds() * 1000.0,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"learning_runtime record_task failed for {self.name}: {e}")
    
    async def _recall_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """R-054: استرجاع الذكريات ذات الصلة بمهمة (مِن memory_system إن مُهيّأ)."""
        if not self._memory_system:
            return {}
        prompt = task.get("prompt", "") or ""
        if not prompt:
            return {}
        try:
            memories = await self._memory_system.recall(context=prompt, limit=5)
            if not memories:
                return {}
            return {
                "memories": [
                    {
                        "memory_id": m.memory_id,
                        "content": m.content,
                        "importance": m.importance,
                    }
                    for m in memories
                ],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"recall_context failed for {self.name}: {e}")
            return {}
    
    def _select_approach(self, task: Dict[str, Any]) -> Optional[str]:
        """R-054: اختيار النهج المتعلم (من learning_runtime إن مُهيّأ)."""
        if not self._learning_runtime:
            return None
        task_type = task.get("type", "unknown")
        try:
            return self._learning_runtime.get_best_approach(task_type)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"select_approach failed for {self.name}: {e}")
            return None
    
    def _summarize_result(self, result: Dict[str, Any]) -> str:
        """Create a summary of the result for experience storage."""
        if result.get("ok"):
            return "success"
        return f"error: {result.get('error', 'unknown')}"
    
    def snapshot(self) -> Dict[str, Any]:
        """Get agent state snapshot."""
        return {
            "id": self.id,
            "name": self.name,
            "agent_type": self.config.agent_type.value,
            "status": self._status,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "task_count": self.task_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_rate, 3),
            "capabilities": self.config.capabilities.to_dict(),
            "generation": self.config.generation,
        }
    
    def evolve(self) -> "DynamicAgent":
        """
        Create an evolved version of this agent.
        
        The new agent inherits this agent's capabilities and experience.
        """
        evolved_config = AgentConfig(
            name=f"{self.config.name}_gen{self.config.generation + 1}",
            agent_type=AgentType.LEARNING,
            capabilities=self.config.capabilities,
            metadata={
                **self.config.metadata,
                "evolved_from": self.id,
            },
            parent_id=self.id,
            generation=self.config.generation + 1,
        )
        
        evolved = DynamicAgent(evolved_config,
                               learning_runtime=self._learning_runtime,
                               memory_system=self._memory_system)
        evolved._experience = self._experience.copy()
        
        logger.info(f"Agent evolved: {self.id} -> {evolved.id}")
        return evolved


class AgentFactory:
    """
    Factory for creating and managing dynamic agents.
    
    Responsible for:
    - Agent creation
    - Agent registration
    - Capability matching
    - Agent lifecycle management
    """
    
    def __init__(self):
        """Initialize the agent factory."""
        self._agents: Dict[str, DynamicAgent] = {}
        self._agent_pool: Dict[str, List[DynamicAgent]] = {}  # By specialization
        self._registry_callback: Optional[Callable] = None
        
        logger.info("AgentFactory initialized")
    
    def register_registry_callback(self, callback: Callable[[DynamicAgent], None]) -> None:
        """
        Register a callback to be called when new agents are created.
        
        Args:
            callback: Function that receives the new agent
        """
        self._registry_callback = callback
    
    def create_agent(
        self,
        name: str,
        agent_type: AgentType = AgentType.DYNAMIC,
        capabilities: Optional[AgentCapabilities] = None,
        metadata: Optional[Dict[str, Any]] = None,
        learning_runtime: Optional["LearningRuntime"] = None,
        memory_system: Optional["MemorySystem"] = None,
    ) -> DynamicAgent:
        """
        Create a new dynamic agent.
        
        Args:
            name: Agent name
            agent_type: Type of agent
            capabilities: Agent capabilities
            metadata: Additional metadata
            learning_runtime: Optional LearningRuntime (R-054)
            memory_system: Optional MemorySystem (R-054)
            
        Returns:
            New DynamicAgent instance
        """
        config = AgentConfig(
            name=name,
            agent_type=agent_type,
            capabilities=capabilities or AgentCapabilities(),
            metadata=metadata or {},
        )
        
        agent = DynamicAgent(config, learning_runtime=learning_runtime, memory_system=memory_system)
        
        # Register agent
        self._agents[agent.id] = agent
        
        # Add to pool by specialization
        for spec in (capabilities or AgentCapabilities()).specializations:
            if spec not in self._agent_pool:
                self._agent_pool[spec] = []
            self._agent_pool[spec].append(agent)
        
        # Notify registry callback
        if self._registry_callback:
            self._registry_callback(agent)
        
        logger.info(f"Agent registered: {agent.id}")
        return agent
    
    def get_agent(self, agent_id: str) -> Optional[DynamicAgent]:
        """Get agent by ID."""
        return self._agents.get(agent_id)
    
    def list_agents(
        self,
        agent_type: Optional[AgentType] = None,
        specialization: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[DynamicAgent]:
        """
        List agents with optional filters.
        
        Args:
            agent_type: Filter by agent type
            specialization: Filter by specialization
            status: Filter by status
            
        Returns:
            List of matching agents
        """
        agents = list(self._agents.values())
        
        if agent_type:
            agents = [a for a in agents if a.config.agent_type == agent_type]
        
        if specialization:
            agents = [
                a for a in agents
                if specialization in a.config.capabilities.specializations
            ]
        
        if status:
            agents = [a for a in agents if a.status == status]
        
        return agents
    
    def evolve_agent(self, agent_id: str) -> Optional[DynamicAgent]:
        """
        Evolve an existing agent.
        
        Args:
            agent_id: ID of agent to evolve
            
        Returns:
            New evolved agent or None if not found
        """
        original = self._agents.get(agent_id)
        if not original:
            return None
        
        evolved = original.evolve()
        
        # Register evolved agent
        self._agents[evolved.id] = evolved
        
        return evolved
    
    def remove_agent(self, agent_id: str) -> bool:
        """
        Remove an agent from the factory.
        
        Args:
            agent_id: ID of agent to remove
            
        Returns:
            True if removed, False if not found
        """
        if agent_id not in self._agents:
            return False
        
        agent = self._agents[agent_id]
        
        # Remove from pools
        for spec in agent.config.capabilities.specializations:
            if spec in self._agent_pool:
                self._agent_pool[spec] = [
                    a for a in self._agent_pool[spec]
                    if a.id != agent_id
                ]
        
        # Remove from agents
        del self._agents[agent_id]
        
        logger.info(f"Agent removed: {agent_id}")
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get factory statistics."""
        return {
            "total_agents": len(self._agents),
            "by_type": {
                agent_type.value: len([
                    a for a in self._agents.values()
                    if a.config.agent_type == agent_type
                ])
                for agent_type in AgentType
            },
            "by_status": {
                status: len([a for a in self._agents.values() if a.status == status])
                for status in ["idle", "working", "busy"]
            },
            "pools": {
                spec: len(agents)
                for spec, agents in self._agent_pool.items()
            },
        }


# Global factory instance
_factory: Optional[AgentFactory] = None


def get_agent_factory() -> AgentFactory:
    """Get or create global agent factory."""
    global _factory
    if _factory is None:
        _factory = AgentFactory()
    return _factory
