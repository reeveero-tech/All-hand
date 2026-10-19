"""R-164: the two NEW storage reads against LIVE PostgREST (Supabase engine).

Skipped unless R163_LIVE=1 and SUPABASE_URL/SUPABASE_SERVICE_KEY point at a
live endpoint with docs/sql/001..003 applied (CI job `storage` —
scripts/r042/run_local_stack.sh). The unit suite runs them against a fake;
this proves the real query semantics:

- ``count_pending_tasks`` — the ``status=in.(...)`` filter counts only
  non-terminal rows, per tenant, and ``limit`` bounds the read;
- ``list_queued_tasks(exclude_tenants=...)`` — ``tenant_id=not.in.(...)``
  really leaves those tenants out (the fairness scan).

Same harness rule as test_r163_live_queue.py: one ``asyncio.run`` per test,
the session closed inside that loop.
"""
import asyncio
import os

import pytest

LIVE = os.environ.get("R163_LIVE") == "1" and bool(os.environ.get("SUPABASE_URL")) \
    and bool(os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))

pytestmark = [
    pytest.mark.skipif(not LIVE, reason="R-164 live storage not configured (set R163_LIVE=1)"),
    pytest.mark.allow_external_network,
]

_A, _B = "r164_live_a", "r164_live_b"


def _cleanup():
    import urllib.request
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
    for tenant in (_A, _B):
        req = urllib.request.Request(
            f"{os.environ['SUPABASE_URL']}/rest/v1/tasks?tenant_id=eq.{tenant}",
            method="DELETE", headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=10):
            pass


def test_r164_live_count_and_exclusion_scan():
    from agent_civilization.storage import get_storage
    s = get_storage()
    _cleanup()
    try:
        asyncio.run(_scenario(s))
    finally:
        _cleanup()


async def _scenario(s):
    from agent_civilization.core.tenancy import ALL_TENANTS
    try:
        for i in range(3):
            assert await s.insert_task(f"r164-live-a{i}", "analyst", "p", "queued", tenant_id=_A)
        assert await s.insert_task("r164-live-b0", "analyst", "p", "queued", tenant_id=_B)
        assert await s.insert_task("r164-live-bdone", "analyst", "p", "completed", tenant_id=_B)
        # per-tenant count: non-terminal rows only; limit bounds the read
        assert await s.count_pending_tasks(_A, 100) == 3
        assert await s.count_pending_tasks(_A, 2) == 2
        assert await s.count_pending_tasks(_B, 100) == 1           # the completed row is not pending
        assert await s.count_pending_tasks(ALL_TENANTS, 1000) >= 4
        # the fairness scan: excluded tenants' rows are really left out
        rows = await s.list_queued_tasks(1000, exclude_tenants=[_A])
        ids = {r["task_id"] for r in rows}
        assert "r164-live-b0" in ids and not any(t.startswith("r164-live-a") for t in ids)
        both = await s.list_queued_tasks(1000, exclude_tenants=[_A, _B])
        assert not any(r["task_id"].startswith("r164-live-") for r in both)
    finally:
        await s.close()
