"""R-163 — durable task queue state machine (D-082).

The explicit, single source of truth for task lifecycle states and their
allowed transitions. Three surfaces must never disagree again (RAM record,
storage row, published events): every status change on the task path goes
through :func:`transition`, which rejects anything not in
:data:`ALLOWED` with :class:`InvalidTransition`.

States (P3 spec, R-163):

    accepted, queued, claimed, running, waiting, retrying,
    completed, failed, cancelled, dead_letter

Public API mapping (compatibility, D-082): the API contract vocabulary is
kept as-is. ``PUBLIC_STATUS`` maps the internal states onto the public
statuses the API has always served:

    accepted/queued/waiting/retrying/claimed -> "queued"   (not started yet)
    running                                 -> "running"
    completed                               -> "completed"
    failed                                  -> "failed"
    cancelled                              -> "failed"   (result says why)
    dead_letter                             -> "failed"   (result says why)

``cancelled`` and ``dead_letter`` are terminal like ``failed``: the record
always carries an outcome whose ``result`` explains the fixed reason
("cancelled", "dead-letter: max attempts exceeded") — no silent loss and no
fake success.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Optional


class TaskState(str, Enum):
    """The ten lifecycle states of a durable task (R-163)."""

    ACCEPTED = "accepted"        # validated, not yet persisted (durable mode)
    QUEUED = "queued"            # persisted, waiting for a worker claim
    CLAIMED = "claimed"          # a worker holds the claim token + lease
    RUNNING = "running"          # the agent is executing (attempt counted)
    WAITING = "waiting"          # deferred (capacity/backpressure — R-164)
    RETRYING = "retrying"        # lease expired / worker died: will requeue
    COMPLETED = "completed"     # terminal, ok
    FAILED = "failed"            # terminal, not ok
    CANCELLED = "cancelled"     # terminal, user asked to stop
    DEAD_LETTER = "dead_letter"  # terminal, max attempts exceeded

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Allowed transitions. Everything not listed is invalid and rejected.
#: Recovery paths (worker death / expired lease) are explicit:
#: ``claimed/running -> retrying -> queued | dead_letter``.
ALLOWED: Dict[TaskState, FrozenSet[TaskState]] = {
    TaskState.ACCEPTED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.CLAIMED, TaskState.WAITING,
                               TaskState.CANCELLED}),
    TaskState.WAITING: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.CLAIMED: frozenset({TaskState.RUNNING, TaskState.QUEUED,
                                 TaskState.RETRYING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset({TaskState.COMPLETED, TaskState.FAILED,
                                  TaskState.RETRYING, TaskState.CANCELLED}),
    TaskState.RETRYING: frozenset({TaskState.QUEUED, TaskState.DEAD_LETTER,
                                   TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.DEAD_LETTER: frozenset(),
}

#: States after which no transition is legal (nothing may leave them).
TERMINAL: FrozenSet[TaskState] = frozenset({
    TaskState.COMPLETED, TaskState.FAILED,
    TaskState.CANCELLED, TaskState.DEAD_LETTER,
})

#: States that mean "not started for the caller" — served as "queued".
PENDING_PUBLIC = "queued"

#: Internal state -> the public API status (kept identical to the v1
#: contract vocabulary; see the module docstring).
PUBLIC_STATUS: Dict[TaskState, str] = {
    TaskState.ACCEPTED: PENDING_PUBLIC,
    TaskState.QUEUED: PENDING_PUBLIC,
    TaskState.WAITING: PENDING_PUBLIC,
    TaskState.RETRYING: PENDING_PUBLIC,
    TaskState.CLAIMED: PENDING_PUBLIC,
    TaskState.RUNNING: "running",
    TaskState.COMPLETED: "completed",
    TaskState.FAILED: "failed",
    TaskState.CANCELLED: "failed",
    TaskState.DEAD_LETTER: "failed",
}

#: The states a claim can be taken from (what the dispatcher may pick up).
CLAIMABLE: FrozenSet[TaskState] = frozenset({TaskState.QUEUED})

#: The states a lease covers (renewal and recovery look at these).
LEASED: FrozenSet[TaskState] = frozenset({TaskState.CLAIMED, TaskState.RUNNING})


class InvalidTransition(Exception):
    """A status change that the state machine does not allow (R-163)."""

    def __init__(self, current: str, new: str):
        super().__init__(f"invalid task transition: {current} -> {new}")
        self.current = current
        self.new = new


def state(value: object) -> TaskState:
    """Coerce a stored/raw status string into a :class:`TaskState`.

    Unknown values raise :class:`ValueError` — an unrecognised status is a
    data problem to surface, never to silently treat as anything.
    """
    if isinstance(value, TaskState):
        return value
    try:
        return TaskState(str(value))
    except ValueError as e:
        raise ValueError(f"unknown task state: {value!r}") from e


def transition(current: object, new: object) -> TaskState:
    """Validate ``current -> new`` and return the new :class:`TaskState`.

    Raises :class:`InvalidTransition` when the machine does not allow it
    (including any transition out of a terminal state).
    """
    c, n = state(current), state(new)
    if n not in ALLOWED[c]:
        raise InvalidTransition(c.value, n.value)
    return n


def is_terminal(value: object) -> bool:
    return state(value) in TERMINAL


def public_status(value: object) -> str:
    """The public API status served for an internal state (compat mapping)."""
    return PUBLIC_STATUS[state(value)]


def outcome_after_expiry(attempts: int, max_attempts: int) -> TaskState:
    """What a recovered (lease-expired) task becomes: requeue or dead-letter.

    ``attempts`` counts executions that reached ``running``. When the task
    already exhausted ``max_attempts`` executions, recovery ends it in
    ``dead_letter`` instead of looping forever.
    """
    if attempts >= max_attempts:
        return TaskState.DEAD_LETTER
    return TaskState.RETRYING


def cancelled_outcome(agent: Optional[str], specialization: str) -> Dict:
    """The fixed outcome published for a cancelled task (no fake success)."""
    return {"ok": False, "agent": agent, "specialization": specialization,
            "elapsed_s": 0, "result": {"error": "cancelled"}}


def dead_letter_outcome(agent: Optional[str], specialization: str,
                        attempts: int, max_attempts: int) -> Dict:
    """The fixed outcome published for a dead-lettered task."""
    return {"ok": False, "agent": agent, "specialization": specialization,
            "elapsed_s": 0,
            "result": {"error": f"dead-letter: max attempts exceeded "
                                f"({attempts}/{max_attempts})"}}
