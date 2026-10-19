#!/usr/bin/env python3
"""Group coordination for many logical participants (R-166, D-085).

A fan-out/fan-in coordinator over a pool of logical participants:

* fair distribution of subtasks across specialties (round-robin per
  specialty — the spread between the most- and least-loaded participant of a
  specialty never exceeds one subtask);
* idempotent messages (a duplicate ``message_id`` is recorded and ignored —
  side effects are counted once, never repeated);
* bounded messages, bounded delegation depth, and delegation-cycle rejection;
* gather/reduce with explicit loss detection — a subtask whose participant
  never answers by the deadline makes the round ``incomplete`` with the
  missing list; success is never claimed silently ("no hidden success");
* failover: a failed participant's subtask is reassigned to another
  participant of the same specialty, bounded by ``max_failovers``;
* out-of-order, late and duplicate responses are all classified, never
  silently dropped and never re-executed;
* a metrics report that is produced identically at 100 / 1k / 10k logical
  participants (the engine holds no per-participant thread or process —
  participants are lightweight records, so the 10k case is a bookkeeping
  measurement, not a resource one).

The engine is pure bookkeeping: it never performs I/O itself. A caller
injects a message layer (``send`` for work, the engine's intake methods for
results) and drives it with ``run``/``finalize``. Two layers ship with the
module: ``SimulatedMessageLayer`` (deterministic drop/duplicate/reorder/
delay for tests and the 100/1k/10k scale measurements) and the endpoint in
``civilization_routes`` drives it over the real civilization agents.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "CoordinationError",
    "MessageBudgetExceeded",
    "DelegationTooDeep",
    "DelegationCycle",
    "Participant",
    "Subtask",
    "CoordinationMessage",
    "CoordinationReport",
    "GroupCoordinator",
    "SimulatedMessageLayer",
    "run_coordination",
    "DEFAULT_MAX_MESSAGES",
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "DEFAULT_MAX_FAILOVERS",
]

DEFAULT_MAX_MESSAGES = 10_000
DEFAULT_MAX_DELEGATION_DEPTH = 3
DEFAULT_MAX_FAILOVERS = 2


class CoordinationError(RuntimeError):
    """Base class of every refusal of the coordinator."""


class MessageBudgetExceeded(CoordinationError):
    """The bounded-message budget of the round was exhausted."""


class DelegationTooDeep(CoordinationError):
    """A delegation chain exceeded the configured depth limit."""


class DelegationCycle(CoordinationError):
    """A delegation chain returned to a participant that is already in it."""


@dataclass(frozen=True)
class Participant:
    """A logical participant: a record, never a thread or a process."""
    participant_id: str
    specialty: str
    capacity: int = 1


@dataclass(frozen=True)
class Subtask:
    """A unit of work handed to exactly one participant."""
    subtask_id: str
    specialty: str
    payload: Any = None


@dataclass(frozen=True)
class CoordinationMessage:
    """A message on the coordination layer (work or a result).

    ``attempt`` stamps the coordination attempt the message belongs to
    (0 for the initial work, 1+ for failovers). Results stamped with a
    superseded attempt — or unstamped results once a subtask has been
    reassigned — are rejected as stale (D-087 fencing).
    """
    message_id: str
    kind: str  # "work" | "result" | "delegate"
    subtask_id: str
    participant_id: str
    body: Any = None
    attempt: Optional[int] = None


@dataclass
class CoordinationReport:
    """The final report of one coordination round.

    ``ok`` is True only when every subtask has a result AND no result body
    reports failure (a dict with ``ok`` falsy) — a failed participant answer
    is never a hidden success, and a participant that never answers is a
    missing subtask.
    """
    ok: bool                       # all subtasks answered AND none failed
    incomplete_reason: Optional[str] = None
    subtasks: int = 0
    results: Dict[str, Any] = field(default_factory=dict)   # subtask_id -> body
    missing: List[str] = field(default_factory=list)        # subtask ids with no result
    failed: List[str] = field(default_factory=list)         # subtask ids whose answer reported failure
    duplicated_message_ids: List[str] = field(default_factory=list)
    late_message_ids: List[str] = field(default_factory=list)
    out_of_order_message_ids: List[str] = field(default_factory=list)
    failovers: int = 0
    messages_sent: int = 0
    messages_delivered: int = 0
    side_effects: int = 0
    fairness_spread: int = 0
    participants: int = 0
    duration_s: float = 0.0

    def metrics(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "subtasks": self.subtasks,
            "results": len(self.results),
            "missing": len(self.missing),
            "failed": len(self.failed),
            "duplicates": len(self.duplicated_message_ids),
            "late": len(self.late_message_ids),
            "out_of_order": len(self.out_of_order_message_ids),
            "failovers": self.failovers,
            "messages_sent": self.messages_sent,
            "messages_delivered": self.messages_delivered,
            "side_effects": self.side_effects,
            "fairness_spread": self.fairness_spread,
            "participants": self.participants,
            "duration_s": round(self.duration_s, 6),
        }


class GroupCoordinator:
    """Fan-out/fan-in coordination over logical participants.

    The caller drives the round:

    1. ``distribute(subtasks)`` fairly assigns each subtask to a participant
       of its specialty and returns the work messages (idempotent — the same
       subtask is never assigned twice in one round).
    2. The caller (or the injected layer) executes the work and feeds results
       back through ``submit_result`` — duplicate/late/reordered messages are
       classified, never re-executed.
    3. ``finalize`` gathers what arrived, reports what did not, and reduces.
    """

    def __init__(
        self,
        participants: Sequence[Participant],
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
        max_failovers: int = DEFAULT_MAX_FAILOVERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not participants:
            raise CoordinationError("coordination needs at least one participant")
        ids = [p.participant_id for p in participants]
        if len(set(ids)) != len(ids):
            raise CoordinationError("participant ids must be unique")
        self.participants: Dict[str, Participant] = {p.participant_id: p for p in participants}
        self._by_specialty: Dict[str, List[str]] = {}
        for p in participants:
            self._by_specialty.setdefault(p.specialty, []).append(p.participant_id)
        self.max_messages = max_messages
        self.max_delegation_depth = max_delegation_depth
        self.max_failovers = max_failovers
        self._clock = clock

        # round state
        self._assignments: Dict[str, str] = {}       # subtask_id -> participant_id
        self._expected: Dict[str, str] = {}           # subtask_id -> participant_id
        self._seen_message_ids: set = set()
        self._results: Dict[str, Any] = {}            # subtask_id -> body
        self._finalized = False
        self._started_at = 0.0
        self._load: Dict[str, int] = {}                # participant_id -> assigned subtasks
        self._failovers: Dict[str, int] = {}          # subtask_id -> attempts used
        self._attempt: Dict[str, int] = {}           # subtask_id -> current attempt (D-087 fencing)
        self._duplicates: List[str] = []
        self._late: List[str] = []
        self._out_of_order: List[str] = []
        self._side_effects = 0
        self._messages_sent = 0
        self._messages_delivered = 0
        self._arrival_order: List[str] = []           # message ids in arrival order

    # ------------------------------------------------------------------ setup

    def participants_for(self, specialty: str) -> List[str]:
        return list(self._by_specialty.get(specialty, []))

    # ------------------------------------------------------------- distribute

    def distribute(self, subtasks: Sequence[Subtask]) -> List[CoordinationMessage]:
        """Fairly assign subtasks to participants and build the work messages."""
        if self._finalized:
            raise CoordinationError("the round is already finalized")
        if self._assignments:
            raise CoordinationError("distribute may be called once per round")
        started = time.monotonic() if self._clock is time.monotonic else self._clock()
        self._started_at = started

        messages: List[CoordinationMessage] = []
        # round-robin cursor per specialty keeps the spread <= 1
        cursor: Dict[str, int] = {}
        for st in subtasks:
            pool = self._by_specialty.get(st.specialty)
            if not pool:
                raise CoordinationError(
                    f"no participant of specialty {st.specialty!r} for subtask {st.subtask_id!r}")
            if st.subtask_id in self._assignments:
                raise CoordinationError(f"subtask {st.subtask_id!r} assigned twice")
            i = cursor.get(st.specialty, 0)
            pid = pool[i % len(pool)]
            cursor[st.specialty] = i + 1
            self._assignments[st.subtask_id] = pid
            self._expected[st.subtask_id] = pid
            self._attempt[st.subtask_id] = 0
            self._load[pid] = self._load.get(pid, 0) + 1
            msg = CoordinationMessage(
                message_id=f"work:{st.subtask_id}:{pid}",
                kind="work",
                subtask_id=st.subtask_id,
                participant_id=pid,
                body=st.payload,
                attempt=0,
            )
            messages.append(msg)
        if len(messages) > self.max_messages:
            raise MessageBudgetExceeded(
                f"{len(messages)} work messages exceed the budget of {self.max_messages}")
        self._messages_sent += len(messages)
        self._arrival_order.extend(m.message_id for m in messages)
        return messages

    def fairness_spread(self) -> int:
        """The worst load spread per specialty, counting every participant of
        each specialty that received work — including zero-load ones. A single
        subtask over two participants of one specialty is a spread of 1."""
        if not self._load:
            return 0
        worst = 0
        for spec, pids in self._by_specialty.items():
            if not any(self._load.get(p) for p in pids):
                continue  # this specialty received no work
            loads = [self._load.get(p, 0) for p in pids]
            worst = max(worst, max(loads) - min(loads))
        return worst

    # ----------------------------------------------------------- failover

    def failover(self, subtask_id: str, from_participant: str) -> Optional[CoordinationMessage]:
        """Reassign a failed subtask to another participant of the same specialty.

        Bounded by ``max_failovers`` and by the message budget. NOTE (D-086):
        failover is an explicit engine capability — it re-executes the subtask,
        so the caller must only drive it for executors whose side effects are
        safe to repeat; ``run_coordination`` does NOT apply it automatically.
        """
        if subtask_id not in self._expected:
            raise CoordinationError(f"unknown subtask {subtask_id!r}")
        if subtask_id in self._results:
            return None  # already answered; nothing to fail over
        used = self._failovers.get(subtask_id, 0)
        if used >= self.max_failovers:
            return None
        current = self._expected[subtask_id]
        st_specialty = self.participants[current].specialty
        pool = [p for p in self._by_specialty.get(st_specialty, []) if p != current]
        if not pool:
            return None
        if self._messages_sent + 1 > self.max_messages:
            raise MessageBudgetExceeded(
                f"failover of {subtask_id!r} would exceed the budget of {self.max_messages}")
        self._failovers[subtask_id] = used + 1
        self._expected[subtask_id] = pool[0]
        self._attempt[subtask_id] = used + 1
        self._load[pool[0]] = self._load.get(pool[0], 0) + 1
        self._load[current] = max(0, self._load.get(current, 1) - 1)
        self._messages_sent += 1
        return CoordinationMessage(
            message_id=f"work:{subtask_id}:failover{used + 1}",
            kind="work",
            subtask_id=subtask_id,
            participant_id=pool[0],
            body=None,
            attempt=used + 1,
        )

    # ------------------------------------------------------------ delegation

    def delegate(
        self, from_participant: str, to_participant: str, subtask_id: str,
        chain: Optional[Sequence[str]] = None,
    ) -> CoordinationMessage:
        """A participant hands its subtask to another participant.

        ``chain`` is the delegation chain so far, ending with the delegator.
        Depth beyond ``max_delegation_depth`` is refused; a cycle (any
        participant appearing twice) is refused.
        """
        path = list(chain or [from_participant])
        if from_participant not in path:
            path.append(from_participant)
        if to_participant in path:
            raise DelegationCycle(
                f"delegation cycle detected: {' -> '.join(path + [to_participant])}")
        if len(path) > self.max_delegation_depth:
            raise DelegationTooDeep(
                f"delegation depth {len(path)} exceeds the limit of "
                f"{self.max_delegation_depth}")
        if to_participant not in self.participants:
            raise CoordinationError(f"unknown participant {to_participant!r}")
        if self._messages_sent + 1 > self.max_messages:
            raise MessageBudgetExceeded(
                f"delegation of {subtask_id!r} would exceed the budget of {self.max_messages}")
        self._messages_sent += 1
        return CoordinationMessage(
            message_id=f"delegate:{subtask_id}:{from_participant}->{to_participant}",
            kind="delegate",
            subtask_id=subtask_id,
            participant_id=to_participant,
            body=None,
        )

    # ---------------------------------------------------------- result intake

    def submit_result(self, message: CoordinationMessage) -> bool:
        """Record a result. Returns True when it is the first accepted answer
        for its subtask from its ASSIGNED participant at the CURRENT attempt.

        Ownership: a result for a subtask from a participant that is not its
        current assignee (including the old participant after a failover) is
        classified as late — never counted as the subtask's answer.
        Attempt fencing (D-087): once a subtask has been reassigned (failover),
        a result is accepted only if it carries the CURRENT attempt stamp —
        a stale answer from a superseded attempt is rejected even when its
        participant has become the assignee again (round-trip failover), and
        an unstamped result cannot be proven current after a reassignment.
        Duplicate message ids are recorded and ignored (no repeated side
        effects). Results for unknown subtasks are recorded as late.
        """
        if message.message_id in self._seen_message_ids:
            self._duplicates.append(message.message_id)
            return False
        self._seen_message_ids.add(message.message_id)
        self._arrival_order.append(message.message_id)
        self._messages_delivered += 1

        if message.subtask_id not in self._expected:
            self._late.append(message.message_id)
            return False
        if self._expected[message.subtask_id] != message.participant_id:
            # not the assignee: a foreign or stale (post-failover) answer
            self._late.append(message.message_id)
            return False
        current_attempt = self._attempt.get(message.subtask_id, 0)
        reassigned = self._failovers.get(message.subtask_id, 0) > 0
        if reassigned:
            # the subtask was reassigned at least once: only a result stamped
            # with the CURRENT attempt is provably not from a superseded try
            if message.attempt != current_attempt:
                self._late.append(message.message_id)
                return False
        elif message.attempt not in (None, 0):
            # never reassigned: an out-of-range stamp cannot be current
            self._late.append(message.message_id)
            return False
        if message.subtask_id in self._results:
            # a second, different message from the assignee for a subtask that
            # already has a result: classified, never re-executed
            self._out_of_order.append(message.message_id)
            return False
        if self._finalized:
            self._late.append(message.message_id)
            return False
        self._results[message.subtask_id] = message.body
        self._side_effects += 1
        return True

    # --------------------------------------------------------------- finalize

    def finalize(self, reducer: Optional[Callable[[Dict[str, Any]], Any]] = None) -> CoordinationReport:
        """Close the round: report missing subtasks, failed answers, and
        reduce the results. ok requires BOTH no missing subtasks AND no failed
        answers — a participant answering {ok: False} is never a hidden
        success."""
        self._finalized = True
        missing = [sid for sid in self._expected if sid not in self._results]
        failed = [
            sid for sid, body in self._results.items()
            if isinstance(body, dict) and body.get("ok") is False
        ]
        report = CoordinationReport(
            ok=not missing and not failed,
            incomplete_reason=(None if not missing and not failed else
                               ("missing results" if missing else "failed results")),
            subtasks=len(self._expected),
            results=dict(self._results),
            missing=missing,
            failed=failed,
            duplicated_message_ids=list(self._duplicates),
            late_message_ids=list(self._late),
            out_of_order_message_ids=list(self._out_of_order),
            failovers=sum(self._failovers.values()),
            messages_sent=self._messages_sent,
            messages_delivered=self._messages_delivered,
            side_effects=self._side_effects,
            fairness_spread=self.fairness_spread(),
            participants=len(self.participants),
        )
        if self._started_at:
            report.duration_s = self._clock() - self._started_at
        if reducer is not None and report.results:
            report.results = {"reduced": reducer(report.results)}
        return report


class SimulatedMessageLayer:
    """A deterministic in-memory message layer for tests and scale runs.

    Supports: dropping messages (loss), duplicating them, reordering them,
    delaying them, and recording everything that was sent. The layer never
    touches the network.
    """

    def __init__(
        self,
        *,
        drop_message_ids: Optional[Iterable[str]] = None,
        duplicate_message_ids: Optional[Iterable[str]] = None,
        reorder: bool = False,
    ) -> None:
        self.drop = set(drop_message_ids or [])
        self.duplicate = set(duplicate_message_ids or [])
        self.reorder = reorder
        self.sent: List[CoordinationMessage] = []
        self.delivered: List[CoordinationMessage] = []
        self.dropped: List[CoordinationMessage] = []

    def send(self, messages: Sequence[CoordinationMessage]) -> List[CoordinationMessage]:
        """Deliver messages, applying drop/duplicate/reorder, in order."""
        out: List[CoordinationMessage] = []
        for m in messages:
            self.sent.append(m)
            if m.message_id in self.drop:
                self.dropped.append(m)
                continue
            out.append(m)
            if m.message_id in self.duplicate:
                out.append(m)  # the same message id arrives twice
        if self.reorder and len(out) > 1:
            out = [out[-1], *out[:-1]]
        self.delivered.extend(out)
        return out


async def run_coordination(
    subtasks: Sequence[Subtask],
    participants: Sequence[Participant],
    execute: Callable[[CoordinationMessage], Awaitable[CoordinationMessage]],
    *,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    max_failovers: int = DEFAULT_MAX_FAILOVERS,
) -> CoordinationReport:
    """Drive a full round over a real (awaitable) executor.

    ``execute`` receives each work message and returns the result message.
    This is the shape the endpoint uses over the real civilization agents;
    tests use it with trivial executors.

    D-086: a failing ``execute`` leaves the subtask MISSING (reported) — the
    round does NOT automatically re-execute an unconfirmed side effect on
    another participant. Failover stays an explicit engine capability
    (``GroupCoordinator.failover``) for callers whose executors are safe to
    repeat; the endpoint does not use it automatically.
    """
    coordinator = GroupCoordinator(
        participants,
        max_messages=max_messages,
        max_delegation_depth=max_delegation_depth,
        max_failovers=max_failovers,
    )
    work = coordinator.distribute(subtasks)

    async def run_one(msg: CoordinationMessage) -> None:
        try:
            result = await execute(msg)
        except Exception:
            return  # missing — reported by finalize, never a hidden success
        coordinator.submit_result(result)

    await asyncio.gather(*(run_one(m) for m in work))
    return coordinator.finalize()
