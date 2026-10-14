"""Runtime utilities for agent loops (R-104c2, D-047 amendment).

The message-processing loop of every ``BaseAgent`` subclass (``_run_loop``,
``_process_workqueue``, ``_process_inbox``) runs inside an event loop; the
helpers here are the loop-facing utilities those loops use. The package's role
is live — it is exercised by every agent message-loop test (e.g. the
``DataWorkerAgent`` workqueue tests in tests/test_r104_data_worker.py, and
tests/test_memory_learning_comm.py) — not a formal import.
"""

from agent_civilization.core.agents.base_agent import BaseAgent, Message  # noqa: F401

__all__ = ["BaseAgent", "Message"]
