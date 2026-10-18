"""R-161c: ``SupabaseStorage.update_task`` succeeds only when the backend
confirms the updated row.

Found by the live run 39082180770 (2026-10-18, log 125): the R-161 runner's
"update_task accepted" check passed against a schema without ``tenant_id``
although nothing was updated — PostgREST answers a PATCH that matches no row
with 204 (or 200 + ``[]``), and ``update_task`` accepted 200/204 alone.

Now the PATCH asks for ``Prefer: return=representation`` + ``select=task_id``
and success requires HTTP 200 with a JSON array holding the row whose
``task_id`` is the target. This file pins:

1. existing task  -> success (the row is returned);
2. missing task   -> failure, also on a bare 204 No Content;
3. server failure or invalid answer -> no success recorded.

Evidence class: mock (aiohttp fakes on 127.0.0.1). The same code against a
real PostgREST is exercised by the CI job ``storage`` (local_integration).
"""
import asyncio
import json
import logging
import pathlib
import sys

import pytest
from aiohttp import web

from agent_civilization.storage import supabase_store as ss

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import r161_live_integration as R  # noqa: E402

KEY = "r161c-SYNTHETIC-KEY-" + "k" * 20        # never a real credential
BODY_MARKER = "r161c-BACKEND-BODY-MARKER"       # must never reach logs/stats
TID = "a1b2c3d4e5f6"


async def _serve(handler):
    app = web.Application()
    app.router.add_route("*", "/rest/v1/{table}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.fixture
def configured(monkeypatch):
    ss.reset_storage_stats()

    def _set(base):
        monkeypatch.setattr(ss, "SUPABASE_URL", base)
        monkeypatch.setattr(ss, "SUPABASE_KEY", KEY)
        monkeypatch.setattr(ss, "SUPABASE_KEY_SOURCE", "SUPABASE_SERVICE_KEY")
    yield _set
    ss.reset_storage_stats()


async def _update(handler, configured, task_id=TID):
    seen = []

    async def recording(request):
        seen.append({"method": request.method, "query": dict(request.query),
                     "prefer": request.headers.get("Prefer", ""),
                     "body": json.loads(await request.read() or b"null")})
        return await handler(request)
    runner, base = await _serve(recording)
    configured(base)
    store = ss.SupabaseStorage()
    try:
        ok = await store.update_task(task_id, "completed", agent_name="a",
                                     result={"ok": True}, elapsed_s=0.1)
    finally:
        await store.close()
        await runner.cleanup()
    return ok, seen, ss.storage_stats()


def _reply(status=200, body=None, text=None):
    async def h(request):
        if text is not None:
            return web.Response(status=status, text=text, content_type="application/json")
        if body is None:
            return web.Response(status=status)
        return web.json_response(body, status=status)
    return h


# ------------------------------------------------ 1. existing task -> success
async def test_existing_task_succeeds_when_the_updated_row_is_returned(configured):
    ok, seen, stats = await _update(_reply(200, [{"task_id": TID}]), configured)
    assert ok is True
    (req,) = seen
    assert req["method"] == "PATCH"
    assert req["query"] == {"task_id": f"eq.{TID}", "select": "task_id"}
    assert "return=representation" in req["prefer"]
    assert req["body"]["status"] == "completed" and req["body"]["result"] == {"ok": True}
    assert stats["operations"]["update_task"] == {"ok": 1, "failed": 0}
    assert stats["last_ok"] is True and stats["last_error"] is None


async def test_integer_like_key_in_the_returned_row_still_matches(configured):
    ok, _, _ = await _update(_reply(200, [{"task_id": 123456}]), configured, task_id="123456")
    assert ok is True


# ----------------------------------------- 2. missing task -> failure (even 204)
@pytest.mark.parametrize("handler,reason", [
    (_reply(204), "HTTP 204"),                         # bare 204 No Content: nothing proves an update
    (_reply(200, []), "no row updated"),               # PostgREST: 0 rows matched
    (_reply(200, [{"task_id": "someoneelse0"}]), "no row updated"),   # a different row
], ids=["204-no-content", "200-empty-list", "200-other-row"])
async def test_missing_task_is_a_failure(configured, handler, reason):
    ok, _, stats = await _update(handler, configured)
    assert ok is False
    assert stats["operations"]["update_task"] == {"ok": 0, "failed": 1}
    assert stats["last_ok"] is False
    assert stats["last_error"]["operation"] == "update_task"
    assert stats["last_error"]["reason"] == reason


# ------------------------- 3. server failure / invalid answer -> no success
@pytest.mark.parametrize("handler,reason", [
    (_reply(500, {"message": BODY_MARKER}), "HTTP 500"),
    (_reply(400, {"code": "42703", "message": f"column tasks.tenant_id does not exist {BODY_MARKER}"}),
     "HTTP 400"),
    (_reply(401, {"message": f"JWT invalid {BODY_MARKER}"}), "HTTP 401"),
    (_reply(201, [{"task_id": TID}]), "HTTP 201"),                 # not the update answer
    (_reply(200, text=f"<html>{BODY_MARKER}</html>"), "malformed response"),   # invalid JSON
    (_reply(200, {"task_id": TID}), "malformed response"),         # an object, not a list
    (_reply(200, [{"id": 1}]), "malformed response"),              # a row without the key
    (_reply(200, [TID]), "malformed response"),                    # not a row object
], ids=["500", "400-missing-column", "401", "201", "invalid-json", "object", "row-without-key", "not-a-row"])
async def test_server_failure_or_invalid_answer_records_no_success(configured, handler, reason, caplog):
    caplog.set_level(logging.DEBUG)
    ok, _, stats = await _update(handler, configured)
    assert ok is False
    assert stats["operations"]["update_task"] == {"ok": 0, "failed": 1}
    assert stats["last_ok"] is False and stats["last_success_at"] is None
    assert stats["last_error"]["reason"] == reason
    blob = json.dumps(stats) + caplog.text
    assert BODY_MARKER not in blob and KEY not in blob        # R-149: no body, no key


async def test_transport_cut_records_no_success(configured):
    async def cut(request):
        request.transport.close()
        await asyncio.sleep(0.2)
        return web.Response(status=200)
    ok, _, stats = await _update(cut, configured)
    assert ok is False
    assert stats["operations"]["update_task"] == {"ok": 0, "failed": 1}
    assert stats["last_error"]["reason"].startswith("exception: ")


async def test_unconfigured_store_sends_nothing_and_records_nothing(monkeypatch):
    ss.reset_storage_stats()
    monkeypatch.setattr(ss, "SUPABASE_URL", "")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "")
    assert await ss.SupabaseStorage().update_task(TID, "completed") is False
    assert ss.storage_stats()["operations"] == {}


# ------------- the runner check: the live run 39082180770, reproduced offline
class _Schema001Only:
    """A PostgREST over docs/sql/001_init.sql without 002_tenant_isolation.sql
    (what run 39082180770 met): any request naming tenant_id -> 400 (42703);
    a PATCH by task_id that matches nothing -> 204, or 200 [] with
    return=representation (PostgREST's documented answers)."""

    def __init__(self):
        self.seen = []

    async def handle(self, request):
        body = await request.read()
        q = dict(request.query)
        self.seen.append((request.method, request.match_info["table"], q))
        row = json.loads(body) if body else {}
        if "tenant_id" in q or (isinstance(row, dict) and "tenant_id" in row):
            return web.json_response({"code": "42703", "message": "column tenant_id does not exist"},
                                     status=400)
        if request.method == "PATCH":
            if "return=representation" in request.headers.get("Prefer", ""):
                return web.json_response([])
            return web.Response(status=204)
        return web.json_response([])


async def test_runner_update_check_fails_when_no_row_exists():
    fake = _Schema001Only()
    runner, base = await _serve(fake.handle)
    try:
        rep = await R.run(["storage"], {"R161_STORAGE_URL": base, "R161_STORAGE_KEY": KEY,
                                        "R161_TIMEOUT_S": "5"}, simulated=True)
    finally:
        await runner.cleanup()
    (r,) = [x for x in rep["results"] if x["target"] == "storage"]
    checks = {c["name"]: c["ok"] for c in r["checks"]}
    assert checks["insert_task accepted"] is False
    assert checks["update_task updated the row"] is False     # was a false "ok" in run 39082180770
    assert checks["get_task returns the row"] is False
    assert r["outcome"] == "FAIL" and r["evidence_class"] == "mock"
    assert "cleanup failed" in r["reason"] and "HTTP 400" in r["reason"]
    assert rep["exit_code"] == 1
    patches = [q for m, t, q in fake.seen if m == "PATCH"]
    assert patches == [{"task_id": f"eq.{r['synthetic']['task_id']}", "select": "task_id"}]
