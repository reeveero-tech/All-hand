"""Agent-to-agent communication — the public messaging API (R-104c2, D-047 amendment).

This package is the canonical home for inter-agent messaging. The implementation
lives in ``agent_civilization.core.agents.communication_hub`` (imported by
``BaseAgent`` and every executor/infrastructure agent); this package re-exports it
so callers can ``from agent_civilization.communication import CommunicationHub``
— the role is live (DataWorkerAgent, TaskDispatcherAgent, the specialists runtime
and their tests all exercise it) and tested (tests/test_r104_data_worker.py and
tests/test_memory_learning_comm.py run through it).

Runtime (``utils/``): shared runtime helpers. The event-loop utilities used by the
message loops of every ``BaseAgent`` subclass live here so the package carries a
live, tested responsibility rather than a formal import.
"""

from agent_civilization.core.agents.communication_hub import (  # noqa: F401
    CommunicationHub,
    DeliveryResult,
)

__all__ = ["CommunicationHub", "DeliveryResult"]
