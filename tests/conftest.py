"""Shared pytest configuration.

R-072 — network isolation (RISK-019)
-----------------------------------
Three real agents reach the internet: ``researcher`` (DuckDuckGo Instant Answer
API), ``api_integrator`` (HTTP GET) and ``network_client`` (DNS + HTTP HEAD).
Before R-072, five deterministic tests failed whenever the machine had no
network (docs/audit/M6/logs/38): the result depended on DuckDuckGo, not on the code.

Now, for every test that is NOT marked ``network``:
  * ``urllib.request.urlopen`` (and ``OpenerDirector.open``, used by
    ``safety.safe_urlopen`` since R-100) to a non-loopback host is answered by a local
    fake transport with realistic, deterministic payloads (the agent's own
    request building, parsing and result shaping still run for real);
  * ``socket.gethostbyname`` / ``getaddrinfo`` for non-loopback names return a
    TEST-NET address (192.0.2.x, RFC 5737) instead of hitting DNS (R-100: the
    outbound URL policy resolves every host with ``getaddrinfo`` before
    connecting, so the fake answers it too — real DNS is still never queried);
  * any real ``socket.connect`` to a non-loopback address raises, so a new
    hidden network dependency fails loudly instead of flaking.
Loopback (127.0.0.1 / localhost / ::1) is untouched — local servers such as
the R-042 PostgREST stack keep working.

Tests that must touch the real internet carry ``@pytest.mark.network`` and are
deselected by default (pytest.ini); run them with ``pytest -m network``
(CI job ``network``). ``@pytest.mark.allow_external_network`` lifts the guard
without deselecting (used by the R-042 live-storage tests, which may target a
hosted Supabase project when R042_LIVE=1).
"""

import io
import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from email.message import Message

import pytest

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "testserver"}
_ORIG_URLOPEN = urllib.request.urlopen
_ORIG_OPENER_OPEN = urllib.request.OpenerDirector.open
_ORIG_GETHOSTBYNAME = socket.gethostbyname
_ORIG_GETADDRINFO = socket.getaddrinfo
_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex


class ExternalNetworkBlocked(OSError):
    """Raised when a deterministic test tries to open a real external connection."""


def _is_loopback_host(host) -> bool:
    if host is None:
        return True
    host = str(host).strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _fake_ip(host: str) -> str:
    return f"192.0.2.{(sum(host.encode()) % 250) + 1}"


class _FakeResponse(io.BytesIO):
    def __init__(self, url, status, body: bytes, content_type: str):
        super().__init__(body)
        self.status = self.code = status
        self._url = url
        self.headers = Message()
        self.headers["content-type"] = content_type
        self.headers["content-length"] = str(len(body))
        self.headers["server"] = "agos-test-fake"

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _fake_payload(url: str, method: str):
    """Deterministic, shape-faithful responses for the hosts the agents use."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if host == "api.duckduckgo.com":
        q = urllib.parse.parse_qs(parts.query).get("q", [""])[0]
        body = {
            "Heading": q[:60] or "query",
            "AbstractText": f"Deterministic test abstract for: {q[:80]}",
            "AbstractURL": "https://example.org/test-abstract",
            "RelatedTopics": [{"Text": f"Related topic {i} for {q[:30]}"} for i in range(3)],
        }
        return 200, json.dumps(body).encode(), "application/x-javascript"
    if host == "html.duckduckgo.com":
        # R-116: the live web-search endpoint (DuckDuckGo HTML), shape-faithful
        rows = "".join(
            f'<div class="result"><a rel="nofollow" class="result__a" '
            f'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fdoc{i}">Test result {i}</a>'
            f'<a class="result__snippet" href="#">Deterministic snippet {i}.</a></div>' for i in range(3))
        return 200, f"<html><body>{rows}</body></html>".encode(), "text/html; charset=utf-8"
    if host == "api.mymemory.translated.net":
        # R-116: the live translation service (MyMemory), shape-faithful
        qs = urllib.parse.parse_qs(parts.query)
        q, pair = qs.get("q", [""])[0], qs.get("langpair", ["en|fr"])[0]
        body = {"responseStatus": 200, "responseDetails": "",
                "responseData": {"translatedText": f"[{pair.split('|')[-1]}] {q}", "match": 1}}
        return 200, json.dumps(body).encode(), "application/json; charset=utf-8"
    if method == "HEAD":
        return 200, b"", "text/html; charset=utf-8"
    body = {"fake": True, "host": host, "path": parts.path or "/", "note": "R-072 offline transport"}
    return 200, json.dumps(body).encode(), "application/json; charset=utf-8"


@pytest.fixture(autouse=True)
def _offline_external_network(request, monkeypatch):
    if request.node.get_closest_marker("network") or request.node.get_closest_marker("allow_external_network"):
        yield None
        return
    calls = []

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        method = req.get_method() if isinstance(req, urllib.request.Request) else "GET"
        if _is_loopback_host(urllib.parse.urlsplit(url).hostname):
            return _ORIG_URLOPEN(req, *args, **kwargs)
        calls.append((method, url))
        status, body, ctype = _fake_payload(url, method)
        return _FakeResponse(url, status, body, ctype)

    def fake_opener_open(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        req = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl)
        if _is_loopback_host(urllib.parse.urlsplit(req.full_url).hostname):
            return _ORIG_OPENER_OPEN(self, fullurl, data, timeout)
        calls.append((req.get_method(), req.full_url))
        status, body, ctype = _fake_payload(req.full_url, req.get_method())
        return _FakeResponse(req.full_url, status, body, ctype)

    def fake_gethostbyname(host):
        return _ORIG_GETHOSTBYNAME(host) if _is_loopback_host(host) else _fake_ip(host)

    def fake_getaddrinfo(host, *args, **kwargs):
        if _is_loopback_host(host):
            return _ORIG_GETADDRINFO(host, *args, **kwargs)
        port = args[0] if args else kwargs.get("port", 0)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (_fake_ip(str(host)), int(port or 0)))]

    def _guard(addr):
        host = addr[0] if isinstance(addr, tuple) else None
        if isinstance(addr, tuple) and not _is_loopback_host(host):
            raise ExternalNetworkBlocked(
                f"external network disabled in deterministic tests (connect to {addr!r}); "
                "mark the test @pytest.mark.network if it really needs the internet")

    def guarded_connect(self, addr):
        _guard(addr)
        return _ORIG_CONNECT(self, addr)

    def guarded_connect_ex(self, addr):
        _guard(addr)
        return _ORIG_CONNECT_EX(self, addr)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_opener_open)
    monkeypatch.setattr(socket, "gethostbyname", fake_gethostbyname)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield calls


@pytest.fixture(autouse=True)
def _code_execution_policy(request, monkeypatch):
    """R-100 (D-044): every test starts from the production default — open mode,
    no ``AGOS_ALLOW_CODE_EXEC`` — so ``test_runner`` refuses to run code.
    Tests that must prove real execution carry ``@pytest.mark.code_exec``,
    which is the same explicit opt-in an operator gives in production."""
    monkeypatch.delenv("AGOS_API_KEY", raising=False)
    monkeypatch.delenv("AGOS_ALLOW_PRIVATE_NETWORK", raising=False)
    if request.node.get_closest_marker("code_exec"):
        monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")
    else:
        monkeypatch.delenv("AGOS_ALLOW_CODE_EXEC", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_storage_outcomes():
    """R-082: storage outcome stats are process-wide; isolate them per test so a
    failure recorded by one test cannot turn /health degraded in the next."""
    from agent_civilization.storage import reset_storage_stats
    reset_storage_stats()
    yield
    reset_storage_stats()


@pytest.fixture(autouse=True)
def _fresh_experience(monkeypatch):
    """R-103: memory/learning are process-wide; every test starts empty and never
    writes a persistence file unless it sets one itself."""
    from agent_civilization.core import experience
    monkeypatch.delenv("AGOS_MEMORY_FILE", raising=False)
    experience.reset()
    yield
    experience.reset()



@pytest.fixture(autouse=True)
def _no_keyless_llm_by_default(request, monkeypatch):
    """R-116: the runtime falls back to a keyless public LLM when no
    OPENROUTER_API_KEY is set. Offline unit tests must stay deterministic, so
    that fallback is switched off unless a test is marked ``network`` (live
    check of the keyless provider) or sets the variable itself."""
    if not request.node.get_closest_marker("network"):
        monkeypatch.setenv("AGOS_LLM_KEYLESS", "0")
    yield


@pytest.fixture
def tenant_auth(monkeypatch):
    """SEC-02 (R-138): an authenticated identity for tests that exercise the
    tenant-data API (tasks, chat, memory, events). Every tenant-data route now
    refuses anonymous requests, so such tests must authenticate like a real
    client. Patches the imported server's credentials with random per-test keys:
    a user of tenant ``test_tenant`` and an admin of tenant ``test_admin``.
    Code execution policy is unaffected (it still follows AGOS_API_KEY / R-100)."""
    import hashlib
    import secrets
    from types import SimpleNamespace

    import server
    from agent_civilization.core.tenancy import Principal

    key, admin_key = "tt-" + secrets.token_urlsafe(16), "ta-" + secrets.token_urlsafe(16)
    creds = {hashlib.sha256(key.encode()).hexdigest(): Principal("test_tenant", "user"),
             hashlib.sha256(admin_key.encode()).hexdigest(): Principal("test_admin", "admin")}
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    monkeypatch.setattr(server, "ENABLE_AUTH", True)
    return SimpleNamespace(key=key, admin_key=admin_key, tenant="test_tenant",
                           headers={"X-API-Key": key}, admin_headers={"X-API-Key": admin_key})


@pytest.fixture
def tenant_context():
    """SEC-02: code that calls the experience layer directly (not through HTTP)
    runs as tenant ``test_tenant`` — with no tenant the layer stores and recalls
    nothing by design. asyncio.run() copies this context into the test's loop."""
    from agent_civilization.core import tenancy
    token = tenancy.set_tenant("test_tenant")
    yield "test_tenant"
    tenancy.reset_tenant(token)


@pytest.fixture(autouse=True)
def _fresh_rate_limit_window():
    """Test isolation: the rate limiter's per-client window
    (``server.request_counts``, 100 requests / 60 s for "testclient") used to
    carry over between test files. The whole suite runs inside one window, so
    the count stood at 95/100 before tests/test_r131_keyless_admission.py on
    main 53f9343 and slower CI runners crossed it (429 instead of the route's
    answer). Each test starts with an empty window; rate-limit behaviour inside
    a test is unchanged. Measured in docs/audit/M7/logs/raw/224."""
    import sys
    srv = sys.modules.get("server")
    if srv is not None and hasattr(srv, "request_counts"):
        srv.request_counts.clear()
    yield
