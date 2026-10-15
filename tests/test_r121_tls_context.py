"""R-121 — one shared, verifying TLS context for every outbound HTTPS request.

Root cause measured in docs/audit/M7/logs/raw/133_r121_root_cause.log: without
an explicit context urllib built a new default context per connection and the
CA bundle was parsed on every handshake; the freed memory stayed held by the
allocator (~0.7 MB per request — VmRSS 361 MB after the R-120 hour).
These tests pin that the context is shared AND that verification is unchanged.
"""
import ssl
import urllib.request

from agent_civilization.core import safety


def test_tls_context_is_created_once_and_reused():
    a = safety.tls_context()
    b = safety.tls_context()
    assert a is b and isinstance(a, ssl.SSLContext)


def test_tls_context_still_verifies_certificates_and_hostnames():
    ctx = safety.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_safe_urlopen_passes_the_shared_context(monkeypatch):
    captured = {}

    class _Opener:
        closed = False
        handlers = []

        def open(self, req, timeout):
            return "opened"

        def close(self):
            _Opener.closed = True

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(safety, "check_url", lambda url, allow_private=None: url)
    req = urllib.request.Request("https://example.com/")
    assert safety.safe_urlopen(req, timeout=1) == "opened"
    assert _Opener.closed  # R-124: opener<->handler cycle broken on return
    https = [h for h in captured["handlers"] if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https) == 1 and https[0]._context is safety.tls_context()
    # the redirect guard (R-100) is still installed
    assert any(isinstance(h, safety._CheckedRedirectHandler) for h in captured["handlers"])


def test_r124_no_opener_or_context_survives_a_request_without_gc(monkeypatch):
    """R-124: after safe_urlopen returns, nothing it built waits for the cyclic GC."""
    import gc
    import io

    class _Resp(io.BytesIO):
        pass

    def fake_https_open(self, req):
        return _Resp(b"ok")

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", fake_https_open)
    monkeypatch.setattr(safety, "check_url", lambda url, allow_private=None: url)
    safety.tls_context()
    gc.collect()
    gc.disable()
    try:
        before = sum(isinstance(o, urllib.request.OpenerDirector) for o in gc.get_objects())
        ctx_before = sum(isinstance(o, ssl.SSLContext) for o in gc.get_objects())
        for _ in range(10):
            safety.safe_urlopen(urllib.request.Request("https://example.com/"), timeout=1).read()
        after = sum(isinstance(o, urllib.request.OpenerDirector) for o in gc.get_objects())
        contexts = sum(isinstance(o, ssl.SSLContext) for o in gc.get_objects())
    finally:
        gc.enable()
    assert after == before, (before, after)
    assert contexts == ctx_before  # no new TLS context per request (the shared one already existed)
