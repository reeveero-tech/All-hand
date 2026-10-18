"""R-162 (D-081): standard agent registry — identity, worker and task kept apart.

Three things that used to be one object (``RealSpecialistAgent`` was at the
same time the definition, the executor and the "busy" flag of a task) are
separate records here:

* ``AgentIdentity`` — WHAT an agent is: a unique ``agent_id``, its
  specialization, capabilities and ``version``. A logical definition: a few
  hundred bytes, no thread, no process, no socket. 10,000 of them are a dict.
* ``Worker`` — WHERE work runs: a worker id, a pool, a ``capacity`` (leases it
  may hold at once), a lifecycle state and a heartbeat. One worker serves many
  identities (the in-process server registers exactly one).
* ``Lease`` — WHICH task runs as which identity on which worker, until when.
  A lease is the only link between a task and a worker, so stopping, removing
  or losing a worker always yields the exact list of task ids that were still
  running on it (``orphans``) — never a task silently stuck in ``running``.

Lifecycle of a worker (any other transition raises ``InvalidTransition``)::

    starting -> ready -> draining -> stopped
         \\         \\         \\
          +--------+---------+--> dead     (missed heartbeat, see reap())

* ``ready``: takes new leases while it has free capacity and is live.
* ``draining``: keeps its leases until they end, takes no new one.
* ``stopped`` / ``dead``: holds no lease; its leases were returned as orphans.

Identity states: ``active`` (assignable), ``disabled`` (no new lease; running
ones finish), ``retired`` (an older version replaced by ``upgrade``).

Health of a worker (``worker_health``): ``healthy`` | ``saturated`` (live and
ready, no free capacity) | ``draining`` | ``unresponsive`` (heartbeat older
than ``heartbeat_ttl_s`` but not reaped yet) | ``stopped`` | ``dead`` |
``starting``.

Saturation: ``in_flight / capacity`` per worker and for the whole registry.
At saturation ``acquire`` raises ``Saturated`` — explicit, never a silent
queue. Waiting, quotas and retry belong to R-163/R-164, not here.

Not thread-safe by design: the server runs it on one asyncio loop and every
method is synchronous (no await inside), so no interleaving is possible.
The clock is injectable (``clock``) for deterministic tests.
"""
from __future__ import annotations

import heapq
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

WORKER_STATES = ("starting", "ready", "draining", "stopped", "dead")
_WORKER_TRANSITIONS = {
    "starting": {"ready", "stopped", "dead"},
    "ready": {"draining", "stopped", "dead"},
    "draining": {"stopped", "dead"},
    "stopped": set(),
    "dead": set(),
}
IDENTITY_STATES = ("active", "disabled", "retired")

DEFAULT_HEARTBEAT_TTL_S = 30.0
DEFAULT_LEASE_TTL_S = 600.0


class RegistryError(Exception):
    """Base class of every refusal of the registry."""


class DuplicateRegistration(RegistryError):
    """An agent id, agent name or worker id that is already registered."""


class UnknownAgent(RegistryError):
    pass


class UnknownWorker(RegistryError):
    pass


class InvalidTransition(RegistryError):
    pass


class NotAssignable(RegistryError):
    """The identity exists but is not ``active`` (disabled or retired)."""


class Saturated(RegistryError):
    """No live, ready worker of the pool has free capacity."""


class LeaseError(RegistryError):
    """Unknown, ended or mismatched lease."""


@dataclass(slots=True)
class AgentIdentity:
    agent_id: str
    name: str
    specialization: str
    version: str = "1"
    capabilities: Tuple[str, ...] = ()
    state: str = "active"
    registered_at: float = 0.0


@dataclass(slots=True)
class Worker:
    worker_id: str
    pool: str
    capacity: int
    state: str = "starting"
    started_at: float = 0.0
    last_heartbeat: float = 0.0
    in_flight: int = 0
    leases_total: int = 0


@dataclass(slots=True)
class Lease:
    task_id: str
    agent_id: str
    agent_version: str
    worker_id: str
    acquired_at: float
    expires_at: float


@dataclass
class Orphans:
    """Tasks whose worker went away while they were running. The caller must
    end or requeue each of them — the registry has already forgotten their
    leases, so they can never be reported as running by it again."""
    worker_id: str
    reason: str
    task_ids: List[str] = field(default_factory=list)


def positive_int_env(name: str, default: int, lo: int = 1, hi: int = 1_000_000) -> int:
    """Read a bounded positive integer; an invalid value is a startup error
    (same rule as AGENTS_PER_SPEC and AGOS_SSE_*)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if not lo <= value <= hi:
        raise RuntimeError(f"{name} must be between {lo} and {hi}, got {value}")
    return value


class AgentRegistry:
    def __init__(self, *, heartbeat_ttl_s: float = DEFAULT_HEARTBEAT_TTL_S,
                 lease_ttl_s: float = DEFAULT_LEASE_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        if heartbeat_ttl_s <= 0 or lease_ttl_s <= 0:
            raise ValueError("heartbeat_ttl_s and lease_ttl_s must be > 0")
        self.heartbeat_ttl_s = float(heartbeat_ttl_s)
        self.lease_ttl_s = float(lease_ttl_s)
        self._clock = clock
        self._agents: Dict[str, AgentIdentity] = {}
        self._names: Dict[str, str] = {}                 # name -> agent_id
        self._history: Dict[str, List[str]] = {}         # agent_id -> versions, oldest first
        self._by_spec: Dict[str, set] = {}               # spec -> active agent ids
        self._load: Dict[str, int] = {}                  # agent_id -> leases held
        self._heap: Dict[str, list] = {}                 # spec -> lazy heap (load, seq, agent_id)
        self._seq = 0
        self._workers: Dict[str, Worker] = {}
        self._leases: Dict[str, Lease] = {}              # task_id -> lease
        self._by_worker: Dict[str, set] = {}             # worker_id -> task ids
        self.counters = {"acquired": 0, "released": 0, "expired": 0, "orphaned": 0,
                         "saturated": 0, "reaped_workers": 0}

    # ---------------------------------------------------------------- identity
    def register_agent(self, agent_id: str, name: str, specialization: str, *,
                       version: str = "1", capabilities: Iterable[str] = ()) -> AgentIdentity:
        for label, value in (("agent_id", agent_id), ("name", name), ("specialization", specialization),
                             ("version", version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if agent_id in self._agents:
            raise DuplicateRegistration(f"agent_id already registered: {agent_id}")
        if name in self._names:
            raise DuplicateRegistration(f"agent name already registered: {name}")
        ident = AgentIdentity(agent_id, name, specialization, version, tuple(capabilities),
                              "active", self._clock())
        self._agents[agent_id] = ident
        self._names[name] = agent_id
        self._history[agent_id] = [version]
        self._load[agent_id] = 0
        self._by_spec.setdefault(specialization, set()).add(agent_id)
        self._push(specialization, agent_id)
        return ident

    def upgrade(self, agent_id: str, version: str, *, capabilities: Optional[Iterable[str]] = None) -> AgentIdentity:
        """Replace the definition by a NEW version. Running leases keep the
        version they started with (``Lease.agent_version``); new leases get
        the new one. Re-registering a version already seen is refused."""
        ident = self._get(agent_id)
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if version in self._history[agent_id]:
            raise DuplicateRegistration(f"{agent_id} version {version} already registered")
        if ident.state == "retired":
            raise InvalidTransition(f"{agent_id} is retired")
        self._history[agent_id].append(version)
        ident.version = version
        if capabilities is not None:
            ident.capabilities = tuple(capabilities)
        return ident

    def versions(self, agent_id: str) -> List[str]:
        self._get(agent_id)
        return list(self._history[agent_id])

    def disable_agent(self, agent_id: str) -> None:
        """No new lease; leases already held run to their end."""
        ident = self._get(agent_id)
        if ident.state == "active":
            ident.state = "disabled"
            self._by_spec[ident.specialization].discard(agent_id)

    def enable_agent(self, agent_id: str) -> None:
        ident = self._get(agent_id)
        if ident.state == "retired":
            raise InvalidTransition(f"{agent_id} is retired")
        if ident.state == "disabled":
            ident.state = "active"
            self._by_spec[ident.specialization].add(agent_id)
            self._push(ident.specialization, agent_id)

    def retire_agent(self, agent_id: str) -> None:
        ident = self._get(agent_id)
        ident.state = "retired"
        self._by_spec[ident.specialization].discard(agent_id)

    def agent(self, agent_id: str) -> AgentIdentity:
        return self._get(agent_id)

    def agent_by_name(self, name: str) -> AgentIdentity:
        if name not in self._names:
            raise UnknownAgent(f"unknown agent name: {name}")
        return self._agents[self._names[name]]

    def select_agent(self, specialization: str) -> AgentIdentity:
        """Least-loaded active identity of the specialization (ties: the identity
        whose load changed least recently). O(log n) amortised via a lazy heap."""
        active = self._by_spec.get(specialization)
        if not active:
            raise UnknownAgent(f"no active agent for specialization: {specialization}")
        heap = self._heap.setdefault(specialization, [])
        while heap:
            load, seq, aid = heap[0]
            if aid in active and self._load.get(aid) == load:
                return self._agents[aid]
            heapq.heappop(heap)                 # stale entry
        # every entry was stale: rebuild from the truth
        for aid in sorted(active):
            self._push(specialization, aid)
        return self.select_agent(specialization)

    # ----------------------------------------------------------------- workers
    def register_worker(self, worker_id: str, pool: str, capacity: int) -> Worker:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if worker_id in self._workers:
            raise DuplicateRegistration(f"worker already registered: {worker_id}")
        now = self._clock()
        w = Worker(worker_id, pool, capacity, "starting", now, now)
        self._workers[worker_id] = w
        self._by_worker[worker_id] = set()
        return w

    def mark_ready(self, worker_id: str) -> None:
        self._transition(worker_id, "ready")
        self._workers[worker_id].last_heartbeat = self._clock()

    def heartbeat(self, worker_id: str) -> None:
        w = self._worker(worker_id)
        if w.state in ("stopped", "dead"):
            raise InvalidTransition(f"worker {worker_id} is {w.state}; a heartbeat does not revive it")
        w.last_heartbeat = self._clock()

    def is_live(self, worker_id: str) -> bool:
        w = self._worker(worker_id)
        return w.state in ("starting", "ready", "draining") and \
            self._clock() - w.last_heartbeat <= self.heartbeat_ttl_s

    def worker_health(self, worker_id: str) -> str:
        w = self._worker(worker_id)
        if w.state in ("stopped", "dead", "starting"):
            return w.state
        if not self.is_live(worker_id):
            return "unresponsive"
        if w.state == "draining":
            return "draining"
        return "saturated" if w.in_flight >= w.capacity else "healthy"

    def drain(self, worker_id: str) -> None:
        self._transition(worker_id, "draining")

    def stop(self, worker_id: str, reason: str = "worker stopped") -> Orphans:
        """Stop now. Leases still held are ended and returned as orphans."""
        self._transition(worker_id, "stopped")
        return self._orphan_all(worker_id, reason)

    def remove_worker(self, worker_id: str) -> Orphans:
        """Remove the worker from the table. A worker that still holds leases
        is stopped first, so its tasks come back as orphans — removal never
        leaves a task attached to a worker that no longer exists."""
        w = self._worker(worker_id)
        orphans = Orphans(worker_id, "worker removed")
        if w.state not in ("stopped", "dead"):
            orphans = self.stop(worker_id, "worker removed")
        del self._workers[worker_id]
        del self._by_worker[worker_id]
        return orphans

    def reap(self) -> List[Orphans]:
        """Mark every worker whose heartbeat is older than the TTL ``dead``
        and return its orphans; also end leases past ``expires_at``."""
        out: List[Orphans] = []
        now = self._clock()
        for wid, w in list(self._workers.items()):
            if w.state in ("starting", "ready", "draining") and now - w.last_heartbeat > self.heartbeat_ttl_s:
                w.state = "dead"
                self.counters["reaped_workers"] += 1
                out.append(self._orphan_all(wid, "worker missed its heartbeat"))
        expired: Dict[str, Orphans] = {}
        for tid, lease in list(self._leases.items()):
            if lease.expires_at < now:
                self._end(tid)
                self.counters["expired"] += 1
                expired.setdefault(lease.worker_id, Orphans(lease.worker_id, "lease expired")).task_ids.append(tid)
        return out + list(expired.values())

    def worker(self, worker_id: str) -> Worker:
        return self._worker(worker_id)

    # ------------------------------------------------------------------ leases
    def acquire(self, task_id: str, agent_id: str, *, pool: Optional[str] = None,
                worker_id: Optional[str] = None) -> Lease:
        """Bind ``task_id`` to ``agent_id`` on a live ready worker with free
        capacity (the one with most free slots; or ``worker_id`` if given).
        Raises ``Saturated`` when none can take it."""
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty string")
        if task_id in self._leases:
            raise LeaseError(f"task already holds a lease: {task_id}")
        ident = self._get(agent_id)
        if ident.state != "active":
            raise NotAssignable(f"agent {agent_id} is {ident.state}")
        now = self._clock()
        if worker_id is not None:
            cands = [self._worker(worker_id)]
        else:
            cands = [w for w in self._workers.values() if pool is None or w.pool == pool]
        best: Optional[Worker] = None
        for w in cands:
            if w.state == "ready" and now - w.last_heartbeat <= self.heartbeat_ttl_s and w.in_flight < w.capacity:
                if best is None or (w.capacity - w.in_flight) > (best.capacity - best.in_flight):
                    best = w
        if best is None:
            self.counters["saturated"] += 1
            raise Saturated(f"no live ready worker with free capacity (pool={pool or '*'})")
        lease = Lease(task_id, agent_id, ident.version, best.worker_id, now, now + self.lease_ttl_s)
        self._leases[task_id] = lease
        self._by_worker[best.worker_id].add(task_id)
        best.in_flight += 1
        best.leases_total += 1
        self._load[agent_id] += 1
        self._push(ident.specialization, agent_id)
        self.counters["acquired"] += 1
        return lease

    def renew(self, task_id: str) -> Lease:
        lease = self._lease(task_id)
        lease.expires_at = self._clock() + self.lease_ttl_s
        return lease

    def release(self, task_id: str) -> Lease:
        """End a lease normally (task finished, whatever its outcome)."""
        lease = self._lease(task_id)
        self._end(task_id)
        self.counters["released"] += 1
        return lease

    def lease(self, task_id: str) -> Optional[Lease]:
        return self._leases.get(task_id)

    def leases_of(self, worker_id: str) -> List[str]:
        self._worker(worker_id)
        return sorted(self._by_worker[worker_id])

    # ---------------------------------------------------------------- reports
    def saturation(self, pool: Optional[str] = None) -> Optional[float]:
        ws = [w for w in self._workers.values() if w.state == "ready" and (pool is None or w.pool == pool)]
        cap = sum(w.capacity for w in ws)
        return None if cap == 0 else round(sum(w.in_flight for w in ws) / cap, 4)

    def snapshot(self) -> dict:
        """Counts only — no task id, tenant or prompt (safe for /metrics)."""
        states: Dict[str, int] = {s: 0 for s in IDENTITY_STATES}
        for a in self._agents.values():
            states[a.state] += 1
        wstates: Dict[str, int] = {s: 0 for s in WORKER_STATES}
        for w in self._workers.values():
            wstates[w.state] += 1
        return {
            "agents": len(self._agents), "agent_states": states,
            "specializations": len([s for s, ids in self._by_spec.items() if ids]),
            "workers": len(self._workers), "worker_states": wstates,
            "capacity_ready": sum(w.capacity for w in self._workers.values() if w.state == "ready"),
            "leases_active": len(self._leases),
            "saturation": self.saturation(),
            "counters": dict(self.counters),
        }

    # ---------------------------------------------------------------- private
    def _get(self, agent_id: str) -> AgentIdentity:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise UnknownAgent(f"unknown agent: {agent_id}") from None

    def _worker(self, worker_id: str) -> Worker:
        try:
            return self._workers[worker_id]
        except KeyError:
            raise UnknownWorker(f"unknown worker: {worker_id}") from None

    def _lease(self, task_id: str) -> Lease:
        try:
            return self._leases[task_id]
        except KeyError:
            raise LeaseError(f"no active lease for task: {task_id}") from None

    def _transition(self, worker_id: str, new: str) -> None:
        w = self._worker(worker_id)
        if new not in _WORKER_TRANSITIONS[w.state]:
            raise InvalidTransition(f"worker {worker_id}: {w.state} -> {new} is not allowed")
        w.state = new

    def _push(self, spec: str, agent_id: str) -> None:
        self._seq += 1
        heap = self._heap.setdefault(spec, [])
        heapq.heappush(heap, (self._load[agent_id], self._seq, agent_id))
        active = self._by_spec.get(spec, ())
        if len(heap) > 4 * len(active) + 64:     # bound the stale entries: rebuild from the truth
            fresh = []
            for aid in active:
                self._seq += 1
                fresh.append((self._load[aid], self._seq, aid))
            heapq.heapify(fresh)
            self._heap[spec] = fresh

    def _end(self, task_id: str) -> None:
        lease = self._leases.pop(task_id)
        self._by_worker.get(lease.worker_id, set()).discard(task_id)
        w = self._workers.get(lease.worker_id)
        if w is not None:
            w.in_flight -= 1
        self._load[lease.agent_id] -= 1
        ident = self._agents[lease.agent_id]
        if ident.state == "active":
            self._push(ident.specialization, lease.agent_id)

    def _orphan_all(self, worker_id: str, reason: str) -> Orphans:
        tids = sorted(self._by_worker[worker_id])
        for tid in tids:
            self._end(tid)
        self.counters["orphaned"] += len(tids)
        return Orphans(worker_id, reason, tids)


def register_civilization(registry: AgentRegistry, civilization: Dict[str, list], *,
                          version: str, capabilities: Optional[Dict[str, str]] = None) -> int:
    """Register every executor of a civilization as an identity (agent_id =
    executor name, unique; an executor without a name gets
    ``<specialization>#<index>``). Returns the number registered."""
    n = 0
    for spec, agents in civilization.items():
        cap = (capabilities or {}).get(spec)
        for i, a in enumerate(agents):
            aid = executor_id(a, spec, i)
            registry.register_agent(aid, aid, spec, version=version,
                                    capabilities=(cap,) if cap else ())
            n += 1
    return n


def executor_id(agent, specialization: str, index: int) -> str:
    name = getattr(agent, "name", None)
    return name if isinstance(name, str) and name.strip() else f"{specialization}#{index}"


__all__ = [
    "AgentIdentity", "AgentRegistry", "DuplicateRegistration", "IDENTITY_STATES", "InvalidTransition",
    "Lease", "LeaseError", "NotAssignable", "Orphans", "RegistryError", "Saturated", "UnknownAgent",
    "UnknownWorker", "WORKER_STATES", "Worker", "executor_id", "positive_int_env", "register_civilization",
]
