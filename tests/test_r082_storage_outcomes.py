"""R-082 (RISK-013): runtime storage failures are visible in /health and /metrics.

Before R-082 (docs/audit/M6/logs/58) a Supabase outage or a revoked key after
startup left /health storage "healthy" and /metrics silent — failures only
reached the log. A fake PostgREST on 127.0.0.1 drives the real SupabaseStorage.
"""
import socket

from aiohttp import web
from fastapi.testclient import TestClient

from agent_civilization.storage import supabase_store as st
from agos_kernel.infrastructure.health import check_storage
from agos_kernel.infrastructure.metrics import MetricsCollector

SECRET_KEY = "sb_secret_" + "k" * 24
SECRET_BODY = "row-level-detail-should-never-be-kept"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _fake_postgrest(status_box):
    async def handler(request):
        await request.read()
        code = status_box["code"]
        if code >= 400:
            return web.json_response({"message": SECRET_BODY}, status=code)
        if request.method == "PATCH":
            return web.Response(status=204)
        return web.json_response([], status=201 if request.method == "POST" else 200)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, port


def _configure(monkeypatch, url):
    monkeypatch.setattr(st, "SUPABASE_URL", url)
    monkeypatch.setattr(st, "SUPABASE_KEY", SECRET_KEY)
    monkeypatch.setattr(st, "SUPABASE_KEY_SOURCE", "SUPABASE_SERVICE_KEY")


async def test_outcomes_recorded_for_http_ok_http_error_and_exception(monkeypatch):
    box = {"code": 201}
    runner, port = await _fake_postgrest(box)
    try:
        _configure(monkeypatch, f"http://127.0.0.1:{port}")
        s = st.SupabaseStorage()
        assert await s.insert_task("t1", "analyst", "p") is True
        assert st.storage_stats()["last_ok"] is True
        box["code"] = 401
        assert await s.insert_task("t2", "analyst", "p") is False      # layer contract unchanged
        assert await s.get_tasks() == []
        stats = st.storage_stats()
        assert stats["last_ok"] is False
        assert stats["last_error"]["operation"] == "get_tasks"
        assert stats["last_error"]["reason"] == "HTTP 401"
        assert stats["operations"]["insert_task"] == {"ok": 1, "failed": 1}
        await s.close()
    finally:
        await runner.cleanup()
    # nothing listens any more -> connection refused
    s2 = st.SupabaseStorage()
    assert await s2.insert_event("e", {"a": 1}) is False
    err = st.storage_stats()["last_error"]
    assert err["operation"] == "insert_event" and err["reason"].startswith("exception: ")
    await s2.close()


async def test_stats_never_keep_keys_bodies_or_urls(monkeypatch):
    box = {"code": 500}
    runner, port = await _fake_postgrest(box)
    try:
        _configure(monkeypatch, f"http://127.0.0.1:{port}")
        s = st.SupabaseStorage()
        await s.update_task("t1", "failed", result={"secret": SECRET_BODY})
        await s.close()
    finally:
        await runner.cleanup()
    text = repr(st.storage_stats())
    assert SECRET_KEY not in text and SECRET_BODY not in text and str(port) not in text
    assert st.storage_stats()["last_error"]["reason"] == "HTTP 500"


async def test_health_degraded_on_last_failure_and_recovers(monkeypatch):
    box = {"code": 401}
    runner, port = await _fake_postgrest(box)
    try:
        _configure(monkeypatch, f"http://127.0.0.1:{port}")
        s = st.SupabaseStorage()
        assert (await check_storage())["status"] == "healthy"         # no operation yet
        await s.insert_task("t1", "analyst", "p")
        res = await check_storage()
        assert res["status"] == "degraded"
        assert "insert_task" in res["message"] and "HTTP 401" in res["message"]
        assert res["total_failed"] == 1
        box["code"] = 201
        await s.insert_task("t2", "analyst", "p")
        res = await check_storage()
        assert res["status"] == "healthy" and res["last_ok"] is True
        assert res["last_error"]["reason"] == "HTTP 401"              # kept for diagnosis
        await s.close()
    finally:
        await runner.cleanup()


async def test_public_key_message_still_wins(monkeypatch):
    _configure(monkeypatch, "http://127.0.0.1:9")
    monkeypatch.setattr(st, "SUPABASE_KEY", "sb_publishable_x")
    st._record("insert_task", False, "HTTP 401")
    res = await check_storage()
    assert res["status"] == "degraded" and "public key" in res["message"]


async def test_unconfigured_storage_unchanged(monkeypatch):
    monkeypatch.setattr(st, "SUPABASE_URL", "")
    st._record("insert_task", False, "HTTP 401")  # even with a recorded failure
    res = await check_storage()
    assert res["status"] == "degraded" and res["message"] == "Using in-memory storage"


def test_gauges_keep_one_series_per_label_set():
    m = MetricsCollector(namespace="t82")
    m.set_gauge("agents_active", 1, {"specialization": "a"})
    m.set_gauge("agents_active", 2, {"specialization": "b"})
    m.set_gauge("plain", 7)
    out = m.export_prometheus().splitlines()
    assert 't82_agents_active{specialization="a"} 1' in out
    assert 't82_agents_active{specialization="b"} 2' in out
    assert "t82_plain 7" in out
    assert sum(1 for ln in out if ln == "# TYPE t82_agents_active gauge") == 1


def test_labelled_counters_keep_their_series():
    m = MetricsCollector(namespace="t82c")
    m.inc_counter("hits", labels={"route": "/a"})
    m.inc_counter("hits", 2, labels={"route": "/b"})
    out = m.export_prometheus().splitlines()
    assert 't82c_hits{route="/a"} 1.0' in out and 't82c_hits{route="/b"} 2.0' in out


def test_metrics_endpoint_exports_all_specializations_and_storage(monkeypatch):
    import server
    _configure(monkeypatch, "http://127.0.0.1:9")
    st._record("insert_task", True)
    st._record("get_tasks", False, "HTTP 503")
    with TestClient(server.app) as c:
        text = c.get("/metrics").text
    lines = text.splitlines()
    assert sum(1 for ln in lines if ln.startswith("agos_agents_active{")) == len(server.CIVILIZATION)
    assert 'agos_storage_operations{operation="get_tasks",outcome="failed"} 1' in lines
    assert 'agos_storage_operations{operation="insert_task",outcome="ok"} 1' in lines
    assert "agos_storage_last_operation_ok 0" in lines
