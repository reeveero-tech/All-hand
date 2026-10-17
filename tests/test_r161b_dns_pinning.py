"""R-161b (G-161-3): DNS pinning at the CONNECTION layer for the storage and
LLM clients used by the integration runner.

Before this batch the runner checked DNS once (preflight, ``safety.check_url``)
and the clients then resolved AGAIN when opening the socket — a rebinding
window. Now ``guarded=True`` clients use the SEC-05 ``GuardedResolver``
(external_adapter.guarded_session_kwargs): one resolution per connection,
every address vetted, connection to the vetted IP literal, no DNS cache,
verifying TLS with the ORIGINAL hostname (Host + SNI), redirects refused.

Evidence class: mock — a scripted resolver (socket.getaddrinfo) and listeners
on 127.0.0.x. Nothing here is a live integration. Every listener counts TCP
CONNECTIONS (not only requests), so "no connection happened" is measured.
"""
import asyncio
import datetime as dt
import http.server
import json
import pathlib
import socket
import ssl
import subprocess
import sys
import threading

import pytest

from agent_civilization.agents.external_adapter import GuardedResolver, guarded_session_kwargs
from agent_civilization.core import safety
from agent_civilization.core.llm import LLMClient
from agent_civilization.storage import supabase_store as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import r161_live_integration as R  # noqa: E402

HOST = "pinned.test"            # the only name the scripted resolver answers
PUBLIC = "192.0.2.10"           # RFC 5737: policy-public (safety._DOC_NETS)
VETTED = "127.0.0.2"            # the listener that may be reached
INTERNAL = "127.0.0.3"          # the internal decoy that must never be reached
SECRET = "r161b-SYNTHETIC-" + "k" * 24


class ScriptedDNS:
    """socket.getaddrinfo for HOST: answer N for call N (the last repeats)."""

    def __init__(self, monkeypatch, *answers, fail=False):
        self.orig, self.answers, self.calls, self.fail = socket.getaddrinfo, [list(a) for a in answers], 0, fail
        monkeypatch.setattr(socket, "getaddrinfo", self._fake)

    def _fake(self, host, port=0, *a, **kw):
        if str(host) != HOST:
            return self.orig(host, port, *a, **kw)
        self.calls += 1
        if self.fail:
            raise socket.gaierror(2, "Name or service not known")
        ans = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, int(port or 0))) for ip in ans]


def _handler(mode="json", location=""):
    class H(http.server.BaseHTTPRequestHandler):
        connections, hits, hosts = 0, 0, []

        def setup(self):
            type(self).connections += 1
            super().setup()

        def _serve(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            type(self).hits += 1
            type(self).hosts.append(self.headers.get("Host"))
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.command == "POST" and "/rest/v1/" in self.path:
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"choices": [{"message": {"content": "PONG"}}], "model": "m"}).encode() \
                if "/rest/v1/" not in self.path else b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PATCH = do_DELETE = _serve

        def log_message(self, *a):
            pass
    return H


class Listener:
    def __init__(self, ip, handler, port=0, tls_ctx=None):
        self.handler = handler
        self.srv = http.server.ThreadingHTTPServer((ip, port), handler)
        if tls_ctx is not None:
            self.srv.socket = tls_ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _free_port_on_both():
    """A port free on 127.0.0.2 AND 127.0.0.3, so a URL port reaches either."""
    for _ in range(50):
        s = socket.socket()
        s.bind((VETTED, 0))
        p = s.getsockname()[1]
        t = socket.socket()
        try:
            t.bind((INTERNAL, p))
            t.close()
            s.close()
            return p
        except OSError:
            s.close()
            t.close()
    raise RuntimeError("no common free port")


@pytest.fixture
def vetted_is_public(monkeypatch):
    """127.0.0.2 plays a PUBLIC allowed destination (the live policy, allow_private=False)."""
    real = safety.is_public_address
    monkeypatch.setattr(safety, "is_public_address", lambda ip: True if ip == VETTED else real(ip))


@pytest.fixture
def decoy():
    port = _free_port_on_both()
    d = Listener(INTERNAL, _handler(), port=port)
    yield d
    d.close()


def _live_env(target, url):
    exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
    env = {"R161_LIVE": "1", "R161_ALLOWED_HOSTS": HOST, "R161_CREDENTIAL_EXPIRES_AT": exp, "R161_TIMEOUT_S": "5"}
    if target == "storage":
        env.update(R161_STORAGE_URL=url, R161_STORAGE_KEY=SECRET)
    else:
        env.update(R161_LLM_URL=url + "/v1/chat/completions", R161_LLM_API_KEY=SECRET)
    return env


_MP = []


@pytest.fixture(autouse=True)
def _store_globals(monkeypatch):
    """SupabaseStorage reads SUPABASE_URL/KEY at call time: keep them set for
    the whole test (restored by monkeypatch)."""
    _MP.append(monkeypatch)
    yield
    _MP.pop()


def _store(url, **kw):
    _MP[-1].setattr(st, "SUPABASE_URL", url)
    _MP[-1].setattr(st, "SUPABASE_KEY", SECRET)
    return st.SupabaseStorage(**kw)


# ===================== A. rebinding: public at the check, internal at connect

@pytest.mark.parametrize("target", ["storage", "llm"])
def test_rebinding_between_preflight_and_connect_never_reaches_the_internal_listener(monkeypatch, decoy, target):
    """The runner's live path: the preflight (safety.check_url) sees PUBLIC;
    every later resolution answers the internal decoy. The connection-time
    resolution goes through GuardedResolver, which refuses it — the decoy sees
    ZERO TCP connections and the target FAILs (never PASS)."""
    dns = ScriptedDNS(monkeypatch, [PUBLIC], [INTERNAL])
    rep = asyncio.run(R.run([target], _live_env(target, f"https://{HOST}:{decoy.port}")))
    r = next(x for x in rep["results"] if x["target"] == target)
    assert r["evidence_class"] == "live_integration" and r["outcome"] == "FAIL", r
    assert r["connected"] is False
    assert decoy.handler.connections == 0 and decoy.handler.hits == 0
    assert dns.calls >= 2                       # the connect-time answer WAS produced — and refused
    assert SECRET not in json.dumps(rep)


@pytest.mark.parametrize("which", ["storage", "llm"])
def test_guarded_clients_refuse_an_internal_answer_at_connect(monkeypatch, decoy, which):
    dns = ScriptedDNS(monkeypatch, [INTERNAL])
    url = f"http://{HOST}:{decoy.port}"

    async def go():
        if which == "storage":
            s = _store(url, guarded=True, allow_private=False)
            ok = await s.insert_task("abcdef012345", "analyst", "x", tenant_id="r161b")
            await s.close()
            return ok
        c = LLMClient(api_key=SECRET, url=url + "/v1/chat/completions", guarded=True)
        out = await c.complete("s", "p", max_tokens=4)
        await c.close()
        return out["ok"]
    assert asyncio.run(go()) is False
    assert decoy.handler.connections == 0 and dns.calls == 1


# ============ B. allowed connection: the connected address IS the vetted one

@pytest.mark.parametrize("which", ["storage", "llm"])
def test_allowed_connection_goes_to_the_vetted_address_once(monkeypatch, vetted_is_public, decoy, which):
    """Policy = live (allow_private=False); 127.0.0.2 is marked public. Call 1
    answers 127.0.0.2, any further call would answer the decoy. One request:
    exactly one resolution, the 127.0.0.2 listener (the only one bound to that
    address) got the connection, the decoy none, and Host is the original name."""
    good = Listener(VETTED, _handler(), port=decoy.port)
    dns = ScriptedDNS(monkeypatch, [VETTED], [INTERNAL])
    url = f"http://{HOST}:{good.port}"
    try:
        async def go():
            if which == "storage":
                s = _store(url, guarded=True, allow_private=False)
                ok = await s.insert_task("abcdef012345", "analyst", "x", tenant_id="r161b")
                await s.close()
                return ok
            c = LLMClient(api_key=SECRET, url=url + "/v1/chat/completions", guarded=True)
            out = await c.complete("s", "p", max_tokens=4)
            await c.close()
            return out["ok"]
        assert asyncio.run(go()) is True
        assert dns.calls == 1
        assert good.handler.connections == 1 and good.handler.hits == 1
        assert good.handler.hosts == [f"{HOST}:{good.port}"]
        assert decoy.handler.connections == 0
    finally:
        good.close()


def test_guarded_resolver_hands_the_connector_the_vetted_literal(monkeypatch, vetted_is_public):
    ScriptedDNS(monkeypatch, [VETTED])
    res = asyncio.run(GuardedResolver(False).resolve(HOST, 443))
    assert [(x["host"], x["hostname"], x["port"]) for x in res] == [(VETTED, HOST, 443)]


# ===================== C. whole answer checked, failures fail closed

@pytest.mark.parametrize("answer", [[PUBLIC, INTERNAL], [INTERNAL, PUBLIC], ["10.0.0.7"], ["169.254.169.254"]])
@pytest.mark.parametrize("which", ["storage", "llm"])
def test_mixed_or_internal_answers_are_rejected_as_a_whole(monkeypatch, decoy, answer, which):
    ScriptedDNS(monkeypatch, answer)
    url = f"http://{HOST}:{decoy.port}"

    async def go():
        if which == "storage":
            s = _store(url, guarded=True)
            ok = await s.insert_task("abcdef012345", "analyst", "x", tenant_id="r161b")
            await s.close()
            return ok
        c = LLMClient(api_key=SECRET, url=url + "/v1/chat/completions", guarded=True)
        out = await c.complete("s", "p", max_tokens=4)
        await c.close()
        return out["ok"]
    assert asyncio.run(go()) is False
    assert decoy.handler.connections == 0


@pytest.mark.parametrize("which", ["storage", "llm"])
def test_dns_failure_fails_closed(monkeypatch, decoy, which):
    ScriptedDNS(monkeypatch, [INTERNAL], fail=True)
    url = f"http://{HOST}:{decoy.port}"

    async def go():
        if which == "storage":
            s = _store(url, guarded=True)
            out = await s.get_tasks(limit=1, scope="r161b")
            await s.close()
            return bool(out)
        c = LLMClient(api_key=SECRET, url=url + "/v1/chat/completions", guarded=True)
        out = await c.complete("s", "p", max_tokens=4)
        await c.close()
        return out["ok"]
    assert asyncio.run(go()) is False
    assert decoy.handler.connections == 0


def test_no_dns_cache_each_connection_is_vetted_again(monkeypatch, vetted_is_public, decoy):
    """Two sessions (two connections): the second resolution answers the
    internal decoy — it is vetted again (no cached pass) and refused."""
    good = Listener(VETTED, _handler(), port=decoy.port)
    dns = ScriptedDNS(monkeypatch, [VETTED], [INTERNAL])
    url = f"http://{HOST}:{good.port}/v1/chat/completions"
    try:
        async def go():
            outs = []
            for _ in range(2):
                c = LLMClient(api_key=SECRET, url=url, guarded=True)
                outs.append((await c.complete("s", "p", max_tokens=4))["ok"])
                await c.close()
            return outs
        assert asyncio.run(go()) == [True, False]
        assert dns.calls == 2 and good.handler.connections == 1 and decoy.handler.connections == 0
    finally:
        good.close()

    async def inspect():
        kw = guarded_session_kwargs(False)
        try:
            return kw["connector"]._use_dns_cache, type(kw["connector"]._resolver)
        finally:
            await kw["connector"].close()
    assert asyncio.run(inspect()) == (False, GuardedResolver)


# ================================================ D. redirects are refused

@pytest.mark.parametrize("which", ["storage", "llm"])
def test_redirects_are_refused_and_the_target_never_contacted(monkeypatch, vetted_is_public, decoy, which):
    """127.0.0.2 (allowed) answers 307 to the same name on another port that
    would resolve to the decoy: the guarded session refuses the redirect."""
    redir = Listener(VETTED, _handler("redirect", f"http://{HOST}:{decoy.port}/elsewhere"))
    ScriptedDNS(monkeypatch, [VETTED], [INTERNAL])
    url = f"http://{HOST}:{redir.port}"
    try:
        async def go():
            if which == "storage":
                s = _store(url, guarded=True)
                out = await s.get_tasks(limit=1, scope="r161b")
                await s.close()
                return out
            c = LLMClient(api_key=SECRET, url=url + "/v1/chat/completions", guarded=True)
            out = await c.complete("s", "p", max_tokens=4)
            await c.close()
            return out["ok"]
        assert not asyncio.run(go())
        assert redir.handler.hits == 1 and decoy.handler.connections == 0
    finally:
        redir.close()


# ======================================= E. TLS: original hostname kept

def _certs(tmp_path, name):
    ext = tmp_path / f"{name}.ext"
    ext.write_text(f"subjectAltName=DNS:{name}\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n")
    ca_key, ca, key, csr, crt = (tmp_path / f"{name}.{x}" for x in ("cakey", "ca", "key", "csr", "crt"))
    for cmd in (
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=r161b-ca",
         "-keyout", str(ca_key), "-out", str(ca), "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign,digitalSignature"],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(csr)],
        ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
         "-days", "2", "-extfile", str(ext), "-out", str(crt)]):
        subprocess.run(cmd, check=True, capture_output=True)
    return str(ca), str(crt), str(key)


@pytest.mark.parametrize("cert_for,ok", [(HOST, True), ("other.test", False)])
def test_tls_sni_and_verification_use_the_original_hostname(monkeypatch, vetted_is_public, tmp_path, cert_for, ok):
    ca, crt, key = _certs(tmp_path, cert_for)
    monkeypatch.setattr(safety, "tls_context", lambda: ssl.create_default_context(cafile=ca))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(crt, key)
    sni = []
    sctx.sni_callback = lambda sslobj, name, ctx: sni.append(name)
    srv = Listener(VETTED, _handler(), tls_ctx=sctx)
    ScriptedDNS(monkeypatch, [VETTED])
    try:
        async def go():
            c = LLMClient(api_key=SECRET, url=f"https://{HOST}:{srv.port}/v1/chat/completions", guarded=True)
            out = await c.complete("s", "p", max_tokens=4)
            await c.close()
            return out
        out = asyncio.run(go())
        assert out["ok"] is ok, out
        assert sni and sni[0] == HOST                       # SNI = original name, not the IP
        if ok:
            assert srv.handler.hosts == [f"{HOST}:{srv.port}"]
        else:
            assert srv.handler.hits == 0                     # verification failed: no request sent
    finally:
        srv.close()


# ============================================== F. scope of the change

def test_default_clients_are_unchanged_and_the_runner_uses_guarded_ones():
    assert st.SupabaseStorage().guarded is False and LLMClient(api_key="x").guarded is False
    src = (ROOT / "scripts" / "r161_live_integration.py").read_text()
    assert "SupabaseStorage(guarded=True" in src and "guarded=True, allow_private" in src
    assert "**guarded_session_kwargs(self.allow_private)" in src      # the cleanup session too
