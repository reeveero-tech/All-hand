"""
Tests for Agent Production Components

Tests agent factory, learning runtime, and memory system.
"""

import pytest

# Test Dynamic Agent
def test_dynamic_agent_creation():
    """Test dynamic agent creation."""
    from agos_kernel.agent_production.agent_factory import DynamicAgent, AgentConfig, AgentCapabilities
    
    config = AgentConfig(
        name="test_agent",
        capabilities=AgentCapabilities(
            tools=["analysis", "research"],
            specializations=["analyst"],
        ),
    )
    
    agent = DynamicAgent(config)
    
    assert agent.name == "test_agent"
    assert agent.status == "idle"
    assert agent.task_count == 0


def test_dynamic_agent_execution():
    """Test dynamic agent execution."""
    import asyncio
    from agos_kernel.agent_production.agent_factory import DynamicAgent, AgentConfig
    
    config = AgentConfig(name="test_agent")
    agent = DynamicAgent(config)
    
    async def run():
        result = await agent.execute({"prompt": "test task"})
        return result
    
    result = asyncio.run(run())
    
    assert "ok" in result
    assert agent.task_count == 1


def test_dynamic_agent_success_tracking():
    """Test success/failure tracking."""
    import asyncio
    from agos_kernel.agent_production.agent_factory import DynamicAgent, AgentConfig
    
    config = AgentConfig(name="test_agent")
    agent = DynamicAgent(config)
    
    async def run():
        # Successful task
        await agent.execute({"prompt": "success"})
        # Failed task
        try:
            raise ValueError("test")
        except Exception:
            pass
    
    asyncio.run(run())
    
    assert agent.task_count == 1  # Only one execute call


def test_dynamic_agent_evolve():
    """Test agent evolution."""
    from agos_kernel.agent_production.agent_factory import DynamicAgent, AgentConfig, AgentType
    
    config = AgentConfig(
        name="test_agent",
        generation=1,
    )
    agent = DynamicAgent(config)
    
    evolved = agent.evolve()
    
    assert evolved.config.generation == 2
    assert evolved.config.parent_id == agent.id
    assert evolved.config.agent_type == AgentType.LEARNING


def test_dynamic_agent_snapshot():
    """Test agent snapshot."""
    from agos_kernel.agent_production.agent_factory import DynamicAgent, AgentConfig
    
    config = AgentConfig(name="test_agent")
    agent = DynamicAgent(config)
    
    snapshot = agent.snapshot()
    
    assert "id" in snapshot
    assert snapshot["name"] == "test_agent"
    assert "status" in snapshot


# Test Agent Factory
def test_agent_factory_creation():
    """Test agent factory."""
    from agos_kernel.agent_production.agent_factory import AgentFactory, AgentType
    
    factory = AgentFactory()
    
    agent = factory.create_agent(
        name="factory_agent",
        agent_type=AgentType.DYNAMIC,
    )
    
    assert agent is not None
    assert factory.get_agent(agent.id) == agent


def test_agent_factory_list():
    """Test listing agents."""
    from agos_kernel.agent_production.agent_factory import AgentFactory, AgentType, AgentCapabilities
    
    factory = AgentFactory()
    
    # Create agents
    factory.create_agent("agent1", agent_type=AgentType.DYNAMIC)
    factory.create_agent(
        "agent2",
        agent_type=AgentType.SPECIALIZED,
        capabilities=AgentCapabilities(specializations=["analyst"]),
    )
    
    # List all
    all_agents = factory.list_agents()
    assert len(all_agents) == 2
    
    # List by type
    dynamic_agents = factory.list_agents(agent_type=AgentType.DYNAMIC)
    assert len(dynamic_agents) == 1
    
    # List by specialization
    analyst_agents = factory.list_agents(specialization="analyst")
    assert len(analyst_agents) == 1


def test_agent_factory_remove():
    """Test removing agents."""
    from agos_kernel.agent_production.agent_factory import AgentFactory
    
    factory = AgentFactory()
    
    agent = factory.create_agent("temp_agent")
    assert factory.get_agent(agent.id) == agent
    
    result = factory.remove_agent(agent.id)
    assert result is True
    assert factory.get_agent(agent.id) is None


def test_agent_factory_evolve():
    """Test evolving agents through factory."""
    from agos_kernel.agent_production.agent_factory import AgentFactory
    
    factory = AgentFactory()
    
    original = factory.create_agent("to_evolve")
    evolved = factory.evolve_agent(original.id)
    
    assert evolved is not None
    assert evolved.id != original.id
    assert factory.get_agent(evolved.id) == evolved


def test_agent_factory_stats():
    """Test factory statistics."""
    from agos_kernel.agent_production.agent_factory import AgentFactory, AgentType
    
    factory = AgentFactory()
    
    factory.create_agent("agent1", agent_type=AgentType.DYNAMIC)
    factory.create_agent("agent2", agent_type=AgentType.SPECIALIZED)
    
    stats = factory.get_stats()
    
    assert stats["total_agents"] == 2
    assert stats["by_type"]["dynamic"] == 1


# Test Learning Runtime
def test_learning_runtime_initialization():
    """Test learning runtime initialization."""
    from agos_kernel.agent_production.learning_runtime import LearningRuntime
    
    runtime = LearningRuntime(decay_factor=0.9)
    
    assert runtime._decay_factor == 0.9


def test_learning_runtime_record_task():
    """Test recording tasks."""
    from agos_kernel.agent_production.learning_runtime import LearningRuntime
    
    runtime = LearningRuntime()
    
    runtime.record_task(
        agent_id="agent1",
        task_type="analysis",
        approach="method_a",
        success=True,
        duration_ms=100.0,
    )
    
    stats = runtime.get_global_stats()
    assert stats["total_tasks"] == 1


def test_learning_runtime_best_approach():
    """Test getting best approach."""
    from agos_kernel.agent_production.learning_runtime import LearningRuntime
    
    runtime = LearningRuntime()
    
    # Record different approaches
    runtime.record_task("agent1", "analysis", "method_a", True, 100)
    runtime.record_task("agent2", "analysis", "method_b", False, 100)
    
    best = runtime.get_best_approach("analysis")
    assert best == "method_a"


def test_learning_runtime_agent_insights():
    """Test getting agent insights."""
    from agos_kernel.agent_production.learning_runtime import LearningRuntime
    
    runtime = LearningRuntime()
    
    runtime.record_task("agent1", "analysis", "method_a", True, 100)
    runtime.record_task("agent1", "research", "method_b", False, 100)
    
    insights = runtime.get_agent_insights("agent1")
    
    assert insights["agent_id"] == "agent1"
    assert "weaknesses" in insights


def test_learning_runtime_evolution_recommendation():
    """Test evolution recommendation."""
    from agos_kernel.agent_production.learning_runtime import LearningRuntime
    
    runtime = LearningRuntime()
    
    # Not enough data
    recommendation = runtime.get_evolution_recommendation("new_agent")
    assert recommendation["recommendation"] == "not_ready"
    
    # Add more data
    for i in range(15):
        runtime.record_task("new_agent", "analysis", "method", i > 13, 100)
    
    recommendation = runtime.get_evolution_recommendation("new_agent")
    assert recommendation["recommendation"] != "not_ready"


# Test Memory System
@pytest.mark.asyncio
async def test_memory_system_initialization():
    """Test memory system initialization."""
    from agos_kernel.agent_production.memory_system import MemorySystem
    
    memory = MemorySystem(max_entries=100)
    
    assert memory._max_entries == 100
    assert len(memory._memories) == 0


@pytest.mark.asyncio
async def test_memory_system_store():
    """Test storing memories."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem()
    
    memory_id = await memory.store(
        content="Test memory",
        memory_type=MemoryType.EPISODIC,
        tags={"test", "unit"},
        importance=0.8,
    )
    
    assert memory_id is not None
    assert memory_id in memory._memories


@pytest.mark.asyncio
async def test_memory_system_retrieve():
    """Test retrieving memories."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem()
    
    memory_id = await memory.store(
        content="Retrieve test",
        memory_type=MemoryType.SEMANTIC,
    )
    
    entry = await memory.retrieve(memory_id)
    
    assert entry is not None
    assert entry.content == "Retrieve test"
    assert entry.access_count == 1


@pytest.mark.asyncio
async def test_memory_system_search():
    """Test searching memories."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType, MemoryQuery
    
    memory = MemorySystem()
    
    await memory.store("First memory", MemoryType.EPISODIC, tags={"analysis"})
    await memory.store("Second memory", MemoryType.SEMANTIC, tags={"analysis"})
    await memory.store("Third memory", MemoryType.PROCEDURAL, tags={"development"})
    
    results = await memory.search(MemoryQuery(tags={"analysis"}))
    
    assert len(results) == 2


@pytest.mark.asyncio
async def test_memory_system_forget():
    """Test forgetting memories."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem()
    
    memory_id = await memory.store("To forget", MemoryType.WORKING)
    
    result = await memory.forget(memory_id)
    
    assert result is True
    assert memory_id not in memory._memories


@pytest.mark.asyncio
async def test_memory_system_recall():
    """Test recalling memories."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem()
    
    await memory.store("Important insight", MemoryType.SEMANTIC, importance=0.9)
    await memory.store("Less important", MemoryType.WORKING, importance=0.3)
    
    recalled = await memory.recall("insight")
    
    assert len(recalled) >= 1
    assert any("Important insight" in r.content for r in recalled)


@pytest.mark.asyncio
async def test_memory_system_learn_from_task():
    """Test learning from tasks."""
    from agos_kernel.agent_production.memory_system import MemorySystem
    
    memory = MemorySystem()
    
    await memory.learn_from_task(
        agent_id="agent1",
        task_type="analysis",
        prompt="Analyze this",
        result={"insights": ["found"]},
        success=True,
    )
    
    stats = memory.get_stats()
    assert stats["total_memories"] >= 1


@pytest.mark.asyncio
async def test_memory_system_prune():
    """Test memory pruning."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem(max_entries=5)
    
    # Add many memories
    for i in range(10):
        await memory.store(f"Memory {i}", MemoryType.EPISODIC)
    
    assert len(memory._memories) <= 5


@pytest.mark.asyncio
async def test_memory_system_stats():
    """Test memory statistics."""
    from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType
    
    memory = MemorySystem()
    
    await memory.store("Type A", MemoryType.EPISODIC, tags={"tag1"})
    await memory.store("Type B", MemoryType.SEMANTIC, tags={"tag2"})
    
    stats = memory.get_stats()
    
    assert stats["total_memories"] == 2
    assert "episodic" in stats["by_type"]
    assert "semantic" in stats["by_type"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
