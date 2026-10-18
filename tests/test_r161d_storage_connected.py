"""R-161d (G-161-7): ``connected`` of the storage target means "the store
really answered over HTTP" — the same definition the llm/external targets use
(an HTTP status, an empty or a malformed answer all count; a refused or
timed-out connection does not).

Before R-161d the storage probe set ``connected`` only when a check had
*succeeded*. The live run 39082180770 showed the gap: the project answered
HTTP 400 to every tenant-scoped operation and its cleanup ``DELETE`` answered
HTTP 400 too; ``connected`` was True only because the (then false-positive)
``update_task`` check passed. With that check fixed (R-161c) the same run would
have reported ``connected=False`` although the server had answered every
request.

Evidence class of THESE tests: mock — fakes on 127.0.0.1, ``simulated=True``.
Nothing here leaves loopback (tests/conftest.py offline guard).
"""
import json

from aiohttp import web

from tests import test_r161_live_integration as T
from tests.test_r161_live_integration import R, SECRET, _r, _serve, _port

from agent_civilization.storage import supabase_store as st


class NoTenantColumnPostgREST(T.FakePostgREST):
    """Like the project of run 39082180770: 001 applied, 002 not — every
    request that names ``tenant_id`` (filter or body) answers HTTP 400; a
    PATCH by ``task_id`` matches nothing (the insert never happened)."""

    async def handle(self, request):
        table = request.match_info["table"]
        q = dict(request.query)
        body = await request.read()
        self.seen.append((request.method, table, q, request.headers.get("apikey")))
        names_tenant = "tenant_id" in q or b"tenant_id" in body \
            or "tenant_id" in q.get("select", "")
        if names_tenant:
            return web.json_response(
                {"code": "42703", "message": f"column tasks.tenant_id does not exist {SECRET}"},
                status=400)
        if request.method == "PATCH":
            return web.json_response([])           # return=representation, 0 rows
        if request.method == "GET":
            return web.json_response([])
        return web.Response(status=405)


async def _run_with(fake, timeout="5"):
    runner, base = await _serve(fake.handle, "/rest/v1/{table}")
    try:
        env = {"R161_STORAGE_URL": base, "R161_STORAGE_KEY": SECRET, "R161_TIMEOUT_S": timeout}
        return await R.run(["storage"], env, simulated=True)
    finally:
        await runner.cleanup()


# ------------------------------------------------- the gap of 39082180770

async def test_server_answering_400_everywhere_is_connected_but_fails():
    """The run-39082180770 shape: every request answered (400 / 200 []),
    no check passes -> FAIL, and the report says the store DID answer."""
    fake = NoTenantColumnPostgREST()
    rep = await _run_with(fake)
    r = _r(rep, "storage")
    assert fake.seen, "the fake must have been reached"
    assert all(not c["ok"] for c in r["checks"] if c["name"] != "other tenant cannot read the row"), r
    assert r["outcome"] == "FAIL" and r["reason"].startswith("check failed: insert_task accepted"), r
    assert r["connected"] is True, r
    assert r["cleanup"]["ok"] is False and r["cleanup"]["detail"] == "DELETE events -> HTTP 400"
    assert rep["exit_code"] == 1


async def test_auth_rejection_is_connected_but_fails():
    """401 on everything (bad key) is an answer, not a missing connection."""
    rep = await _run_with(T.FakePostgREST("reject"))
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["connected"] is True, r


async def test_malformed_answer_is_connected():
    rep = await _run_with(T.FakePostgREST("malformed"))
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["connected"] is True, r


# --------------------------------------------- no answer -> not connected

async def test_connection_refused_is_not_connected():
    env = {"R161_STORAGE_URL": f"http://127.0.0.1:{_port()}", "R161_STORAGE_KEY": SECRET,
           "R161_TIMEOUT_S": "3"}
    r = _r(await R.run(["storage"], env, simulated=True), "storage")
    assert r["outcome"] == "FAIL" and r["connected"] is False, r


async def test_timeout_without_any_answer_is_not_connected():
    """The fake answers after HANG_S (4 s) > the 1 s deadline of the probe and
    of the cleanup: no HTTP answer was ever received."""
    rep = await _run_with(T.FakePostgREST("hang"), timeout="1")
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and "timeout after 1s" in r["reason"]
    assert r["connected"] is False, r


async def test_cleanup_answer_alone_counts_as_connected():
    """If the probe steps got no answer (timeout) but the cleanup's DELETE was
    answered with an HTTP status, the store did answer."""
    class SlowWritesFastDelete(T.FakePostgREST):
        async def handle(self, request):
            if request.method == "DELETE":
                return web.json_response({"message": "denied"}, status=403)
            import asyncio
            await asyncio.sleep(T.HANG_S)
            return web.Response(status=201)
    rep = await _run_with(SlowWritesFastDelete(), timeout="1")
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["cleanup"]["detail"] == "DELETE events -> HTTP 403"
    assert r["connected"] is True, r


async def test_step_answers_alone_count_as_connected():
    """The steps are answered (HTTP 400) but the cleanup gets no answer at
    all (its DELETE hangs past the deadline): the store still answered —
    proven by the operation outcomes recorded during the probe alone."""
    class FastRejectHangingDelete(T.FakePostgREST):
        async def handle(self, request):
            if request.method == "DELETE":
                import asyncio
                await asyncio.sleep(T.HANG_S)
                return web.json_response([])
            return web.json_response({"message": "bad request"}, status=400)
    rep = await _run_with(FastRejectHangingDelete(), timeout="1")
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["reason"].startswith("check failed: insert_task accepted"), r
    assert "HTTP" not in r["cleanup"]["detail"] and r["cleanup"]["ok"] is False, r
    assert r["connected"] is True, r


# ------------------------------------------------------- no regression

async def test_passing_probe_is_still_connected_and_passes():
    rep = await _run_with(T.FakePostgREST())
    r = _r(rep, "storage")
    assert r["outcome"] == "PASS" and r["connected"] is True and rep["exit_code"] == 0, r


async def test_observer_is_removed_after_the_probe():
    """The probe observes storage outcomes only while it runs; afterwards the
    module's recorder is the original function again (no global side effect),
    also after a failing run."""
    original = st._record
    await _run_with(NoTenantColumnPostgREST())
    assert st._record is original
    await _run_with(T.FakePostgREST())
    assert st._record is original


async def test_report_keeps_no_backend_body_or_secret():
    """The 400 body above echoes the key; nothing of it may reach the report."""
    rep = await _run_with(NoTenantColumnPostgREST())
    text = json.dumps(rep)
    assert SECRET not in text and "does not exist" not in text and "42703" not in text
