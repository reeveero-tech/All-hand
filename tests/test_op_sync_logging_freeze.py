"""Operational risk (log 294 §72, recorded unmitigated): synchronous logging.

The server configures logging with ``logging.basicConfig`` (server.py) and the
telemetry middleware writes one INFO record per request from inside the event
loop. If the log destination is slow or blocked (a full pipe, a stalled
collector), ``Handler.emit`` runs in the loop thread and the whole server
freezes — no request completes, health checks included.

Contract under test: a blocked log destination must NOT freeze the request
path, and records produced during the block are delivered once it clears.

Red-first: on the synchronous-logging tree both tests fail (the request thread
is frozen by the blocked destination). With the queue-based logger they pass
(records queue; the drain thread blocks, the loop does not).

Note on the INFO level: pytest attaches a capturing handler to the root logger
before the app is imported, so ``logging.basicConfig`` is a no-op under pytest
and the root level stays WARNING — telemetry INFO records would never be
emitted. The fixture therefore sets the root level to INFO for the duration of
the test (restored afterwards); without it this test would silently test
nothing.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import httpx
import pytest


class _BlockedDestination(logging.Handler):
    """A log destination that blocks until released (a full/slow pipe).

    Records received after the block clears are kept in ``seen``.
    """

    def __init__(self, release: threading.Event) -> None:
        super().__init__(level=logging.INFO)
        self._release = release
        self.seen: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: N802 — logging API
        self._release.wait()
        self.seen.append(record.getMessage())


def _destinations_owner(server_module):
    """The object whose ``.handlers`` are the real log destinations.

    Pre-fix: the root logger (synchronous destinations).
    Post-fix: ``server._LOG_LISTENER`` owns them behind a queue.
    """
    listener = getattr(server_module, "_LOG_LISTENER", None)
    if listener is not None:
        return listener
    return logging.getLogger()


@pytest.fixture
def blocked_log_destination():
    """Install a blocked destination in place of the real log destinations.

    Also forces the root level to INFO so the request actually emits telemetry
    records (see the module docstring) — restored on teardown.
    """
    import server

    owner = _destinations_owner(server)
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(owner.handlers)
    root.setLevel(logging.INFO)
    release = threading.Event()
    blocked = _BlockedDestination(release)
    owner.handlers = [blocked]
    try:
        yield blocked
    finally:
        # restore first, then unblock, so the drained backlog goes to the real
        # destinations and the drain thread (if any) never sees the block again
        owner.handlers = saved_handlers
        root.setLevel(saved_level)
        release.set()


def _request_in_worker_thread(server_module, box: list) -> None:
    """Run one request in its own loop/thread so the test can time it out.

    If the destination blocks the loop, this thread never finishes — the join
    timeout is the verdict, so the test itself never hangs.
    """

    async def run() -> None:
        transport = httpx.ASGITransport(app=server_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/agents")
            box.append(r.status_code)

    asyncio.run(run())


def test_blocked_log_destination_does_not_freeze_requests(blocked_log_destination):
    """The core contract: requests complete while the log destination is blocked."""
    import server

    box: list = []
    t = threading.Thread(target=_request_in_worker_thread, args=(server, box), daemon=True)
    t.start()
    t.join(timeout=5.0)
    finished = not t.is_alive()
    if not finished:
        pytest.fail(
            "the request never completed: a blocked log destination froze the "
            "server event loop (synchronous logging from inside the loop)"
        )
    assert box, "worker thread finished without a response"
    assert box[0] in (200, 401, 403), f"unexpected status {box[0]}"


def test_log_records_survive_a_blocked_destination(blocked_log_destination):
    """Records produced during the block are delivered once it clears."""
    import server

    box: list = []
    t = threading.Thread(target=_request_in_worker_thread, args=(server, box), daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "request frozen by blocked log destination"
    assert box[0] in (200, 401, 403)
    # the request's telemetry record is queued behind the block; unblock and
    # give the drain path a moment to deliver it
    blocked_log_destination._release.set()
    deadline = time.time() + 3.0
    while not blocked_log_destination.seen and time.time() < deadline:
        time.sleep(0.05)
    assert blocked_log_destination.seen, (
        "records produced during the block were not delivered to the "
        "destination after it cleared (lost backlog)"
    )
