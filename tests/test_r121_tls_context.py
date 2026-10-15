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
        def open(self, req, timeout):
            return "opened"

    def fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(safety, "check_url", lambda url, allow_private=None: url)
    req = urllib.request.Request("https://example.com/")
    assert safety.safe_urlopen(req, timeout=1) == "opened"
    https = [h for h in captured["handlers"] if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(https) == 1 and https[0]._context is safety.tls_context()
    # the redirect guard (R-100) is still installed
    assert any(isinstance(h, safety._CheckedRedirectHandler) for h in captured["handlers"])
