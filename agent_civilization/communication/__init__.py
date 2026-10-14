"""Agent-to-agent communication — the public messaging API (R-104c2, D-047 amendment).

The public interface for inter-agent messaging. The implementation lives in
``agent_civilization.core.agents.communication_hub`` (imported by ``BaseAgent``
and every executor/infrastructure agent); this package re-exports it as the
public API. Production consumer: ``specialist_runtime.py`` (the
``AGOS_AGENT_IMPL=specialists`` builder) imports ``CommunicationHub`` from here.
"""

from agent_civilization.core.agents.communication_hub import (  # noqa: F401
    CommunicationHub,
    DeliveryResult,
)

__all__ = ["CommunicationHub", "DeliveryResult"]
