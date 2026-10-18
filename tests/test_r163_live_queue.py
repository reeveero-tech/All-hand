"""R-163: the durable queue operations against LIVE PostgREST (Supabase engine).

Skipped unless R163_LIVE=1 and SUPABASE_URL/SUPABASE_SERVICE_KEY point at a
live endpoint with docs/sql/001_init.sql + 002_tenant_isolation.sql +
003_task_queue.sql applied (CI job `storage` — scripts/r042/run_local_stack.sh
applies the three, or a real Supabase project).

Covers what the fake-backed unit suite cannot: the real HTTP round-trip and
the real SQL semantics of every queue operation — the conditional PATCH as an
atomic fence, ``Prefer: return=representation``, the ``in./lt.`` filters, and
the unique ``(tenant_id, idempotency_key)`` index (409 on duplicates).

Harness note: aiohttp sessions are bound to the event loop that created them,
and the storage singleton is shared across tests — so each test runs its whole
scenario inside ONE ``asyncio.run`` and closes the session inside that same
loop before returning (the first CI run of this suite failed exactly that
way: a fresh ``asyncio.run`` per storage call reused a dead loop's session and
raised RuntimeError).
"""
import asyncio
import os

import pytest

LIVE = os.environ.get("R163_LIVE") == "1" and bool(os.environ.get("SUPABASE_URL")) \
    and bool(os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))

pytestmark = [
    pytest.mark.skipif(not LIVE, reason="R-163 live queue not configured (set R163_LIVE=1)"),
    # may target a hosted Supabase project — exempt from the offline guard.
    pytest.mark.allow_external_network,
]

_TENANT = "r163_live_tenant"


def _storage():
    from agent_civilization.storage import get_storage
    return get_storage()


def _iso(offset_s: float = 0.0) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat(timespec="seconds")


def _cleanup():
    """Delete only this run's tenant rows (the R-042 cleanup pattern)."""
    import urllib.request
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
    req = urllib.request.Request(
        f"{os.environ['SUPABASE_URL']}/rest/v1/tasks?tenant_id=eq.{_TENANT}",
        method="DELETE", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=10):
        pass


def test_r163_live_queue_roundtrip():
    s = _storage()
    _cleanup()
    try:
        asyncio.run(_roundtrip(s))
    finally:
        _cleanup()


async def _roundtrip(s):
    try:
        # insert with the queue columns
        assert await s.insert_task("r163-live-1", "analyst", "p", "queued",
                                   tenant_id=_TENANT, payload={"n": [1, 2]},
                                   idempotency_key="live-key-1", max_attempts=3)
        # duplicate key: the unique (tenant, key) index answers 409
        assert not await s.insert_task("r163-live-2", "analyst", "p", "queued",
                                       tenant_id=_TENANT, idempotency_key="live-key-1")
        # replay finds the SAME task, tenant-scoped
        found = await s.find_task_by_idempotency_key("live-key-1", scope=_TENANT)
        assert found is not None and found["task_id"] == "r163-live-1"
        # claim is atomic: the second claim of a claimed row wins nothing
        row = await s.claim_task("r163-live-1", "tok-live", _iso(60))
        assert row is not None and row["claim_token"] == "tok-live"
        assert await s.claim_task("r163-live-1", "tok-live-b", _iso(60)) is None
        # start counts the attempt; the stale token cannot start it
        assert await s.start_task("r163-live-1", "tok-live-b", 5) is False
        assert await s.start_task("r163-live-1", "tok-live", 1) is True
        # renew extends the lease (fenced)
        assert await s.renew_task_lease("r163-live-1", "tok-live", _iso(120))
        # finish is fenced: the stale token's late result is discarded
        assert await s.finish_task("r163-live-1", "tok-live-b", "completed",
                                   result={"ok": True}) is False
        assert await s.finish_task("r163-live-1", "tok-live", "completed",
                                   agent_name="analyst_007", result={"ok": True}) is True
    finally:
        await s.close()


def test_r163_live_recovery_semantics():
    s = _storage()
    _cleanup()
    try:
        asyncio.run(_recovery(s))
    finally:
        _cleanup()


async def _recovery(s):
    try:
        # two leased rows: one live (future deadline), one dead (past deadline)
        assert await s.insert_task("r163-live-live", "analyst", "p", "queued", tenant_id=_TENANT)
        assert await s.insert_task("r163-live-dead", "analyst", "p", "queued", tenant_id=_TENANT)
        assert await s.claim_task("r163-live-live", "tok-a", _iso(3600)) is not None
        assert await s.claim_task("r163-live-dead", "tok-b", _iso(-3600)) is not None
        await s.start_task("r163-live-live", "tok-a", 1)
        await s.start_task("r163-live-dead", "tok-b", 1)
        expired = await s.list_expired_lease_tasks()
        assert [r["task_id"] for r in expired] == ["r163-live-dead"]
        # recovery never steals the live lease (lt. filter)
        assert await s.transition_task("r163-live-dead", ("claimed", "running"), "retrying",
                                       lease_expired_before=_iso()) is True
        assert await s.transition_task("r163-live-dead", ("retrying",), "queued") is True
        live = await s.get_task("r163-live-live", scope=_TENANT)
        assert live["status"] == "running" and live["claim_token"] == "tok-a"
    finally:
        await s.close()
