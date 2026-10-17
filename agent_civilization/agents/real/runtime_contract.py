"""R-157 (D-069): the canonical contract of an agent runtime.

Two runtimes implement the same capability ("run one task on one agent of a
specialization") and are selected by ``AGOS_AGENT_IMPL``:

* ``real`` (default) — ``RealSpecialistAgent`` in ``real_agents.py``;
* ``specialists`` — ``SpecialistBackedAgent`` in ``specialist_runtime.py``,
  an adapter around one of the 1,000 ``specialists/`` classes.

Both are consumed through the same surface (server.py ``_pick_agent`` ->
``agent.execute``; ``pipeline/orchestrator.py`` ``_pick`` -> ``execute``;
``/api/agents`` -> ``snapshot``). This module is the single place that states
that surface and the one security rule both must apply to unexpected
exceptions. It adds no behaviour of its own: ``real_agents`` already applied
``public_error``'s rule inline (R-149) and keeps the exact same output.

Before R-157 the ``specialists`` runtime did NOT apply the rule in practice:
``BaseAgent.execute`` (inside the specialist instance) caught the handler's
exception first and returned ``"<Type>: <message>"``, so the raw message
reached the tenant-visible result, and the failure was stored in memory and
learning — while ``real`` reported the type only and stored nothing
(docs/audit/M8/logs/raw/076_r157_runtime_parity_before.log). The adapter now
runs the specialist's ``_process_task`` inside this canonical envelope.

Intended differences that remain (documented, tested, not hidden):
see ``INTENDED_DIFFERENCES``.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

from agent_civilization.core.safety import UnsafeURLError

#: keys every ``execute()`` outcome carries in both runtimes
OUTCOME_KEYS = frozenset({"ok", "agent", "elapsed_s", "result"})
#: optional outcome keys shared by both runtimes
OUTCOME_OPTIONAL_KEYS = frozenset({"experience"})
#: keys of ``snapshot()`` in both runtimes (``/api/agents``)
SNAPSHOT_KEYS = frozenset({"id", "name", "specialization", "status", "stats"})
#: keys of ``stats`` / ``snapshot()["stats"]`` (server writes them to storage)
STATS_KEYS = frozenset({"tasks_completed", "tasks_failed", "total_time_s"})
#: the two values ``status`` takes around ``execute()``
STATUSES = frozenset({"idle", "working"})

INTENDED_DIFFERENCES: Dict[str, str] = {
    "impl": ("specialists adds outcome['impl'] and snapshot()['impl'] = the "
             "specialist class name (R-032); real has no such key."),
    "no_handler_message": ("dispatch() returning None (no handler) yields "
                           "'no handler for <spec>' in real and the template's "
                           "'no real dispatcher for <spec>' in specialists. "
                           "Unreachable for the 20 specializations (all have "
                           "handlers: tests/test_real_dispatch_audit.py); the "
                           "1,000 template-generated classes are left untouched."),
}


def public_error(exc: BaseException) -> str:
    """The tenant-visible text of an unexpected exception (R-149).

    Only the exception TYPE is reported — its message can carry internal
    details. ``UnsafeURLError`` keeps its fixed refusal message (SEC-01 /
    SEC-05 rely on it)."""
    return str(exc) if isinstance(exc, UnsafeURLError) else type(exc).__name__


def failure_result(exc: BaseException) -> Dict[str, Any]:
    """The ``result`` of an outcome whose handler raised."""
    return {"ok": False, "error": public_error(exc)}


@runtime_checkable
class AgentRuntime(Protocol):
    """What server.py, the pipeline and /api/agents rely on."""

    name: str
    specialization: str
    agent_id: int
    status: str
    stats: Dict[str, float]

    async def execute(self, work: Dict[str, Any]) -> Dict[str, Any]: ...

    def snapshot(self) -> Dict[str, Any]: ...


__all__ = [
    "AgentRuntime", "INTENDED_DIFFERENCES", "OUTCOME_KEYS", "OUTCOME_OPTIONAL_KEYS",
    "SNAPSHOT_KEYS", "STATS_KEYS", "STATUSES", "failure_result", "public_error",
]
