"""Shared utilities for the agent civilization (R-104c2, D-047 amendment).

Re-exports the small shared helpers agents rely on. The role is live and tested:
``Message`` is the payload every agent-to-agent interaction is built on (exercised
by tests/test_r104_data_worker.py, tests/test_memory_learning_comm.py), and
``BaseAgent`` is the class every executor and infrastructure agent inherits.
"""

from agent_civilization.core.agents.base_agent import BaseAgent, Message  # noqa: F401

__all__ = ["BaseAgent", "Message"]
