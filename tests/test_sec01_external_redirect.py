"""SEC-01: ExternalAgentAdapter must re-check every redirect target.

Before the fix, ``execute`` checked only ``self.url`` with ``safety.check_url``
and then let aiohttp follow redirects on its own (``allow_redirects=True`` is
aiohttp's default), so a public agent URL could answer ``302 Location:
http://127.0.0.x/...`` (or a private / link-local / metadata address) and the
server would call it — bypassing the first check (SSRF).

Setup (real aiohttp client, real local HTTP servers — nothing about the HTTP
path is mocked):
  * the "public" agent runs on 127.0.0.1; the test marks ONLY 127.0.0.1 as
    public through ``safety.is_public_address`` (every other address keeps the
    real policy), simulating an attacker-controlled public host;
  * the "internal" target runs on 127.0.0.2 (a different loopback address that
    stays non-public) and counts the hits it receives.
"""
import asyncio
import http.server
import json
import threading

import pytest

from agent_civilization.agents.external_adapter import ExternalAgentAdapter
from agent_civilization.core import safety


class _InternalHandler(http.server.BaseHTTPRequestHandler):
    hits = 0

    def _serve(self):
        type(self).hits += 1
        body = b'{"choices":[{"message":{"content":"INTERNAL SECRET"}}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


def _redirecting_handler(location_for_path, status=302):
    class _Public(http.server.BaseHTTPRequestHandler):
        seen = []

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            type(self).seen.append((self.command, self.path,
                                    self.headers.get("Authorization"), body))
            target = location_for_path(self.path)
            if target:
                self.send_response(status)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            out = json.dumps({"choices": [{"message": {"content": "public answer"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        do_GET = do_POST = _serve

        def log_message(self, *a):
            pass

    return _Public


def _serve(host, handler):
    srv = http.server.ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def internal_target():
    _InternalHandler.hits = 0
    srv = _serve("127.0.0.2", _InternalHandler)
    yield f"http://127.0.0.2:{srv.server_address[1]}/latest/meta-data/", _InternalHandler
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def public_is_127_0_0_1(monkeypatch):
    real = safety.is_public_address

    def fake(ip):
        return True if ip == "127.0.0.1" else real(ip)

    monkeypatch.setattr(safety, "is_public_address", fake)


def _start_public(location_for_path, status=302):
    handler = _redirecting_handler(location_for_path, status)
    srv = _serve("127.0.0.1", handler)
    return srv, handler, f"http://127.0.0.1:{srv.server_address[1]}"


def _run(adapter):
    return asyncio.run(adapter.execute({"prompt": "hi"}))


def test_redirect_from_public_to_loopback_is_blocked(public_is_127_0_0_1, internal_target):
    target, internal = internal_target
    srv, public, base = _start_public(lambda p: target)
    try:
        out = _run(ExternalAgentAdapter("sec01", f"{base}/v1/chat/completions"))
    finally:
        srv.shutdown(); srv.server_close()
    assert len(public.seen) == 1                       # the public host was called
    assert internal.hits == 0                          # the redirect target never was
    assert out["ok"] is False
    assert "blocked non-public address" in out["result"]["error"]
    assert "INTERNAL SECRET" not in json.dumps(out)


@pytest.mark.parametrize("target", [
    "http://10.0.0.5/v1",                              # private (RFC 1918)
    "http://169.254.169.254/latest/meta-data/",       # link-local / cloud metadata
    "http://[::1]:8080/v1",                            # IPv6 loopback
    "file:///etc/passwd",                              # scheme not allowed
])
def test_redirect_to_non_public_targets_is_blocked(public_is_127_0_0_1, target):
    srv, public, base = _start_public(lambda p: target)
    try:
        out = _run(ExternalAgentAdapter("sec01", f"{base}/v1"))
    finally:
        srv.shutdown(); srv.server_close()
    assert out["ok"] is False
    err = out["result"]["error"]
    assert "blocked non-public address" in err or "scheme not allowed" in err


def test_redirect_to_public_target_is_still_followed_with_post(public_is_127_0_0_1):
    # 307 keeps the method and body; the final public answer is returned.
    srv, public, base = _start_public(
        lambda p: "/final" if p == "/v1" else None, status=307)
    try:
        out = _run(ExternalAgentAdapter("sec01", f"{base}/v1", api_key="k"))
    finally:
        srv.shutdown(); srv.server_close()
    assert out["ok"] is True and out["result"]["answer"] == "public answer"
    assert [(m, p) for m, p, _, _ in public.seen] == [("POST", "/v1"), ("POST", "/final")]
    assert json.loads(public.seen[1][3])["messages"][0]["content"] == "hi"
    assert public.seen[1][2] == "Bearer k"             # same origin keeps the key


def test_redirect_loop_is_bounded(public_is_127_0_0_1):
    srv, public, base = _start_public(lambda p: "/again")
    try:
        out = _run(ExternalAgentAdapter("sec01", f"{base}/v1"))
    finally:
        srv.shutdown(); srv.server_close()
    assert out["ok"] is False and "too many redirects" in out["result"]["error"]
    assert len(public.seen) == ExternalAgentAdapter.MAX_REDIRECTS + 1


def test_operator_agent_may_still_redirect_internally(internal_target):
    # EXTERNAL_AGENTS (operator config, allow_private=True) is unchanged: an
    # internal target stays allowed, now through the same per-hop check.
    target, internal = internal_target
    srv, public, base = _start_public(lambda p: target)
    try:
        out = _run(ExternalAgentAdapter("sec01-op", f"{base}/v1", allow_private=True))
    finally:
        srv.shutdown(); srv.server_close()
    assert out["ok"] is True and internal.hits == 1


def test_cross_origin_redirect_drops_the_agent_key(public_is_127_0_0_1):
    # Another port is another origin: the 307 is followed (public target) but
    # the agent's Authorization header is not forwarded to it.
    other, other_h, other_base = _start_public(lambda p: None)
    srv, public, base = _start_public(lambda p: f"{other_base}/final", status=307)
    try:
        out = _run(ExternalAgentAdapter("sec01", f"{base}/v1", api_key="secret-key"))
    finally:
        for x in (srv, other):
            x.shutdown(); x.server_close()
    assert out["ok"] is True
    assert public.seen[0][2] == "Bearer secret-key"
    assert other_h.seen[0][0] == "POST" and other_h.seen[0][2] is None
