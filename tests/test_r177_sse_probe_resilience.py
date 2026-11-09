"""R-177 / D-145 — مسبار SSE لا يُسقط تشغيل الأعطال حين يكون الخادم متوقفًا.

السبب الجذري (السجل 397): في سيناريو crash لخدمة api-sse يعمل المسبار
بعد انتهاء حمل k6 والخادم مقتول، فيرفع ConnectionRefusedError غير ملتقط
ويُسقط المشغل كله بلا تقرير. الإصلاح: كل محاولة فاشلة تُسجَّل خطأً صريحًا
والنتيجة not_measured إن لم تنجح أي عينة — لا 0 ولا نجاح.
"""
import pathlib
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import r177_per_service as ps  # noqa: E402


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_probe_does_not_raise_when_server_down():
    port = _closed_port()
    res = ps._sse_ttfb_probe(f"http://127.0.0.1:{port}", "k", probes=2, pause_s=0.0)
    assert res["samples_ms"] == []
    assert res["p95_ms"] is None
    assert res["status"] == "not_measured"
    assert len(res["errors"]) == 2
    assert all("ConnectionRefused" in e["error"] or "Errno" in e["error"] for e in res["errors"])


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

    def log_message(self, *a):
        pass


def test_probe_measures_when_server_up():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        res = ps._sse_ttfb_probe(f"http://127.0.0.1:{srv.server_port}", "k", probes=3, pause_s=0.0)
    finally:
        srv.shutdown()
    assert len(res["samples_ms"]) == 3
    assert res["statuses"] == [200, 200, 200]
    assert res["errors"] == []
    assert res["status"] == "measured"
    assert res["p95_ms"] is not None


def test_partial_failure_is_marked_partial(monkeypatch):
    import http.client
    calls = {"n": 0}
    real = http.client.HTTPConnection.request

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionRefusedError(111, "Connection refused")
        return real(self, *a, **kw)

    srv = HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setattr(http.client.HTTPConnection, "request", flaky)
    try:
        res = ps._sse_ttfb_probe(f"http://127.0.0.1:{srv.server_port}", "k", probes=2, pause_s=0.0)
    finally:
        srv.shutdown()
    assert len(res["samples_ms"]) == 1
    assert len(res["errors"]) == 1
    assert res["status"] == "partial"


def test_probe_ok_never_passes_on_empty_or_errors():
    assert ps._sse_probe_ok({"status": "not_measured", "statuses": [], "errors": [{}]}) is False
    assert ps._sse_probe_ok({"status": "partial", "statuses": [200], "errors": [{}]}) is False
    assert ps._sse_probe_ok({"status": "measured", "statuses": [200, 503], "errors": []}) is False
    assert ps._sse_probe_ok({"status": "measured", "statuses": [], "errors": []}) is False
    assert ps._sse_probe_ok({"status": "measured", "statuses": [200, 200], "errors": []}) is True
