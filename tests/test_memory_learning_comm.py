"""R-054: الذاكرة والتعلم والتواصل بين الوكلاء — اختبارات سلوكية.

القبول: "اختبارات سلوكية تثبت أن الذاكرة والتعلم يؤثران على نتيجة مهمة
لاحقة (وليس مجرد استيراد)". كل اختبار هنا تمييزي: يُظهر النتيجة الأساسية
بلا ذاكرة/تعلم، ثم نتيجة مختلفة بسبب الذاكرة/التعلم المستخدم فعلاً.
"""
import asyncio

import pytest

from agos_kernel.agent_production import AgentFactory, LearningRuntime, MemorySystem
from agos_kernel.agent_production.memory_system import MemoryType


async def _make_agent(learning=True, memory=True):
    lr = LearningRuntime() if learning else None
    ms = MemorySystem() if memory else None
    return AgentFactory().create_agent("test_agent", learning_runtime=lr, memory_system=ms), lr, ms


@pytest.mark.asyncio
async def test_memory_changes_task_result():
    """الذاكرة تُغيّر نتيجة مهمة لاحقة فعلياً — لا مجرد استيراد."""
    agent, _, ms = await _make_agent()
    prompt = "AGOS platform overview"

    # 1) baseline: no memory stored
    r1 = await agent.execute({"prompt": prompt, "type": "analysis", "approach": "detailed"})
    assert r1["ok"]
    assert r1["result"]["memory_used"] == "no prior memories"

    # 2) store a memory whose content contains the prompt substring
    await ms.store(
        content="AGOS platform overview: 1000 specialists civilization",
        memory_type=MemoryType.SEMANTIC,
        importance=0.9,
    )

    # 3) same task — memory recalled and USED in result
    r2 = await agent.execute({"prompt": prompt, "type": "analysis", "approach": "detailed"})
    assert r2["result"]["memory_used"] == "AGOS platform overview: 1000 specialists civilization"
    assert r2["result"] != r1["result"]


@pytest.mark.asyncio
async def test_memory_disabled_does_not_change_result():
    """تعطيل الذاكرة يمنع التغيير — الاختبار تمييزي."""
    agent, lr, ms = await _make_agent()
    await ms.store(content="AGOS overview", memory_type=MemoryType.SEMANTIC, importance=0.9)
    # agent WITHOUT memory system
    agent_no_mem = AgentFactory().create_agent("no_mem_agent")
    r = await agent_no_mem.execute({"prompt": "AGOS overview", "type": "analysis", "approach": "detailed"})
    assert r["result"]["memory_used"] == "no prior memories"


@pytest.mark.asyncio
async def test_learning_changes_task_result():
    """التعلم يغيّر النهج المستخدم فعلاً ونتيجة المهمة — لا مجرد تسجيل."""
    agent, lr, _ = await _make_agent()
    task = {"prompt": "AGOS overview", "type": "analysis"}

    # 1) baseline: no learning data → default approach → default result shape
    r1 = await agent.execute(task)
    assert r1["result"]["message"] == "Task executed by test_agent"

    # 2) seed learning: default partially fails, concise succeeds → concise wins
    lr.record_task("test_agent", "analysis", "default", success=False, duration_ms=100)
    lr.record_task("test_agent", "analysis", "concise", success=True, duration_ms=10)
    assert lr.get_best_approach("analysis") == "concise"

    # 3) same task — learned approach consumed, result shape changed
    r2 = await agent.execute(task)
    assert r2["result"]["message"] == "done: test_agent"
    assert r2["result"]["detail"] == "brief"
    assert r2["result"] != r1["result"]


@pytest.mark.asyncio
async def test_learning_disabled_does_not_change_result():
    """تعطيل التعلم يمنع التغيير — الاختبار تمييزي."""
    # learning runtime exists but empty → get_best_approach returns None → default
    agent, lr, _ = await _make_agent()
    task = {"prompt": "AGOS", "type": "analysis"}
    r = await agent.execute(task)
    assert r["result"]["message"] == "Task executed by test_agent"


@pytest.mark.asyncio
async def test_agent_records_experience_to_learning_runtime():
    """الوكيل يسجل تنفيذه تلقائياً — المسار الموثّق execute → _record_experience → learning_runtime."""
    agent, lr, _ = await _make_agent()
    await agent.execute({"prompt": "AGOS", "type": "analysis", "approach": "detailed"})
    # After execution, learning_runtime has data for this agent
    stats = lr.get_global_stats()
    assert stats["total_tasks"] == 1
    assert lr.get_best_approach("analysis") == "detailed"


@pytest.mark.asyncio
async def test_agent_evolve_inherits_memory_and_learning():
    """الوكيل المتطور يرث نفس learning_runtime وmemory_system (R-054)."""
    agent, lr, ms = await _make_agent()
    evolved = agent.evolve()
    assert evolved._learning_runtime is lr
    assert evolved._memory_system is ms
    assert evolved.config.generation == agent.config.generation + 1
    assert evolved._experience == agent._experience


# ---------------------------------------------------------------
# التواصل: CommunicationHub — سلوك التسليم الفعلي (R-054)
# ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_hub_delivers_message_to_specific_recipient():
    """رسالة تصل للمستلم المحدد دون غيره، والمحتوى يُحفظ في memory الخاص به."""
    from agent_civilization.core.agents.base_agent import BaseAgent, Message
    from agent_civilization.core.agents.communication_hub import CommunicationHub

    hub = CommunicationHub()
    alice = BaseAgent("alice", hub)
    bob = BaseAgent("bob", hub)
    # start both so deliver_message puts into inbox and _run processes
    alice.start()
    bob.start()
    await hub.register_agent(alice, "alice")
    await hub.register_agent(bob, "bob")
    try:
        msg = Message(sender="alice", recipient="bob", content={"action": "note", "text": "hello bob"})
        result = await hub.send_message(msg)
        assert result.success
        assert result.recipient == "bob"
        # wait for bob to process
        for _ in range(50):
            await asyncio.sleep(0.02)
            if bob.memory.get("messages"):
                break
        msgs = bob.memory.get("messages", [])
        assert msgs, "الرسالة لم تصل bob"
        assert msgs[0]["content"]["text"] == "hello bob"
        assert msgs[0]["sender"] == "alice"
        # alice did NOT receive it
        assert not alice.memory.get("messages")
    finally:
        await alice.shutdown_async()
        await bob.shutdown_async()


@pytest.mark.asyncio
async def test_hub_broadcast_reaches_all_registered_agents():
    """البث يصل لكل المسجلين، والمحتوى يُحفظ في inbox كل منهم."""
    from agent_civilization.core.agents.base_agent import BaseAgent, Message
    from agent_civilization.core.agents.communication_hub import CommunicationHub

    hub = CommunicationHub()
    agents = []
    for name in ("alice", "bob", "carol"):
        a = BaseAgent(name, hub)
        a.start()
        agents.append(a)
        await hub.register_agent(a, name)
    try:
        msg = Message(sender="alice", recipient="broadcast", content={"action": "note", "text": "all hands"})
        result = await hub.send_message(msg)
        assert result.success
        assert result.recipient == "broadcast"
        # wait for all to process
        for _ in range(50):
            await asyncio.sleep(0.02)
            if all(a.memory.get("messages") for a in agents[1:]):  # bob + carol (alice sender included too)
                break
        for a in agents:
            msgs = a.memory.get("messages", [])
            assert msgs, f"البث لم يصل {a.name}"
            assert msgs[0]["content"]["text"] == "all hands"
    finally:
        for a in agents:
            await a.shutdown_async()


@pytest.mark.asyncio
async def test_hub_send_to_unregistered_fails():
    """إرسال لغير مسجل يفشل بوضوح (DeliveryResult.success=False)."""
    from agent_civilization.core.agents.base_agent import Message
    from agent_civilization.core.agents.communication_hub import CommunicationHub

    hub = CommunicationHub()
    msg = Message(sender="alice", recipient="ghost", content={"text": "any"})
    result = await hub.send_message(msg)
    assert not result.success
    assert result.error == "Agent not registered"
    assert hub.get_stats()["failed_deliveries"] == 1
