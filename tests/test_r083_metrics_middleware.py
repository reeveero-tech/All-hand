"""R-083: MetricsMiddleware mounted — HTTP requests are visible in /metrics.

Before R-083 (docs/audit/M6/logs/61) `agos_http_requests_total` was always 0,
durations were never exported, the histogram export format was invalid and raw
samples grew without bound.
"""
import asyncio
import re
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server
from agos_kernel.infrastructure.metrics import MetricsCollector, MetricsMiddleware, get_metrics

VALID = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-zA-Z_][a-zA-Z0-9_]*="[^"]*",?)*\})? -?[0-9.e+Inf-]+$')


def _requests(text):
    out = {}
    for ln in text.splitlines():
        if ln.startswith("agos_http_requests_total{"):
            name, val = ln.rsplit(" ", 1)
            out[name] = float(val)
    return out


def test_server_counts_requests_by_route_template(tenant_auth):
    # SEC-02: authenticated, so a missing task id is still the 404 of the route
    with TestClient(server.app, headers=tenant_auth.headers) as c:
        before = _requests(c.get("/metrics").text)
        for i in range(25):
            c.get(f"/api/tasks/id-{i}")
        c.get("/api")
        after = _requests(c.get("/metrics").text)
    tmpl = 'agos_http_requests_total{method="GET",endpoint="/api/tasks/{task_id}",status="404"}'
    assert after[tmpl] - before.get(tmpl, 0) == 25
    api = 'agos_http_requests_total{method="GET",endpoint="/api",status="200"}'
    assert after[api] - before.get(api, 0) == 1
    assert not any("id-" in k for k in after)               # raw values never become labels
    assert not any('endpoint="/metrics"' in k for k in after)  # /metrics itself is skipped


def test_spa_catch_all_does_not_explode_cardinality():
    """Unknown paths collapse into ONE label: `/{full_path}` when web/dist is built
    (SPA catch-all route registered), `<unmatched>` otherwise (CI `test` job)."""
    has_spa = any(getattr(r, "path", "") == "/{full_path:path}" for r in server.app.routes)
    expected = "/{full_path}" if has_spa else "<unmatched>"
    with TestClient(server.app) as c:
        for i in range(30):
            c.get(f"/random/page/{i}")
        text = c.get("/metrics").text
    assert not any("/random/page" in ln for ln in text.splitlines())
    assert f'endpoint="{expected}"' in text


def test_server_metrics_exposition_is_valid():
    with TestClient(server.app) as c:
        c.get("/api"); c.get("/health")
        text = c.get("/metrics").text
    bad = [ln for ln in text.splitlines() if ln and not ln.startswith("#") and not VALID.match(ln)]
    assert bad == []
    assert 'agos_http_request_duration_seconds_count{method="GET",endpoint="/api",status="200"}' in text


def test_histogram_cumulative_per_series_and_bounded():
    m = MetricsCollector(namespace="h83")
    m.observe_histogram("lat", 0.003, {"r": "a"})
    m.observe_histogram("lat", 0.003, {"r": "a"})
    m.observe_histogram("lat", 0.2, {"r": "a"})
    m.observe_histogram("lat", 7, {"r": "b"})
    out = m.export_prometheus().splitlines()
    assert 'h83_lat_bucket{r="a",le="0.005"} 2' in out
    assert 'h83_lat_bucket{r="a",le="0.25"} 3' in out          # cumulative, not double counted
    assert 'h83_lat_bucket{r="a",le="+Inf"} 3' in out
    assert 'h83_lat_count{r="a"} 3' in out
    assert 'h83_lat_bucket{r="b",le="5"} 0' in out and 'h83_lat_bucket{r="b",le="10"} 1' in out
    assert sum(1 for ln in out if ln == "# TYPE h83_lat histogram") == 1
    for _ in range(5000):
        m.observe_histogram("lat", 0.1, {"r": "a"})
    assert len(m._histograms[m._make_key("lat", {"r": "a"})]) == 1024        # raw window bounded
    assert 'h83_lat_count{r="a"} 5003' in m.export_prometheus().splitlines()  # aggregates exact


def test_exception_is_recorded_as_500_and_unmatched_label():
    col = MetricsCollector(namespace="e83")
    app = FastAPI()

    @app.get("/boom/{x}")
    async def boom(x: str):
        raise RuntimeError("boom")

    app.add_middleware(MetricsMiddleware, metrics=col)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/boom/1").status_code == 500
        assert c.get("/nowhere").status_code == 404
    out = col.export_prometheus().splitlines()
    assert 'e83_http_requests_total{method="GET",endpoint="/boom/{x}",status="500"} 1.0' in out
    assert 'e83_http_requests_total{method="GET",endpoint="<unmatched>",status="404"} 1.0' in out


async def test_sse_stream_passes_through_middleware(tenant_auth):
    """Raw ASGI call through the full middleware stack: the first SSE chunk
    arrives while the stream is still open, then a disconnect ends it.
    SEC-02: the stream needs the tenant's key and reads that tenant's events.
    SEC-04 (R-144): events are broadcast to connected streams only (nothing
    is kept for a tenant with no stream), so the event is published once the
    stream has subscribed — before R-144 it was queued in advance."""
    tag = f"r083_{time.time()}"
    got_chunk = asyncio.Event()
    messages = []

    async def receive():
        await got_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        messages.append(msg)
        if msg["type"] == "http.response.body" and tag.encode() in msg.get("body", b""):
            got_chunk.set()

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/api/events/stream", "raw_path": b"/api/events/stream",
             "query_string": b"", "headers": [(b"host", b"testserver"), (b"x-api-key", tenant_auth.key.encode())],
             "client": ("127.0.0.1", 1),
             "server": ("testserver", 80), "root_path": ""}
    key = get_metrics()._make_key("http_requests_total",
                                  {"method": "GET", "endpoint": "/api/events/stream", "status": "200"})
    before = get_metrics()._counters.get(key, 0)
    app_call = asyncio.ensure_future(server.app(scope, receive, send))
    for _ in range(500):
        if server.EVENT_BUS.subscribers(tenant_auth.tenant):
            break
        await asyncio.sleep(0.01)
    await server._publish(tenant_auth.tenant, {"type": tag})
    await asyncio.wait_for(app_call, timeout=5)
    assert server.EVENT_BUS.subscribers(tenant_auth.tenant) == 0   # released on disconnect
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert (b"content-type", b"text/event-stream; charset=utf-8") in start["headers"]
    assert got_chunk.is_set()
    assert get_metrics()._counters.get(key, 0) - before == 1
