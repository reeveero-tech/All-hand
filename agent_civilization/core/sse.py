"""SEC-04 (R-144): in-process tenant event broadcast for SSE, with bounds.

Before R-144 each tenant had ONE unbounded ``asyncio.Queue`` shared by all of
its SSE connections: a published event was consumed by a single listener
(the others never saw it), and events piled up with no listener at all.

Model now:
  * ``EventBus.publish(tenant, event)`` hands a copy of the event to every
    current subscription of that tenant only — synchronously, it never awaits,
    so a slow listener cannot block the publisher (task execution) or others.
  * No subscriber -> the event is not DELIVERED anywhere; since G-160-5
    (D-109) it is still kept in a small per-tenant REPLAY RING (below).
  * RESUME (G-160-5, D-109): every published event gets an SSE ``id``
    ``<epoch>-<generation>-<seq>`` — ``epoch`` is random per bus (per
    process), ``generation`` is unique per tenant log (a tenant log evicted
    from the bounded LRU comes back with a new generation), ``seq`` is
    contiguous per tenant log. A reconnect carrying ``Last-Event-ID``
    replays, before any live event, exactly the ring events with a larger
    ``seq`` — no loss and no duplicate while the ring still holds them. If
    older events after that id were evicted from the ring, a ``stream_gap``
    with the EXACT number missing precedes the replay. An id this bus cannot
    place (other process/worker, restart, evicted tenant log, malformed,
    future) yields one ``stream_reset`` event: recover from the REST routes.
    Bounds: ``replay_size`` events per tenant (``AGOS_SSE_REPLAY_SIZE``,
    0 = no replay, ids still sent) x ``MAX_REPLAY_TENANTS`` tenant logs.
    Process memory only: nothing survives a restart or crosses workers.
  * Each subscription (= one SSE connection) has its own bounded queue
    (``queue_size``). Full queue -> the OLDEST event is dropped to make room
    for the newest; the drop is counted (per subscription and globally) and
    the listener receives one synthetic ``stream_gap`` event
    ``{"type": "stream_gap", "missed": n, "recover": [...]}`` before the
    remaining events, telling it to re-read state.
  * Admission (``open``) enforces, before the stream starts: an open-rate per
    client (sliding window, bounded tracking map), total connections, per
    tenant and per client connections. Refusal -> ``StreamLimit`` (the route
    answers 429 with a fixed message and ``Retry-After``).
  * ``close`` is idempotent; empty per-tenant / per-client entries are removed.

  SHARED ADMISSION (log 299 residual, fixed with ``sse_admission``): when the
  bus is constructed with an ``admission`` store, the caps and the open-rate
  window are enforced by ONE shared lease per subscription (host-local SQLite
  — the pattern of log 303) instead of the process-local maps above. The
  local maps then only track subscriptions for EVENT DELIVERY and metrics;
  the lease is released on ``close`` and renewed by an independent periodic
  loop (``sse_admission.renewal_loop``), so a killed worker's capacity frees
  at lease expiry. A store outage refuses admission (fail-closed). Without a
  store the behavior is exactly the previous in-process one (the server
  always passes a store).
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
import re
import secrets
from typing import Any, Callable, Deque, Dict, Optional, Set

from agent_civilization.core.sse_admission import (
    SSEAdmissionRefused,
    SSELeaseStore,
)

logger = logging.getLogger("agos.sse")

GAP_RECOVER = ["/api/tasks", "/api/events"]
MAX_TRACKED_CLIENTS = 10_000      # hard bound of the open-rate tracking map
MAX_REPLAY_TENANTS = 1_000        # hard bound of the per-tenant replay logs (LRU)
MAX_LAST_EVENT_ID_LEN = 64


class SSEEvent(dict):
    """A delivered bus event: the payload is the dict itself (unchanged
    contract), the SSE id travels OUT OF BAND in ``sse_id`` so no consumer
    ever sees an extra key. ``dict(ev)`` drops the id (a plain copy)."""
    __slots__ = ("sse_id",)

    def __init__(self, payload: Dict[str, Any], sse_id: str):
        super().__init__(payload)
        self.sse_id = sse_id


_EVENT_ID_RE = re.compile(r"^([0-9a-f]{8})-([1-9][0-9]{0,11})-([1-9][0-9]{0,14})$")
CAPACITY_RETRY_AFTER = 30         # seconds; when a slot frees is unknown

# name: (env var, default, min, max)
_LIMITS = {
    "queue_size":      ("AGOS_SSE_QUEUE_SIZE", 100, 1, 10_000),
    "max_connections": ("AGOS_SSE_MAX_CONNECTIONS", 100, 1, 10_000),
    "max_per_tenant":  ("AGOS_SSE_MAX_PER_TENANT", 10, 1, 10_000),
    "max_per_client":  ("AGOS_SSE_MAX_PER_CLIENT", 5, 1, 10_000),
    "open_rate":       ("AGOS_SSE_OPEN_RATE", 10, 1, 10_000),
    "open_window_s":   ("AGOS_SSE_OPEN_WINDOW", 60, 1, 3_600),
    "max_lifetime_s":  ("AGOS_SSE_MAX_LIFETIME", 3600, 10, 86_400),
    "heartbeat_s":     ("AGOS_SSE_HEARTBEAT", 30, 1, 300),
    "lease_ttl_s":     ("AGOS_SSE_LEASE_TTL", 90, 1, 86_400),
    "lease_renewal_s": ("AGOS_SSE_LEASE_RENEWAL", 30, 1, 3_600),
    "replay_size":     ("AGOS_SSE_REPLAY_SIZE", 100, 0, 10_000),
}


@dataclass(frozen=True)
class SSELimits:
    queue_size: int = 100
    max_connections: int = 100
    max_per_tenant: int = 10
    max_per_client: int = 5
    open_rate: int = 10
    open_window_s: int = 60
    max_lifetime_s: int = 3600
    heartbeat_s: int = 30
    lease_ttl_s: int = 90
    lease_renewal_s: int = 30
    replay_size: int = 100

    def __post_init__(self):
        for name, (var, _d, lo, hi) in _LIMITS.items():
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"{var} must be an integer in [{lo}, {hi}], got {v!r}")
        for name in ("max_per_tenant", "max_per_client"):
            if getattr(self, name) > self.max_connections:
                raise ValueError(f"{_LIMITS[name][0]} must not exceed AGOS_SSE_MAX_CONNECTIONS")
        # The renewal interval must be much shorter than the lease TTL, or a
        # single missed renewal could expire a live stream's lease (and the
        # renewal loop's outage bound would be meaningless).
        if self.lease_renewal_s >= self.lease_ttl_s:
            raise ValueError(
                "AGOS_SSE_LEASE_RENEWAL must be smaller than AGOS_SSE_LEASE_TTL "
                f"(got {self.lease_renewal_s} >= {self.lease_ttl_s})")

    @classmethod
    def from_env(cls, env=None) -> "SSELimits":
        """Read the limits from the environment; an invalid value (not an
        integer, zero, negative, out of range) is a startup error — never
        silently ignored, never a disabled control."""
        env = os.environ if env is None else env
        values = {}
        for name, (var, default, _lo, _hi) in _LIMITS.items():
            raw = (env.get(var) or "").strip()
            if not raw:
                values[name] = default
                continue
            try:
                values[name] = int(raw)
            except ValueError:
                raise ValueError(f"{var} must be an integer, got {raw!r}") from None
        return cls(**values)


class StreamLimit(Exception):
    """Admission refused. ``retry_after`` in whole seconds (>= 1)."""

    def __init__(self, reason: str, retry_after: int):
        super().__init__(reason)
        self.reason = reason          # internal (logs/metrics) — never sent to the client
        self.retry_after = max(1, int(retry_after))


class Subscription:
    """One SSE connection: its own bounded queue."""

    def __init__(self, bus: "EventBus", tenant: str, client: str, opened_at: float,
                 lease_id: Optional[str] = None):
        self.bus, self.tenant, self.client, self.opened_at = bus, tenant, client, opened_at
        self.lease_id = lease_id          # shared-admission lease (None = in-process)
        self.lease_expires_at = None      # last CONFIRMED expiry (renewal deadline)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=bus.limits.queue_size)
        self.dropped = 0          # total dropped for this connection
        self._preload: Deque[Dict[str, Any]] = deque()   # resume frames, before live
        self._gap = 0             # dropped since the last stream_gap was delivered
        self.closed = False

    def offer(self, event: Dict[str, Any]) -> None:
        """Never blocks: full queue -> drop the oldest, keep the newest."""
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - single-threaded loop
                pass
            self.dropped += 1
            self._gap += 1
            self.bus._on_drop(self)
        self.queue.put_nowait(event)

    async def next(self, timeout: float) -> Optional[Dict[str, Any]]:
        """The next event, a ``stream_gap`` first if events were dropped, or
        None on timeout (heartbeat). Resume frames (stream_reset /
        stream_gap / replayed events) come first, in order."""
        if self._preload:
            return self._preload.popleft()
        if self._gap:
            n, self._gap = self._gap, 0
            return {"type": "stream_gap", "missed": n, "recover": list(GAP_RECOVER)}
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            if self._gap:  # drops while waiting (queue was full and refilled)
                n, self._gap = self._gap, 0
                return {"type": "stream_gap", "missed": n, "recover": list(GAP_RECOVER)}
            return None


class EventBus:
    def __init__(self, limits: Optional[SSELimits] = None, clock: Callable[[], float] = time.monotonic,
                 on_metric: Optional[Callable[[str, float], None]] = None,
                 admission: Optional[SSELeaseStore] = None):
        self.limits = limits or SSELimits()
        self.clock = clock
        self._on_metric = on_metric
        self._admission = admission      # shared caps (None = in-process)
        self._subs: Dict[str, Set[Subscription]] = {}
        self._per_client: Dict[str, int] = {}
        self._opens: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self.stats = {"published": 0, "delivered": 0, "dropped": 0, "no_subscriber": 0,
                      "opened": 0, "closed": 0, "refused_rate": 0, "refused_capacity": 0,
                      "resumed": 0, "replayed": 0, "resume_gap": 0, "resume_reset": 0}
        self.epoch = secrets.token_hex(4)
        self._generation = 0
        # tenant -> [generation, next_seq, ring of (seq, event)]
        self._logs: "OrderedDict[str, list]" = OrderedDict()

    # ------------------------------------------------------------ metrics
    def _metric(self, name: str, value: float = 1) -> None:
        if self._on_metric is not None:
            try:
                self._on_metric(name, value)
            except Exception:  # pragma: no cover - metrics never break the stream
                pass

    def _on_drop(self, sub: Subscription) -> None:
        self.stats["dropped"] += 1
        self._metric("sse_events_dropped_total")
        if sub.dropped == 1 or sub.dropped % 100 == 0:
            logger.warning("sse: slow listener, dropped oldest event(s): %d on this connection "
                           "(queue_size=%d)", sub.dropped, self.limits.queue_size)

    # ------------------------------------------------------------ state
    @property
    def total(self) -> int:
        return sum(len(s) for s in self._subs.values())

    def subscribers(self, tenant: str) -> int:
        return len(self._subs.get(tenant, ()))

    def snapshot(self) -> Dict[str, Any]:
        return {**self.stats, "connections": self.total, "tenants": len(self._subs),
                "clients": len(self._per_client), "tracked_clients": len(self._opens)}

    # ------------------------------------------------------------ publish
    def _tenant_log(self, tenant: str) -> list:
        log = self._logs.get(tenant)
        if log is None:
            while len(self._logs) >= MAX_REPLAY_TENANTS:
                self._logs.popitem(last=False)      # its ids now resolve to stream_reset
            self._generation += 1
            log = self._logs[tenant] = [self._generation, 1,
                                        deque(maxlen=self.limits.replay_size)]
        else:
            self._logs.move_to_end(tenant)
        return log

    def publish(self, tenant: Optional[str], event: Dict[str, Any]) -> int:
        """Copy ``event`` to every subscription of ``tenant``; returns the count.
        The event gets its SSE id and is kept in the tenant's replay ring."""
        if tenant is None:
            return 0
        self.stats["published"] += 1
        log = self._tenant_log(tenant)
        seq = log[1]
        log[1] += 1
        clean = dict(event)
        if self.limits.replay_size:
            log[2].append((seq, clean))
        eid = f"{self.epoch}-{log[0]}-{seq}"
        subs = self._subs.get(tenant)
        if not subs:
            self.stats["no_subscriber"] += 1
            return 0
        for sub in list(subs):
            sub.offer(SSEEvent(clean, eid))
        self.stats["delivered"] += len(subs)
        return len(subs)

    # ------------------------------------------------------------ admission
    def _prune_opens(self, now: float) -> None:
        horizon = now - self.limits.open_window_s
        while self._opens:
            client, stamps = next(iter(self._opens.items()))
            if stamps and stamps[-1] > horizon:
                break
            del self._opens[client]

    def _check_rate(self, client: str, now: float) -> None:
        self._prune_opens(now)
        stamps = self._opens.get(client)
        horizon = now - self.limits.open_window_s
        if stamps is not None:
            while stamps and stamps[0] <= horizon:
                stamps.popleft()
            if len(stamps) >= self.limits.open_rate:
                self.stats["refused_rate"] += 1
                self._metric("sse_refused_total")
                raise StreamLimit("open_rate", math.ceil(stamps[0] + self.limits.open_window_s - now))
            self._opens.move_to_end(client)
        else:
            if len(self._opens) >= MAX_TRACKED_CLIENTS:
                # every tracked client is active within the window: fail closed
                self.stats["refused_rate"] += 1
                self._metric("sse_refused_total")
                oldest = next(iter(self._opens.values()))
                raise StreamLimit("tracking_full", math.ceil(oldest[-1] + self.limits.open_window_s - now))
            stamps = self._opens[client] = deque(maxlen=self.limits.open_rate)
        stamps.append(now)

    @property
    def shared_admission(self) -> bool:
        """True when this bus decides admission through the shared lease
        store (the server route then splits acquire/registry by thread)."""
        return self._admission is not None

    def acquire_lease(self, tenant: str, client: str) -> tuple:
        """The shared-admission store transaction ONLY — no registry
        mutation, so it is SAFE TO RUN OFF THE EVENT LOOP (the server route
        runs it via ``asyncio.to_thread``; the registry add stays on the
        loop — a worker thread must never mutate ``_subs`` while the loop
        iterates it). Returns ``(lease_id, expires_at)``; raises
        ``StreamLimit`` on refusal (rate/capacity) exactly like ``open``.
        """
        lim = self.limits
        try:
            return self._admission.acquire(
                tenant, client,
                max_connections=lim.max_connections,
                max_per_tenant=lim.max_per_tenant,
                max_per_client=lim.max_per_client,
                open_rate=lim.open_rate,
                open_window_s=lim.open_window_s,
                lease_ttl_s=lim.lease_ttl_s)
        except SSEAdmissionRefused as e:
            if e.reason == "open_rate":
                self.stats["refused_rate"] += 1
            else:
                self.stats["refused_capacity"] += 1
            self._metric("sse_refused_total")
            raise StreamLimit(e.reason, e.retry_after_s) from None

    def open(self, tenant: str, client: str,
             lease: Optional[tuple] = None,
             last_event_id: Optional[str] = None) -> Subscription:
        """Admit one connection (counts toward the open-rate even if then
        refused for capacity) and register its subscription.

        With a shared ``admission`` store the caps and the open-rate window
        are decided by ONE lease transaction in the store (see
        ``sse_admission``); the local maps below then only track the
        subscription for delivery.

        ``lease``: a lease PRE-ACQUIRED off-loop via :meth:`acquire_lease`
        (the route's to_thread split: store I/O off-loop, registry
        mutation on-loop). ``None`` acquires here (sync, tests/local)."""
        if self._admission is not None:
            sub = self._open_shared(tenant, client, lease=lease)
            self._resume(sub, last_event_id)
            return sub
        now = self.clock()
        self._check_rate(client, now)
        lim = self.limits
        if (self.total >= lim.max_connections or self.subscribers(tenant) >= lim.max_per_tenant
                or self._per_client.get(client, 0) >= lim.max_per_client):
            self.stats["refused_capacity"] += 1
            self._metric("sse_refused_total")
            raise StreamLimit("capacity", CAPACITY_RETRY_AFTER)
        sub = Subscription(self, tenant, client, now)
        self._subs.setdefault(tenant, set()).add(sub)
        self._per_client[client] = self._per_client.get(client, 0) + 1
        self.stats["opened"] += 1
        self._metric("sse_connections_opened_total")
        self._metric("sse_connections", self.total)
        self._resume(sub, last_event_id)
        return sub

    def _open_shared(self, tenant: str, client: str,
                     lease: Optional[tuple] = None) -> Subscription:
        """Shared-admission path: the store's ONE transaction decides the
        open-rate window and every cap; the local maps are delivery state.
        With ``lease`` given (pre-acquired off-loop) the store is not
        touched here — only the on-loop registry add."""
        if lease is not None:
            lease_id, expires_at = lease
        else:
            lease_id, expires_at = self.acquire_lease(tenant, client)
        sub = Subscription(self, tenant, client, self.clock(), lease_id=lease_id)
        sub.lease_expires_at = expires_at   # the renewal deadline (from the store)
        self._subs.setdefault(tenant, set()).add(sub)
        self._per_client[client] = self._per_client.get(client, 0) + 1
        self.stats["opened"] += 1
        self._metric("sse_connections_opened_total")
        self._metric("sse_connections", self.total)
        return sub

    def _resume(self, sub: Subscription, last_event_id: Optional[str]) -> None:
        """Queue the resume frames for ``sub`` (called in the SAME synchronous
        step that registered it, so no publish can fall between the replay
        snapshot and live delivery — no loss, no duplicate)."""
        if last_event_id is None:
            return
        self.stats["resumed"] += 1
        m = _EVENT_ID_RE.match(last_event_id) if len(last_event_id) <= MAX_LAST_EVENT_ID_LEN \
            else None
        log = self._logs.get(sub.tenant)
        if (m is None or m.group(1) != self.epoch or log is None
                or int(m.group(2)) != log[0] or int(m.group(3)) >= log[1]):
            self.stats["resume_reset"] += 1
            sub._preload.append({"type": "stream_reset", "reason": "unknown_last_event_id",
                                 "recover": list(GAP_RECOVER)})
            return
        last = int(m.group(3))
        gen, next_seq, ring = log
        oldest = ring[0][0] if ring else next_seq
        missed = max(0, oldest - last - 1)
        if missed:
            self.stats["resume_gap"] += 1
            sub._preload.append({"type": "stream_gap", "missed": missed,
                                 "recover": list(GAP_RECOVER)})
        for seq, ev in ring:
            if seq > last:
                sub._preload.append(SSEEvent(ev, f"{self.epoch}-{gen}-{seq}"))
                self.stats["replayed"] += 1

    def detach(self, sub: Subscription) -> bool:
        """Remove the subscription from the delivery registry (ON-LOOP:
        registry mutation only, no store I/O — safe in the loss path,
        where the response must be cancelled BEFORE any SQLite wait).
        Idempotent: True only the first time."""
        if sub.closed:
            return False
        sub.closed = True
        return self._unregister(sub)

    def release_lease(self, sub: Subscription) -> None:
        """The store release ONLY (run OFF the loop via to_thread; the TTL
        reclaims the capacity if this fails). Idempotent at the store."""
        if sub.lease_id is not None and self._admission is not None:
            try:
                self._admission.release(sub.lease_id)
            except Exception as exc:  # noqa: BLE001 — type only, per R-149
                logger.warning(
                    "sse: lease release failed (%s) — the TTL will reclaim "
                    "the capacity", type(exc).__name__)

    def close(self, sub: Subscription) -> bool:
        """Release the subscription and its slots (sync, whole: detach +
        store release — tests/local paths). Idempotent: True only the
        first time."""
        if sub.closed:
            return False
        sub.closed = True
        self.release_lease(sub)
        self._unregister(sub)
        return True

    def _unregister(self, sub: Subscription) -> bool:
        if not sub.closed:
            sub.closed = True
        subs = self._subs.get(sub.tenant)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.tenant]
        n = self._per_client.get(sub.client, 0) - 1
        if n > 0:
            self._per_client[sub.client] = n
        else:
            self._per_client.pop(sub.client, None)
        self.stats["closed"] += 1
        self._metric("sse_connections", self.total)
        if sub.dropped:
            logger.warning("sse: connection closed after dropping %d event(s)", sub.dropped)
        return True

    def lifetime_left(self, sub: Subscription) -> float:
        return sub.opened_at + self.limits.max_lifetime_s - self.clock()
