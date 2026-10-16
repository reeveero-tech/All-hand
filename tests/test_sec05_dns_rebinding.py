"""SEC-05 (R-146): outbound connections are pinned to vetted DNS results.

The gap this closes: ``safety.check_url`` resolved a hostname and checked the
answer, then the transport (``http.client`` via ``socket.create_connection``,
aiohttp via its default resolver) resolved the SAME hostname again for the
actual TCP connection.  A DNS answer that changed between the two resolutions
(rebinding) made the protected paths reach an unvetted address — proven live
on the pre-fix tree by ``scripts/sec05/probe_dns_rebinding.py`` (raw log 253).

Everything here is deterministic and local-only:
* a controlled ``socket.getaddrinfo`` answers for the test hostname
  (``localhost`` — a loopback name, so the suite's offline guard lets the real
  transport run): the first resolution returns an address the policy allows,
  later resolutions return the rebinding answer;
* the "internal" listener runs on 127.0.0.2 (loopback /8, non-public) and
  counts the requests it receives — a hit means an unvetted address was
  reached, which is the vulnerability;
* the suite's autouse guard still blocks any real non-loopback connection, so
  the tests cannot touch an external network even if a check is removed.
"""
import asyncio
import http.server
import json
import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from agent_civilization.agents.external_adapter import ExternalAgentAdapter, GuardedResolver
from agent_civilization.core import safety

_bodies: list = []
CONTROL_HOST = "localhost"      # loopback name: the offline guard lets it run
ALLOWED_FIRST = "192.0.2.1"      # RFC 5737 documentation range: policy-allowed
INTERNAL = "127.0.0.2"           # loopback /8: local-only, non-public


# --------------------------------------------------------------------------- #
# controlled DNS + local servers
# --------------------------------------------------------------------------- #

class ControlledDNS:
    """``socket.getaddrinfo`` with a scripted answer sequence for CONTROL_HOST."""

    def __init__(self, monkeypatch):
        self._orig = socket.getaddrinfo
        self.answers: list = []      # each item: list of address strings
        self.raw: list | None = None  # prebuilt getaddrinfo entries, as-is
        self.calls: list = []
        self.fail = False            # True: every resolution raises
        self.fail_from = 0           # >0: raise from that call (1-based)
        self.error_text = "Name or service not known"
        self.exc: BaseException | None = None  # raise this instead of gaierror
        monkeypatch.setattr(socket, "getaddrinfo", self._fake)

    def script(self, *answers):
        self.answers = [list(a) for a in answers]
        self.raw = None

    def script_raw(self, *entries):
        """Prebuilt getaddrinfo entries, passed through UNVALIDATED (malformed
        structure tests: the GUARD must reject them, not the fake)."""
        self.raw = [list(e) for e in entries]
        self.answers = []

    def _fake(self, host, *args, **kwargs):
        host = str(host)
        if host == CONTROL_HOST:
            call = len(self.calls) + 1
            self.calls.append(call - 1)
            if self.fail or (self.fail_from and call >= self.fail_from):
                if self.exc is not None:
                    raise self.exc
                raise socket.gaierror(2, self.error_text)
            if self.raw is not None:
                return self.raw[min(call - 1, max(0, len(self.raw) - 1))]
            port = args[0] if args else kwargs.get("port", 0)
            answer = self.answers[min(call - 1, max(0, len(self.answers) - 1))]
            out = []
            for ip in answer:
                if ":" in ip:
                    # like the system resolver: IPv6 sockaddrs are 4-tuples
                    # (ip, port, flowinfo, scope_id)
                    out.append((socket.AF_INET6, socket.SOCK_STREAM,
                                socket.IPPROTO_TCP, "",
                                (ip, int(port or 0), 0, 0)))
                else:
                    out.append((socket.AF_INET, socket.SOCK_STREAM,
                                socket.IPPROTO_TCP, "",
                                (ip, int(port or 0))))
            return out
        return self._orig(host, *args, **kwargs)


class _JSONHandler(http.server.BaseHTTPRequestHandler):
    """Serves the OpenAI-ish answer; records Host header and request line.

    ``connections`` counts TCP connections (setup runs once per connection,
    before any request is parsed) — a served-request counter alone cannot
    prove that a socket never opened.
    """

    hits = 0
    connections = 0
    seen_hosts: list = []
    last_request = None            # (method, path, authorization, body)

    def setup(self):
        type(self).connections += 1
        super().setup()

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        _bodies.append(self.rfile.read(length) if length else b"")
        type(self).hits += 1
        type(self).seen_hosts.append(self.headers.get("Host"))
        type(self).last_request = (self.command, self.path,
                                   self.headers.get("Authorization"),
                                   _bodies[-1] if _bodies else b"")
        body = json.dumps({"choices": [{"message": {"content": "public answer"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """Answers a redirect to ``target`` with ``status_code``."""

    target = ""
    status_code = 302

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(self.status_code)
        self.send_header("Location", self.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


def _serve(host, handler):
    base = (http.server.ThreadingHTTPServer if ":" not in host
            else type("_V6Server", (http.server.ThreadingHTTPServer,),
                      {"address_family": socket.AF_INET6}))
    srv = base((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def dns(monkeypatch):
    return ControlledDNS(monkeypatch)


@pytest.fixture
def internal_listener():
    _JSONHandler.hits = 0
    _JSONHandler.connections = 0
    _JSONHandler.seen_hosts = []
    srv = _serve(INTERNAL, _JSONHandler)
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def loopback_is_public(monkeypatch):
    """Marks 127.0.0.1 (and ::1) as public, like the SEC-01 suite — the local
    server then plays an allowed destination without touching real DNS."""
    real = safety.is_public_address

    def fake(ip):
        if ip in ("127.0.0.1", "::1"):
            return True
        return real(ip)

    monkeypatch.setattr(safety, "is_public_address", fake)


def _adapter(url, **kw):
    return ExternalAgentAdapter("sec05", url, **kw)


# --------------------------------------------------------------------------- #
# A. the rebinding gap is closed on both paths
# --------------------------------------------------------------------------- #

def test_urllib_rebinding_is_blocked_before_the_socket(dns, internal_listener):
    dns.script([ALLOWED_FIRST], [INTERNAL])
    req = urllib.request.Request(f"http://{CONTROL_HOST}:{internal_listener}/x")
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.safe_urlopen(req, timeout=5)
    assert "blocked non-public address" in str(ei.value)
    assert _JSONHandler.hits == 0                    # the listener was never reached
    assert _JSONHandler.connections == 0             # ... and no TCP connection opened


def test_aiohttp_rebinding_is_blocked_before_the_socket(dns, internal_listener):
    dns.script([ALLOWED_FIRST], [INTERNAL])
    out = asyncio.run(_adapter(
        f"http://{CONTROL_HOST}:{internal_listener}/v1/chat/completions").execute(
        {"prompt": "hi"}))
    assert out["ok"] is False
    assert "blocked non-public address" in out["result"]["error"]
    assert _JSONHandler.hits == 0
    assert _JSONHandler.connections == 0            # no TCP connection opened


def test_urllib_error_does_not_leak_the_url_or_credentials(dns, internal_listener):
    dns.script([ALLOWED_FIRST], [INTERNAL])
    req = urllib.request.Request(
        f"http://{CONTROL_HOST}:{internal_listener}/x?token=secret-token-123")
    req.add_header("Authorization", "Bearer agent-key")
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.safe_urlopen(req, timeout=5)
    msg = str(ei.value)
    assert "token" not in msg and "agent-key" not in msg and "/x" not in msg


# --------------------------------------------------------------------------- #
# B. the allowed path connects to the address the policy vetted
# --------------------------------------------------------------------------- #

def test_urllib_allowed_connection_uses_the_vetted_address(dns, loopback_is_public):
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    dns.script([ALLOWED_FIRST], ["127.0.0.1"])
    srv = _serve("127.0.0.1", _JSONHandler)
    port = srv.server_address[1]
    try:
        with safety.safe_urlopen(
                urllib.request.Request(f"http://{CONTROL_HOST}:{port}/x"), timeout=5) as r:
            assert r.status == 200
    finally:
        srv.shutdown()
        srv.server_close()
    assert _JSONHandler.hits == 1                   # connected, to the vetted IP
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]   # Host = original name
    assert len(dns.calls) == 2                      # check + connection: no hidden extra


def test_aiohttp_allowed_connection_uses_the_vetted_address(dns, loopback_is_public):
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    dns.script([ALLOWED_FIRST], ["127.0.0.1"])
    srv = _serve("127.0.0.1", _JSONHandler)
    port = srv.server_address[1]
    try:
        out = asyncio.run(_adapter(
            f"http://{CONTROL_HOST}:{port}/v1/chat/completions").execute(
            {"prompt": "hi"}))
    finally:
        srv.shutdown()
        srv.server_close()
    assert out["ok"] is True and out["result"]["answer"] == "public answer"
    assert _JSONHandler.hits == 1
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]
    assert len(dns.calls) == 2                       # early check + guarded resolver only


def test_vetted_sockaddrs_return_the_checked_targets(dns, loopback_is_public):
    dns.script(["127.0.0.1"])
    out = safety.vetted_sockaddrs(CONTROL_HOST, 8080)
    assert [sa for _, _, sa in out] == [("127.0.0.1", 8080)]
    assert out[0][0] == socket.AF_INET
    assert out[0][1] == socket.IPPROTO_TCP


# --------------------------------------------------------------------------- #
# C. address policy: every address of the answer is checked
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("answer", [
    [ALLOWED_FIRST, INTERNAL],                 # mixed IPv4: one internal -> reject
    ["2001:db8::1", "::1"],                  # mixed IPv6
    ["::1"],                                 # IPv6 loopback alone
    [INTERNAL],                              # internal alone
])
def test_mixed_or_internal_answers_are_rejected_as_a_whole(dns, answer):
    dns.script(answer)
    with pytest.raises(safety.UnsafeURLError):
        safety.vetted_sockaddrs(CONTROL_HOST, 80)
    with pytest.raises(safety.UnsafeURLError):
        safety.check_url(f"http://{CONTROL_HOST}:80/x")


def test_guarded_resolver_rejects_a_mixed_answer(dns):
    dns.script(["2001:db8::1", INTERNAL])

    async def resolve():
        return await GuardedResolver(allow_private=False).resolve(CONTROL_HOST, 80)

    with pytest.raises(safety.UnsafeURLError):
        asyncio.run(resolve())


def test_allowed_ipv6_connection_reaches_the_vetted_address(dns, loopback_is_public):
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    dns.script(["::1"])
    srv = _serve("::1", _JSONHandler)
    port = srv.server_address[1]
    try:
        with safety.safe_urlopen(
                urllib.request.Request(f"http://{CONTROL_HOST}:{port}/x"), timeout=5) as r:
            assert r.status == 200
    finally:
        srv.shutdown()
        srv.server_close()
    assert _JSONHandler.hits == 1
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]


def test_dns_failure_and_empty_answer_fail_closed(dns):
    dns.fail = True
    # the transport layer wraps the failure in the policy error (fail closed)
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.vetted_sockaddrs(CONTROL_HOST, 80)
    assert "DNS resolution failed" in str(ei.value)
    # the full request path fails too — never falls back to a plain urlopen
    with pytest.raises((safety.UnsafeURLError, socket.gaierror)):
        safety.safe_urlopen(
            urllib.request.Request(f"http://{CONTROL_HOST}:80/x"), timeout=5)

    dns.fail = False
    dns.script([])                 # one answer: the empty set
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.vetted_sockaddrs(CONTROL_HOST, 80)
    assert "no usable addresses" in str(ei.value)
    with pytest.raises(safety.UnsafeURLError):
        safety.safe_urlopen(
            urllib.request.Request(f"http://{CONTROL_HOST}:80/x"), timeout=5)


# --------------------------------------------------------------------------- #
# D. HTTPS: original hostname keeps SNI and certificate verification
# --------------------------------------------------------------------------- #

def _make_certs(tmp_path, hostname):
    """A local CA + a leaf certificate for ``hostname`` (openssl CLI)."""
    ca_key = tmp_path / "ca.key"
    ca_crt = tmp_path / "ca.crt"
    leaf_key = tmp_path / "leaf.key"
    leaf_csr = tmp_path / "leaf.csr"
    leaf_crt = tmp_path / "leaf.crt"
    leaf_ext = tmp_path / "leaf.ext"
    leaf_ext.write_text(
        f"subjectAltName=DNS:{hostname}\nbasicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\n")
    common = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=sec05-test-ca", "-keyout", str(ca_key), "-out", str(ca_crt),
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign,digitalSignature"],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes",
         "-subj", f"/CN={hostname}", "-keyout", str(leaf_key), "-out", str(leaf_csr)],
        ["openssl", "x509", "-req", "-in", str(leaf_csr), "-CA", str(ca_crt),
         "-CAkey", str(ca_key), "-CAcreateserial", "-days", "2",
         "-extfile", str(leaf_ext), "-out", str(leaf_crt)],
    ]
    for cmd in common:
        subprocess.run(cmd, check=True, capture_output=True)
    return str(ca_crt), str(leaf_crt), str(leaf_key)


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    """A verifying local HTTPS server for ``localhost`` + a client CA context.

    Records the SNI the client sent.  ``safety.tls_context`` is pointed at the
    test CA — the same verifying context the guarded HTTPS connection uses.
    """
    ca_crt, leaf_crt, leaf_key = _make_certs(tmp_path, "localhost")
    client_ctx = ssl.create_default_context(cafile=ca_crt)
    monkeypatch.setattr(safety, "tls_context", lambda: client_ctx)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(leaf_crt, leaf_key)
    sni: list = []

    def record_sni(sslos, server_name, ctx):
        sni.append(server_name)
        return None

    server_ctx.sni_callback = record_sni

    plain = _serve("127.0.0.1", _JSONHandler)
    plain.socket = server_ctx.wrap_socket(plain.socket, server_side=True)
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    yield plain.server_address[1], sni
    plain.shutdown()
    plain.server_close()


def test_https_keeps_hostname_sni_and_verification(dns, loopback_is_public, tls_server):
    port, sni = tls_server
    dns.script(["127.0.0.1"])
    with safety.safe_urlopen(
            urllib.request.Request(f"https://{CONTROL_HOST}:{port}/x"), timeout=5) as r:
        assert r.status == 200
    assert _JSONHandler.hits == 1
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]   # Host: original name
    assert sni == [CONTROL_HOST]                                   # SNI: original name


def test_https_rejects_a_certificate_for_another_hostname(dns, loopback_is_public,
                                                          tmp_path, monkeypatch):
    # The server presents a certificate for "other.test": verification against
    # the ORIGINAL hostname (localhost) must fail — connecting to a vetted IP
    # never weakens the identity check.  The TCP connection may be established
    # before the handshake fails; the request itself never completes.
    ca_crt, leaf_crt, leaf_key = _make_certs(tmp_path, "other.test")
    client_ctx = ssl.create_default_context(cafile=ca_crt)
    monkeypatch.setattr(safety, "tls_context", lambda: client_ctx)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(leaf_crt, leaf_key)
    srv = _serve("127.0.0.1", _JSONHandler)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    dns.script(["127.0.0.1"])
    try:
        # urllib wraps the transport failure in URLError; the REASON is the
        # certificate verification failure against the ORIGINAL hostname —
        # specifically the hostname mismatch (a trusted CA is used, so a
        # generic trust failure would mean the test itself is broken).
        with pytest.raises(urllib.error.URLError) as ei:
            safety.safe_urlopen(
                urllib.request.Request(f"https://{CONTROL_HOST}:{port}/x"), timeout=5)
        assert isinstance(ei.value.reason, ssl.SSLCertVerificationError)
        assert "Hostname mismatch" in str(ei.value.reason)
        assert _JSONHandler.hits == 0            # no request was ever served
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------------- #
# E. proxies from the environment are not honored for protected requests
# --------------------------------------------------------------------------- #

def test_environment_proxies_are_ignored(dns, loopback_is_public, monkeypatch):
    _ProxySink.hits = 0
    proxy = _serve("127.0.0.1", _ProxySink)
    _JSONHandler.hits = 0
    _JSONHandler.connections = 0
    _JSONHandler.seen_hosts = []
    dns.script(["127.0.0.1"])
    srv = _serve("127.0.0.1", _JSONHandler)
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY",
                "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{proxy.server_address[1]}")
    # the proxy bypass vars must NOT hide the effect of the proxy vars
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    try:
        # positive control under the SAME environment: a DEFAULT opener
        # (env proxies honored) really routes the request through the proxy —
        # proves the environment is effective, so the protected path ignoring
        # it is our handler's doing, not a bypassed setting.
        plain = urllib.request.build_opener()
        with plain.open(f"http://{CONTROL_HOST}:{srv.server_address[1]}/x",
                        timeout=5) as r:
            assert r.status == 200
        assert _ProxySink.hits == 1 and _ProxySink.connections >= 1
        # the protected path: same environment, same destination — the proxy
        # is ignored and the VETTED destination is reached directly.
        _ProxySink.hits = 0
        _ProxySink.connections = 0
        with safety.safe_urlopen(
                urllib.request.Request(f"http://{CONTROL_HOST}:{srv.server_address[1]}/x"),
                timeout=5) as r:
            assert r.status == 200
        assert _ProxySink.hits == 0                 # no request was ever served
        assert _ProxySink.connections == 0         # ... and no TCP socket opened
        assert _JSONHandler.hits == 1               # the real vetted destination was
    finally:
        for x in (proxy, srv):
            x.shutdown()
            x.server_close()


class _ProxySink(http.server.BaseHTTPRequestHandler):
    """A would-be proxy: counts SERVED requests AND TCP connections — a
    served-request counter alone cannot prove that no socket ever opened."""

    hits = 0
    connections = 0

    def setup(self):
        type(self).connections += 1
        super().setup()

    def _serve(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


# --------------------------------------------------------------------------- #
# F. redirects, methods and policy contracts stay intact
# --------------------------------------------------------------------------- #

def _redirect_to(target, status=302):
    cls = type("_Redirect", (_RedirectHandler,), {"target": target,
                                                  "status_code": status})
    return cls


def test_urllib_redirect_target_goes_through_the_guarded_connection(
        dns, loopback_is_public, internal_listener):
    # First hop: a literal allowed IP (no DNS).  The redirect points at the
    # controlled hostname: the early re-check sees the allowed answer, the
    # guarded connection resolves the rebinding answer and must refuse.
    dns.script([ALLOWED_FIRST], [INTERNAL])
    hop = _serve("127.0.0.1", _redirect_to(f"http://{CONTROL_HOST}:{internal_listener}/x"))
    _JSONHandler.hits = 0
    try:
        with pytest.raises(safety.UnsafeURLError):
            safety.safe_urlopen(
                urllib.request.Request(f"http://127.0.0.1:{hop.server_address[1]}/r"),
                timeout=5)
    finally:
        hop.shutdown()
        hop.server_close()
    assert _JSONHandler.hits == 0


def test_aiohttp_redirect_target_goes_through_the_guarded_resolver(
        dns, loopback_is_public, internal_listener):
    dns.script([ALLOWED_FIRST], [INTERNAL])
    hop = _serve("127.0.0.1", _redirect_to(
        f"http://{CONTROL_HOST}:{internal_listener}/v1/chat/completions"))
    _JSONHandler.hits = 0
    try:
        out = asyncio.run(_adapter(
            f"http://127.0.0.1:{hop.server_address[1]}/v1/chat/completions").execute(
            {"prompt": "hi"}))
    finally:
        hop.shutdown()
        hop.server_close()
    assert out["ok"] is False
    assert "blocked non-public address" in out["result"]["error"]
    assert _JSONHandler.hits == 0
    assert _JSONHandler.connections == 0            # no TCP connection opened


def test_urllib_redirect_loop_stays_bounded(loopback_is_public):
    class _Loop(http.server.BaseHTTPRequestHandler):
        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", "/again")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _serve

        def log_message(self, *a):
            pass

    srv = _serve("127.0.0.1", _Loop)
    try:
        with pytest.raises(urllib.error.HTTPError):
            safety.safe_urlopen(
                urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/r"),
                timeout=5)
    finally:
        srv.shutdown()
        srv.server_close()


def test_public_literal_url_still_works(loopback_is_public):
    _JSONHandler.hits = 0
    srv = _serve("127.0.0.1", _JSONHandler)
    try:
        with safety.safe_urlopen(
                urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/x"),
                timeout=5) as r:
            assert r.status == 200
    finally:
        srv.shutdown()
        srv.server_close()
    assert _JSONHandler.hits == 1


def test_metadata_and_internal_literals_are_rejected():
    for url in ("http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/v1", "http://[::1]:8080/v1"):
        with pytest.raises(safety.UnsafeURLError):
            safety.safe_urlopen(urllib.request.Request(url), timeout=5)


def test_allow_private_still_reaches_internal_targets(dns, internal_listener):
    # The documented exception for operator-configured components: the guarded
    # transport connects to the internal address when the policy allows it.
    dns.script([INTERNAL])
    with safety.safe_urlopen(
            urllib.request.Request(f"http://{CONTROL_HOST}:{internal_listener}/x"),
            timeout=5, allow_private=True) as r:
        assert r.status == 200
    assert _JSONHandler.hits == 1

    _JSONHandler.hits = 0
    out = asyncio.run(_adapter(
        f"http://{CONTROL_HOST}:{internal_listener}/v1/chat/completions",
        allow_private=True).execute({"prompt": "hi"}))
    assert out["ok"] is True and _JSONHandler.hits == 1


def test_allow_private_does_not_skip_url_or_redirect_checks(dns):
    # allow_private widens the ADDRESS policy only: schemes and redirects are
    # still checked (file:// refused, a redirect still re-checked per hop).
    with pytest.raises(safety.UnsafeURLError):
        safety.safe_urlopen(
            urllib.request.Request("file:///etc/passwd"), timeout=5,
            allow_private=True)


# --------------------------------------------------------------------------- #
# G. R-146b review round: explicit proxies, corrupt answers, clean errors,
#    aiohttp-side TLS/IPv6/DNS-failure, redirect method semantics
# --------------------------------------------------------------------------- #

def test_explicit_proxy_on_the_request_is_refused(dns, loopback_is_public,
                                                  internal_listener):
    # An explicit proxy on the Request (set_proxy rewrites req.host; for HTTPS
    # it also sets a tunnel) would make the PROXY resolve the destination and
    # void the vetted-address guarantee.  No trusted-proxy design exists, so
    # every proxy signature is refused BEFORE any connection — and a REAL
    # proxy listener proves, per case, that neither a request was served to
    # the proxy NOR a TCP socket ever opened to it.
    dns.script(["127.0.0.1"])           # the early check passes...
    _ProxySink.hits = 0
    _ProxySink.connections = 0
    sink = _serve("127.0.0.1", _ProxySink)
    sink_port = sink.server_address[1]
    try:
        # a) http proxy at a DIFFERENT address than the destination
        req = urllib.request.Request(f"http://{CONTROL_HOST}:{internal_listener}/x")
        req.set_proxy(f"127.0.0.1:{sink_port}", "http")
        with pytest.raises(safety.UnsafeURLError) as ei:
            safety.safe_urlopen(req, timeout=5)
        assert "explicit proxies are not supported" in str(ei.value)
        assert _ProxySink.hits == 0 and _ProxySink.connections == 0

        # b) the HTTPS tunnel (set_proxy stores the destination in
        #    _tunnel_host and would CONNECT through the proxy)
        req = urllib.request.Request(f"https://{CONTROL_HOST}:{internal_listener}/x")
        req.set_proxy(f"127.0.0.1:{sink_port}", "https")
        with pytest.raises(safety.UnsafeURLError) as ei:
            safety.safe_urlopen(req, timeout=5)
        assert "explicit proxies are not supported" in str(ei.value)
        assert _ProxySink.hits == 0 and _ProxySink.connections == 0

        # c) the MATCHING-ADDRESS proxy: set_proxy to the request's OWN host
        #    keeps req.host == netloc, so a host-comparison check alone would
        #    miss it — has_proxy() (the absolute-form selector) catches it.
        req = urllib.request.Request(f"http://{CONTROL_HOST}:{sink_port}/x")
        req.set_proxy(req.host, "http")
        with pytest.raises(safety.UnsafeURLError) as ei:
            safety.safe_urlopen(req, timeout=5)
        assert "explicit proxies are not supported" in str(ei.value)
        assert _ProxySink.hits == 0 and _ProxySink.connections == 0
    finally:
        sink.shutdown()
        sink.server_close()
    assert _JSONHandler.hits == 0 and _JSONHandler.connections == 0


def test_non_ip_entries_in_the_answer_fail_closed(dns):
    # A hostname/garbage inside the DNS answer would make socket.connect
    # resolve it AGAIN — outside the guard.  Rejected regardless of the
    # private exception (allow_private reclassifies an address, it never
    # disables the usability check).
    dns.script(["attacker-controlled.example"])
    for kwargs in ({}, {"allow_private": True}):
        with pytest.raises(safety.UnsafeURLError) as ei:
            safety.vetted_sockaddrs(CONTROL_HOST, 80, **kwargs)
        assert "unusable address" in str(ei.value)
    with pytest.raises(safety.UnsafeURLError):
        safety.check_url(f"http://{CONTROL_HOST}:80/x")
    # a literal-IP policy check still rejects garbage the same way
    with pytest.raises(safety.UnsafeURLError):
        safety.check_resolved(CONTROL_HOST, ["not-an-ip"])


# All entries use POLICY-ALLOWED addresses (documentation ranges are public
# per the address policy), so with allow_private=False the request still
# reaches the STRUCTURE validation — the rejection reason is the usability
# one for every case, on both transports.


MALFORMED_CASES = [
    # truncated tuple (no sockaddr element at all)
    [("AF_INET", socket.SOCK_STREAM, socket.IPPROTO_TCP, "")],
    # non-string address inside the sockaddr
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      (None, 80))],
    # family contradicts the address: AF_INET entry carrying an IPv6
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("2001:db8::1", 80))],
    # family contradicts the address: AF_UNSPEC entry carrying an IPv6
    # (an explicit family match — "not AF_INET" alone would let this pass)
    [(socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("2001:db8::1", 80))],
    # IPv4 literal with a scope suffix: NOT a usable connect target
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("192.0.2.1%eth0", 80))],
    # non-integer port
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("192.0.2.1", "80"))],
    # IPv6 extras (flowinfo/scope_id) on an IPv4 sockaddr
    [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("192.0.2.1", 80, 0, 42))],
    # non-integer sockaddr extras
    [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("2001:db8::1", 80, "0", 42))],
    # 3-element IPv6 sockaddr (flowinfo without scope_id): unsupported shape
    [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("2001:db8::1", 80, 0))],
    # 5-element IPv6 sockaddr: unsupported shape
    [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
      ("2001:db8::1", 80, 0, 0, 0))],
]


@pytest.mark.parametrize("allow_private", [False, True])
@pytest.mark.parametrize("entry", MALFORMED_CASES)
def test_malformed_getaddrinfo_results_fail_closed_on_both_paths(
        dns, entry, allow_private):
    # R-146b review rounds 2+3: the COMPLETE getaddrinfo entry is validated
    # by the SHARED gate both transports use — a truncated tuple, a
    # non-string address, a family that contradicts the address (including
    # AF_UNSPEC), an IPv4 literal with a scope suffix, a non-integer port,
    # IPv6 extras on an IPv4 sockaddr or non-integer extras all fail closed
    # with the FIXED policy reason (never a raw extraction error), and the
    # private exception never disables the structure check — on the urllib
    # path AND the resolver path alike.
    dns.script_raw(entry)
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.vetted_sockaddrs(CONTROL_HOST, 80, allow_private=allow_private)
    assert "unusable" in str(ei.value)

    dns.script_raw(entry)
    with pytest.raises(safety.UnsafeURLError) as ei:
        asyncio.run(GuardedResolver(allow_private=allow_private)
                    .resolve(CONTROL_HOST, 80))
    assert "unusable" in str(ei.value)


def test_vetted_sockaddr_preserves_ipv6_scope_information(dns):
    # R-146b review round 3: an IPv6 sockaddr may carry flowinfo and
    # scope_id — they are validated as integers and PRESERVED in the connect
    # target, and a scope embedded in the address literal survives too (the
    # socket layer gets the full sockaddr, never a re-resolvable name).
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1%eth0", 8080, 0, 42))])
    out = safety.vetted_sockaddrs(CONTROL_HOST, 8080, allow_private=True)
    assert out == [(socket.AF_INET6, socket.IPPROTO_TCP,
                   ("fe80::1%eth0", 8080, 0, 42))]

    # a 2-element IPv6 sockaddr (no extras) stays valid
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("2001:db8::1", 8080))])
    out = safety.vetted_sockaddrs(CONTROL_HOST, 8080)
    assert out == [(socket.AF_INET6, socket.IPPROTO_TCP, ("2001:db8::1", 8080))]

    # a BARE literal with a numeric scope_id (no "%scope" in the string):
    # the urllib path keeps the full sockaddr — the numeric scope IS the
    # connect target's scope_id element, nothing is dropped.
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1", 8080, 0, 42))])
    out = safety.vetted_sockaddrs(CONTROL_HOST, 8080, allow_private=True)
    assert out == [(socket.AF_INET6, socket.IPPROTO_TCP,
                   ("fe80::1", 8080, 0, 42))]


def test_guarded_resolver_embeds_the_numeric_ipv6_scope(dns):
    # R-146b review round 4: the agent transport builds its connect sockaddr
    # from the host literal alone (aiohttp hardcodes flowinfo/scope_id to 0),
    # so a numeric IPv6 scope must ride INSIDE the literal to reach the
    # connect target — and a nonzero flowinfo, which that transport cannot
    # represent at all, fails closed with a fixed reason instead of being
    # silently dropped.
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1", 8080, 0, 42))])
    out = asyncio.run(GuardedResolver(allow_private=True)
                      .resolve(CONTROL_HOST, 8080))
    assert [r["host"] for r in out] == ["fe80::1%42"]   # scope rides inside

    # nonzero flowinfo: not representable by the transport -> fixed reason
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1", 8080, 7, 42))])
    with pytest.raises(safety.UnsafeURLError) as ei:
        asyncio.run(GuardedResolver(allow_private=True)
                    .resolve(CONTROL_HOST, 8080))
    assert "flowinfo" in str(ei.value)

    # BOTH representations present: they must AGREE.  A matching pair
    # (the kernel resolves the string scope to the same interface index)
    # passes through unchanged — numeric string scopes are compared as
    # integers, interface names via if_nametoindex.
    lo_ifindex = socket.if_nametoindex("lo")
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1%lo", 8080, 0, lo_ifindex))])
    out = asyncio.run(GuardedResolver(allow_private=True)
                      .resolve(CONTROL_HOST, 8080))
    assert [r["host"] for r in out] == ["fe80::1%lo"]

    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1%42", 8080, 0, 42))])
    out = asyncio.run(GuardedResolver(allow_private=True)
                      .resolve(CONTROL_HOST, 8080))
    assert [r["host"] for r in out] == ["fe80::1%42"]

    # a CONFLICTING pair is rejected with a fixed reason — the string scope
    # is never silently preferred over the numeric one (and vice versa).
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1%lo", 8080, 0, 999))])
    with pytest.raises(safety.UnsafeURLError) as ei:
        asyncio.run(GuardedResolver(allow_private=True)
                    .resolve(CONTROL_HOST, 8080))
    assert "conflicting IPv6 scope" in str(ei.value)

    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1%42", 8080, 0, 7))])
    with pytest.raises(safety.UnsafeURLError) as ei:
        asyncio.run(GuardedResolver(allow_private=True)
                    .resolve(CONTROL_HOST, 8080))
    assert "conflicting IPv6 scope" in str(ei.value)


def test_aiohttp_scoped_ipv6_reaches_the_vetted_address(dns, loopback_is_public):
    # End-to-end proof that a numeric IPv6 scope reaches the CONNECT TARGET
    # itself on the agent transport: the answer is a BARE ::1 with the
    # loopback interface's real index as scope_id, the guarded resolver
    # embeds it in the literal, and the REAL adapter connects to a live ::1
    # listener through it.  Entirely local — no external network.
    lo_ifindex = next(i for i, name in socket.if_nameindex() if name == "lo")
    srv = _serve("::1", _JSONHandler)
    port = srv.server_address[1]
    _JSONHandler.hits = 0
    _JSONHandler.seen_hosts = []
    try:
        dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM,
                         socket.IPPROTO_TCP, "",
                         ("::1", port, 0, lo_ifindex))])
        out = asyncio.run(_adapter(f"http://{CONTROL_HOST}:{port}/v1/chat/"
                                  "completions").execute({"prompt": "hi"}))
        assert out["ok"] is True and out["result"]["answer"] == "public answer"
        assert _JSONHandler.hits == 1
        assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_urllib_connect_target_carries_the_numeric_scope(dns, loopback_is_public,
                                                         monkeypatch):
    # R-146b review round 5: the SUCCESS of a ::1 connection alone would not
    # prove scope preservation (::1%99 connects too on this kernel) — a
    # test-level observer records the address the REAL urllib transport
    # actually hands to socket.connect, and the recorded target is the
    # proof: the full 4-tuple with the expected scope_id, then the local
    # listener completes the request.
    lo_ifindex = socket.if_nametoindex("lo")
    recorded = []
    orig_connect = socket.socket.connect

    def record_connect(sock, addr):
        recorded.append(addr)
        return orig_connect(sock, addr)

    monkeypatch.setattr(socket.socket, "connect", record_connect)
    srv = _serve("::1", _JSONHandler)
    port = srv.server_address[1]
    _JSONHandler.hits = 0
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("::1", port, 0, lo_ifindex))])
    try:
        with safety.safe_urlopen(
                urllib.request.Request(f"http://{CONTROL_HOST}:{port}/x"),
                timeout=5) as r:
            assert r.status == 200
        assert recorded == [("::1", port, 0, lo_ifindex)]
    finally:
        srv.shutdown()
        srv.server_close()


def test_aiohttp_connect_target_carries_the_numeric_scope(dns, loopback_is_public,
                                                           monkeypatch):
    # Same observer discipline on the agent transport: the address the REAL
    # adapter hands to socket.connect is recorded — the numeric scope must
    # ride INSIDE the literal (aiohttp's transport builds the sockaddr from
    # the host literal alone), so the recorded host is "::1%<ifindex>".
    lo_ifindex = socket.if_nametoindex("lo")
    recorded = []
    orig_connect = socket.socket.connect

    def record_connect(sock, addr):
        recorded.append(addr)
        return orig_connect(sock, addr)

    monkeypatch.setattr(socket.socket, "connect", record_connect)
    srv = _serve("::1", _JSONHandler)
    port = srv.server_address[1]
    _JSONHandler.hits = 0
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("::1", port, 0, lo_ifindex))])
    try:
        out = asyncio.run(_adapter(f"http://{CONTROL_HOST}:{port}/v1/chat/"
                                   "completions").execute({"prompt": "hi"}))
        assert out["ok"] is True
        # The FINAL connect target carries the expected numeric scope_id:
        # the literal "::1%<ifindex>" is handed to the transport, asyncio
        # normalizes it through the address-resolution layer, and the
        # sockaddr that reaches socket.connect is ("::1", port, 0,
        # <ifindex>) — the scope is IN the kernel-bound target, not merely
        # in the string.
        assert recorded == [("::1", port, 0, lo_ifindex)]
    finally:
        srv.shutdown()
        srv.server_close()


def test_aiohttp_fe80_numeric_scope_recorded_and_refused(dns, monkeypatch):
    # The fe80::1 case from the review: the connect attempt is INTERCEPTED,
    # its target RECORDED and the connection REFUSED — no socket ever opens,
    # no external network is touched.  The recorded host proves the numeric
    # scope (42) reached the connect target inside the literal.
    recorded = []

    async def refuse_sock_connect(loop, sock, address):
        recorded.append(address)
        raise OSError("test barrier: connect refused")

    monkeypatch.setattr(asyncio.SelectorEventLoop, "sock_connect",
                        refuse_sock_connect)

    def refuse_connect(sock, addr):
        raise OSError("test barrier: connect refused")

    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    dns.script_raw([(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", ("fe80::1", 8080, 0, 42))])
    out = asyncio.run(_adapter(f"http://{CONTROL_HOST}:8080/v1/chat/"
                              "completions",
                              allow_private=True).execute({"prompt": "hi"}))
    assert out["ok"] is False                      # refused, never connected
    # The transport's connect target (what aiohappyeyeballs hands to
    # loop.sock_connect) carries the numeric scope INSIDE the literal —
    # the address that would be normalized into a kernel sockaddr with
    # scope_id 42.  No socket ever opened.
    assert recorded == [("fe80::1%42", 8080, 0, 0)]


def test_connection_layer_dns_error_is_cleaned(dns, loopback_is_public, caplog):
    # R-146b review rounds 2+3: the EARLY check sees a valid allowed answer
    # and the failure is raised by the resolution used FOR THE CONNECTION —
    # proving the TRANSPORT layer (not just the pre-check) converts
    # resolver errors to the fixed reason.  The sequence is re-armed for
    # EVERY path and EVERY error type, with the call count asserted after
    # each attempt (1 = the early check, 2 = the connection resolution).
    SECRET = "fake-secret-token-XYZ"

    def arm(error):
        """A fresh 2-step sequence: an allowed first answer, then `error`."""
        dns.calls.clear()
        dns.fail = False
        dns.fail_from = 0
        dns.exc = None
        dns.raw = None
        dns.script(["127.0.0.1"])
        error()

    def secret_gaierror():
        dns.fail_from = 2
        dns.error_text = SECRET

    def non_oserror():
        dns.fail_from = 2
        dns.exc = ValueError(f"{SECRET} non-oserror")

    def corrupt_answer():
        # the connection-layer resolution returns a corrupted entry whose
        # payload carries the secret — the fixed reason, never the payload
        good = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                 "", ("127.0.0.1", 8081))]
        corrupt = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     "", (SECRET, 8081))]
        dns.script_raw(good, corrupt)

    for arm_error, reason in ((secret_gaierror, "DNS resolution failed"),
                             (non_oserror, "DNS resolution failed"),
                             (corrupt_answer, "unusable")):
        # urllib path: the early check consumes call 1, the guarded
        # connection's resolution is call 2 and raises/returns the failure.
        arm(arm_error)
        with pytest.raises(safety.UnsafeURLError) as ei:
            safety.safe_urlopen(
                urllib.request.Request(f"http://{CONTROL_HOST}:8081/x"),
                timeout=5)
        assert reason in str(ei.value)
        assert SECRET not in str(ei.value)
        assert len(dns.calls) == 2

        # adapter path: the SAME fresh sequence (its early check is call 1,
        # the connector's guarded resolution is call 2).
        arm(arm_error)
        out = asyncio.run(_adapter(
            f"http://{CONTROL_HOST}:8081/v1/chat/completions").execute(
            {"prompt": "hi"}))
        assert out["ok"] is False
        assert reason in out["result"]["error"]
        assert SECRET not in json.dumps(out)
        assert SECRET not in caplog.text
        assert len(dns.calls) == 2



def test_guarded_resolver_hands_the_connector_vetted_ip_literals(dns,
                                                                 loopback_is_public):
    # The connector receives IP LITERALS (never a name it could re-resolve),
    # the requested port, the family, and proto from the getaddrinfo answer.
    dns.script(["127.0.0.1", "::1"])

    async def resolve():
        return await GuardedResolver(allow_private=False).resolve(CONTROL_HOST, 8080)

    out = asyncio.run(resolve())
    assert out, "resolver returned nothing"
    for r in out:
        assert r["host"] in ("127.0.0.1", "::1")   # IP literal, never a name
        assert r["port"] == 8080
        assert r["proto"] == socket.IPPROTO_TCP    # the answer's protocol field
        assert r["family"] in (socket.AF_INET, socket.AF_INET6)
        assert r["hostname"] == CONTROL_HOST


def test_guarded_resolver_wraps_dns_failure_and_empty_answer(dns):
    async def resolve():
        return await GuardedResolver(allow_private=False).resolve(CONTROL_HOST, 80)

    import aiohttp
    dns.fail = True
    with pytest.raises(aiohttp.ClientConnectionError) as ei:
        asyncio.run(resolve())
    assert "DNS resolution failed" in str(ei.value)

    dns.fail = False
    dns.script([])
    with pytest.raises(aiohttp.ClientConnectionError) as ei:
        asyncio.run(resolve())
    assert "no addresses" in str(ei.value)


def test_raw_dns_error_text_is_not_forwarded(dns, caplog):
    # The controlled resolver raises an error whose TEXT carries a fake
    # secret: neither path may forward that text to the result or the log.
    dns.fail = True
    dns.error_text = "fake-secret-token-XYZ"
    with pytest.raises(safety.UnsafeURLError) as ei:
        safety.safe_urlopen(
            urllib.request.Request(f"http://{CONTROL_HOST}:80/x"), timeout=5)
    assert "DNS resolution failed" in str(ei.value)
    assert "fake-secret-token-XYZ" not in str(ei.value)

    out = asyncio.run(_adapter(
        f"http://{CONTROL_HOST}:80/v1/chat/completions").execute(
        {"prompt": "hi"}))
    assert out["ok"] is False
    assert "DNS resolution failed" in out["result"]["error"]
    assert "fake-secret-token-XYZ" not in json.dumps(out)
    assert "fake-secret-token-XYZ" not in caplog.text

@pytest.fixture
def tls_material(tmp_path):
    """A local CA + a leaf certificate for ``localhost`` (openssl CLI)."""
    ca_crt, leaf_crt, leaf_key = _make_certs(tmp_path, "localhost")
    return ca_crt, leaf_crt, leaf_key


def _tls_server_for(ca_crt, leaf_crt, leaf_key, handler=_JSONHandler):
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(leaf_crt, leaf_key)
    sni: list = []

    def record_sni(sslos, server_name, ctx):
        sni.append(server_name)
        return None

    server_ctx.sni_callback = record_sni
    srv = _serve("127.0.0.1", handler)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    return srv, sni


def test_aiohttp_https_keeps_hostname_sni_and_verification(
        dns, loopback_is_public, tls_material, monkeypatch):
    # The REAL adapter over HTTPS.  aiohttp builds its verified context at
    # IMPORT time (ssl.create_default_context), so SSL_CERT_FILE cannot reach
    # it: the test patches the connector's verified context with one that
    # trusts the local CA.  Verification itself (CERT_REQUIRED + hostname
    # check) is untouched; the guarded resolver pins the connection to the
    # vetted IP and the server records the ORIGINAL hostname in Host and SNI.
    ca_crt, leaf_crt, leaf_key = tls_material
    import aiohttp.connector
    monkeypatch.setattr(aiohttp.connector, "_SSL_CONTEXT_VERIFIED",
                        ssl.create_default_context(cafile=ca_crt))
    _JSONHandler.hits = 0
    _JSONHandler.connections = 0
    _JSONHandler.seen_hosts = []
    srv, sni = _tls_server_for(ca_crt, leaf_crt, leaf_key)
    port = srv.server_address[1]
    dns.script(["127.0.0.1"])
    try:
        out = asyncio.run(_adapter(
            f"https://{CONTROL_HOST}:{port}/v1/chat/completions").execute(
            {"prompt": "hi"}))
    finally:
        srv.shutdown()
        srv.server_close()
    assert out["ok"] is True and out["result"]["answer"] == "public answer"
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]   # Host: original
    assert sni == [CONTROL_HOST]                                   # SNI: original


def test_aiohttp_https_rejects_a_certificate_for_another_hostname(
        dns, loopback_is_public, tmp_path, monkeypatch):
    # Same CA (trusted), but the leaf is for another name: the handshake must
    # fail specifically on HOSTNAME MISMATCH — verification targets the
    # original URL hostname, not the vetted IP.
    ca_crt, _crt, _key = _make_certs(tmp_path, "localhost")
    _, leaf_crt, leaf_key = _make_certs(tmp_path, "other.test")
    import aiohttp.connector
    monkeypatch.setattr(aiohttp.connector, "_SSL_CONTEXT_VERIFIED",
                        ssl.create_default_context(cafile=ca_crt))
    _JSONHandler.hits = 0
    _JSONHandler.connections = 0
    srv, _sni = _tls_server_for(ca_crt, leaf_crt, leaf_key)
    port = srv.server_address[1]
    dns.script(["127.0.0.1"])
    try:
        out = asyncio.run(_adapter(
            f"https://{CONTROL_HOST}:{port}/v1/chat/completions").execute(
            {"prompt": "hi"}))
    finally:
        srv.shutdown()
        srv.server_close()
    assert out["ok"] is False
    err = str(out["result"]["error"])
    assert "Hostname mismatch" in err or "hostname mismatch" in err.lower()
    assert _JSONHandler.hits == 0            # no request was ever served


def test_aiohttp_allowed_ipv6_connection_reaches_the_vetted_address(
        dns, loopback_is_public):
    _JSONHandler.hits = 0
    _JSONHandler.connections = 0
    _JSONHandler.seen_hosts = []
    dns.script(["::1"])
    srv = _serve("::1", _JSONHandler)
    port = srv.server_address[1]
    try:
        out = asyncio.run(_adapter(
            f"http://{CONTROL_HOST}:{port}/v1/chat/completions").execute(
            {"prompt": "hi"}))
    finally:
        srv.shutdown()
        srv.server_close()
    assert out["ok"] is True and out["result"]["answer"] == "public answer"
    assert _JSONHandler.hits == 1
    assert _JSONHandler.seen_hosts == [f"{CONTROL_HOST}:{port}"]


@pytest.mark.parametrize("code,final_method", [
    (301, "GET"), (302, "GET"), (303, "GET"),   # after a POST -> GET, no body
    (307, "POST"), (308, "POST"),               # keep the method and the body
])
def test_all_redirect_codes_keep_their_method_semantics(
        code, final_method, dns, loopback_is_public):
    # The full SEC-01 method matrix through the guarded transport.  The
    # redirect stays SAME-ORIGIN (same host and port, different path), so the
    # agent key must survive the hop; 307/308 keep POST + body + the key,
    # 301/302/303 after a POST switch to GET without a body.
    class _RedirectThenJSON(http.server.BaseHTTPRequestHandler):
        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.path.endswith("/final"):
                type(self).hits += 1
                type(self).seen_hosts.append(self.headers.get("Host"))
                type(self).last_request = (self.command, self.path,
                                           self.headers.get("Authorization"),
                                           self.headers.get("Content-Type"),
                                           body)
                out = json.dumps({"choices": [{"message": {"content": "public answer"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(code)
            self.send_header("Location", "/v1/chat/completions/final")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = _serve

        def log_message(self, *a):
            pass

    _RedirectThenJSON.hits = 0
    _RedirectThenJSON.seen_hosts = []
    _RedirectThenJSON.last_request = None
    srv = _serve("127.0.0.1", _RedirectThenJSON)
    port = srv.server_address[1]
    try:
        out = asyncio.run(_adapter(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            api_key="k").execute({"prompt": "hi"}))
    finally:
        srv.shutdown()
        srv.server_close()
    assert out["ok"] is True and out["result"]["answer"] == "public answer"
    assert _RedirectThenJSON.seen_hosts == [f"127.0.0.1:{port}"]
    method, path, auth, ctype, body = _RedirectThenJSON.last_request
    assert method == final_method
    assert auth == "Bearer k"                     # same origin keeps the key
    if final_method == "POST":
        # 307/308: the method, the body AND the content type all survive
        assert json.loads(body)["messages"][0]["content"] == "hi"
        assert ctype == "application/json"
    else:
        # 301/302/303 after a POST: GET without a body or a body content type
        assert body == b""
        assert ctype is None                        # GET carries no body
