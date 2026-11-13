"""R-205 (D-154): BYOK vault + key test — no database needed.

Proves, with FAKE keys and loopback HTTP servers only (no real provider, no
credit spent):
* envelope encryption on the R-189 cipher: ciphertext never contains the key,
  the AAD binds (key id, workspace, user, provider), a different KEK is
  detected and refused, the KEK is never printed;
* the key test sends the key only to the provider endpoint in the right
  header, never forwards it on a redirect, never reads the provider's body
  into the result (bodies that echo the key do not leak), and reports an
  honest reason for every failure class (wrong key, forbidden, outage,
  rate limit, timeout, unreachable, blocked private address);
* validation errors never echo the submitted key — including FastAPI's
  default 422 body (negative control: the default DOES echo it).
"""
from __future__ import annotations

import base64
import http.server
import logging
import os
import socket
import threading
import time
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent_civilization.core.secret_store import MasterKeyError, SecretStoreError
from agos_v2 import byok
from agos_v2.byok import (PROVIDERS, InvalidInput, KeyIn, KeyTester, KeyVault, TestLimiter,
                          VaultConfigError, VaultKeyMismatch, parse_body, validate_base_url,
                          validate_secret)

FAKE = "sk-test-FAKE-" + uuid.uuid4().hex + "-wxyz"  # never a real key


def _ids():
    return dict(key_id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()),
                user_id=str(uuid.uuid4()), provider="openai")


def _row(sealed, ids):
    return {"id": ids["key_id"], "workspace_id": ids["workspace_id"], "user_id": ids["user_id"],
            "provider": ids["provider"], **sealed}


# ================================================================ vault

def test_seal_open_roundtrip_and_ciphertext_holds_no_key():
    v = KeyVault(os.urandom(32))
    ids = _ids()
    s = v.seal(**ids, secret=FAKE)
    assert s["last4"] == FAKE[-4:] and s["kek_id"] == v.kek_id
    for k in ("key_ct", "key_nonce", "dek_wrapped", "dek_nonce"):
        b = bytes(s[k])
        assert FAKE.encode() not in b and FAKE[:-4].encode() not in b
        assert base64.b64encode(FAKE.encode()) not in b
    assert v.open(_row(s, ids)) == FAKE
    # a fresh data key per seal: same key, different ciphertext
    s2 = v.seal(**ids, secret=FAKE)
    assert s2["key_ct"] != s["key_ct"] and s2["dek_wrapped"] != s["dek_wrapped"]


@pytest.mark.parametrize("field", ["key_id", "workspace_id", "user_id", "provider"])
def test_ciphertext_is_bound_to_its_row(field):
    v = KeyVault(os.urandom(32))
    ids = _ids()
    s = v.seal(**ids, secret=FAKE)
    moved = _row(s, ids)
    moved["id" if field == "key_id" else field] = "anthropic" if field == "provider" else str(uuid.uuid4())
    with pytest.raises(SecretStoreError) as e:
        v.open(moved)
    assert FAKE not in str(e.value) and FAKE[-4:] not in str(e.value)


def test_a_different_kek_is_refused_not_decrypted():
    ids = _ids()
    s = KeyVault(os.urandom(32)).seal(**ids, secret=FAKE)
    other = KeyVault(os.urandom(32))
    with pytest.raises(VaultKeyMismatch):
        other.open(_row(s, ids))
    # even with a forged matching kek_id the cipher refuses (fail-closed)
    forged = _row({**s, "kek_id": other.kek_id}, ids)
    with pytest.raises(SecretStoreError):
        other.open(forged)


def test_tampered_ciphertext_is_refused():
    v = KeyVault(os.urandom(32))
    ids = _ids()
    s = v.seal(**ids, secret=FAKE)
    bad = bytearray(s["key_ct"])
    bad[0] ^= 1
    with pytest.raises(SecretStoreError):
        v.open(_row({**s, "key_ct": bytes(bad)}, ids))


def test_kek_from_env_rules_and_kek_never_printed():
    kek = os.urandom(32)
    b64 = base64.b64encode(kek).decode()
    assert KeyVault.from_env({}) is None
    assert KeyVault.from_env({"AGOS_BYOK_KEK": "  "}) is None
    v = KeyVault.from_env({"AGOS_BYOK_KEK": b64})
    assert v.kek_id == KeyVault(kek).kek_id
    assert KeyVault.from_env({"AGOS_BYOK_KEK": base64.urlsafe_b64encode(kek).decode().rstrip("=")}).kek_id == v.kek_id
    assert b64 not in repr(v) and kek.hex() not in repr(v) and v.kek_id in repr(v)
    for bad in ("not base64 !!", base64.b64encode(os.urandom(31)).decode(),
                base64.b64encode(os.urandom(33)).decode()):
        with pytest.raises(VaultConfigError) as e:
            KeyVault.from_env({"AGOS_BYOK_KEK": bad})
        assert bad not in str(e.value)
    with pytest.raises(MasterKeyError):
        KeyVault(b"short")


def test_server_refuses_to_boot_with_a_malformed_kek(tmp_path):
    import subprocess
    import sys
    env = {**os.environ, "AGOS_ENV": "test", "AGOS_BYOK_KEK": "definitely-not-32-bytes"}
    p = subprocess.run([sys.executable, "-c", "import server"], cwd=os.path.dirname(os.path.dirname(__file__)),
                       env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode != 0
    assert "VaultConfigError" in p.stderr and "definitely-not-32-bytes" not in p.stderr + p.stdout


# ============================================================== input

def test_secret_and_url_validation_never_echo_the_value():
    for bad in ("short", "x" * 513, "has space inside-1234", "tab\tinside-1234", "عربي-مفتاح-1234", 12345678):
        with pytest.raises(InvalidInput) as e:
            validate_secret(bad)
        assert str(bad) not in str(e.value)
    assert validate_secret(FAKE) == FAKE
    assert validate_base_url("http://localhost:11434/") == "http://localhost:11434"
    for bad in ("ftp://host:1/x", "http://user:" + FAKE + "@host:1", "http://h:1/?k=" + FAKE,
                "http://h:1/#" + FAKE, "nohost", "http://"):
        with pytest.raises(InvalidInput) as e:
            validate_base_url(bad)
        assert FAKE not in str(e.value)


class Naive(BaseModel):
    model_config = {"extra": "forbid"}
    provider: str
    label: str


def _mini_app():
    app = FastAPI()

    @app.post("/safe")
    async def safe(request: Request):
        body = await parse_body(request, KeyIn)
        return {"provider": body.provider}

    @app.post("/naive")
    async def naive(body: Naive):  # FastAPI's default validation
        return {}
    return app


def test_validation_422_never_echoes_the_key_but_fastapi_default_does():
    c = TestClient(_mini_app())
    bodies = [{"provider": "openai", "label": "x", "apiKey": FAKE},           # extra field
              {"provider": "openai", "label": FAKE * 3},                       # too long
              {"provider": ["openai", FAKE], "label": "x"},                     # wrong type
              {"provider": "openai", "label": "x", "api_key": FAKE, "secret": FAKE}]
    for b in bodies:
        safe = c.post("/safe", json=b)
        assert safe.status_code == 422, safe.text
        assert FAKE not in safe.text and FAKE[:12] not in safe.text
    # negative control — the threat is real: the default 422 echoes the input
    naive = c.post("/naive", json=bodies[0])
    assert naive.status_code == 422 and FAKE in naive.text
    assert c.post("/safe", content=b"{not json").status_code == 422
    assert c.post("/safe", json=[FAKE]).status_code == 422 and FAKE not in c.post("/safe", json=[FAKE]).text


# ============================================================ key test

class FakeProvider:
    """A loopback HTTP server standing in for a provider."""

    def __init__(self, handler):
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                handler(self)

            def log_message(self, *a):  # silence
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _reply(status, body=b"{}", headers=()):
    def h(req):
        req.send_response(status)
        for k, v in headers:
            req.send_header(k, v)
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)
    return h


@pytest.fixture
def provider_server():
    made = []

    def make(handler):
        fp = FakeProvider(handler)
        made.append(fp)
        return fp
    yield make
    for fp in made:
        fp.close()


def _tester(url, **kw):
    return KeyTester(endpoints={p: url + "/v1/models" for p in PROVIDERS}, allow_private=True, **kw)


@pytest.mark.parametrize("pid,header,value", [
    ("openai", "authorization", f"Bearer {FAKE}"), ("anthropic", "x-api-key", FAKE),
    ("google", "x-goog-api-key", FAKE), ("openrouter", "authorization", f"Bearer {FAKE}"),
    ("mistral", "authorization", f"Bearer {FAKE}"), ("groq", "authorization", f"Bearer {FAKE}")])
def test_key_reaches_the_provider_only_in_its_auth_header(provider_server, pid, header, value):
    fp = provider_server(_reply(200))
    import asyncio
    res = asyncio.run(_tester(fp.url).test(PROVIDERS[pid], FAKE, None))
    assert res == byok.TestResult(True, "ok", 200)
    path, headers = fp.requests[0]
    assert headers[header] == value and FAKE not in path
    if pid == "anthropic":
        assert headers["anthropic-version"] == "2023-06-01"
    others = {k: v for k, v in headers.items() if k != header}
    assert FAKE not in repr(others)


def test_ollama_without_key_sends_no_auth_and_uses_the_user_url(provider_server):
    fp = provider_server(_reply(200))
    import asyncio
    t = KeyTester(allow_private=True)
    assert t.url_for(PROVIDERS["ollama"], fp.url) == fp.url + "/api/tags"
    res = asyncio.run(t.test(PROVIDERS["ollama"], None, fp.url))
    assert res.ok and fp.requests[0][0] == "/api/tags" and "authorization" not in fp.requests[0][1]


@pytest.mark.parametrize("status,reason", [(401, "invalid_key"), (403, "forbidden"),
                                           (404, "endpoint_not_found"), (429, "rate_limited"),
                                           (500, "provider_unavailable"), (503, "provider_unavailable"),
                                           (418, "unexpected_status")])
def test_failures_are_honest_and_echoing_bodies_do_not_leak(provider_server, status, reason, caplog):
    echo = (f'{{"error":"Incorrect API key provided: {FAKE}"}}').encode()
    fp = provider_server(_reply(status, echo))
    import asyncio
    caplog.set_level(logging.DEBUG)
    res = asyncio.run(_tester(fp.url).test(PROVIDERS["openai"], FAKE, None))
    assert res == byok.TestResult(False, reason, status)
    assert FAKE not in repr(res) and FAKE not in caplog.text and FAKE[:-4] not in caplog.text


def test_redirect_never_carries_the_key_and_is_not_success(provider_server):
    sink = provider_server(_reply(200))
    fp = provider_server(_reply(302, b"", headers=(("Location", sink.url + "/stolen"),)))
    import asyncio
    res = asyncio.run(_tester(fp.url).test(PROVIDERS["openai"], FAKE, None))
    assert res.ok is False and res.reason == "unexpected_redirect"
    for _, headers in sink.requests:  # urllib followed — but without the key
        assert FAKE not in repr(headers) and "authorization" not in headers


def test_outage_classes_timeout_unreachable_blocked(provider_server):
    import asyncio

    def slow(req):
        time.sleep(2)
        _reply(200)(req)
    fp = provider_server(slow)
    assert asyncio.run(_tester(fp.url, timeout=0.3).test(PROVIDERS["openai"], FAKE, None)).reason == "timeout"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    res = asyncio.run(_tester(f"http://127.0.0.1:{dead}").test(PROVIDERS["openai"], FAKE, None))
    assert res == byok.TestResult(False, "unreachable", None)
    # a private/loopback URL is refused by the SEC-05 guard when not allowed
    ok_srv = provider_server(_reply(200))
    t = KeyTester(allow_private=False)
    res = asyncio.run(t.test(PROVIDERS["ollama"], None, ok_srv.url))
    assert res == byok.TestResult(False, "blocked_address", None) and ok_srv.requests == []


def test_cloud_endpoints_are_fixed_https_free_endpoints():
    for p in PROVIDERS.values():
        if p.id == "ollama":
            assert p.test_url is None and not p.needs_key
            continue
        assert p.test_url.startswith("https://") and p.needs_key
        # model list / key info only — never a generation endpoint (no credit spent)
        assert any(s in p.test_url for s in ("/models", "/api/v1/key"))
        assert "completions" not in p.test_url and "generate" not in p.test_url


def test_test_limiter_caps_per_user():
    lim = TestLimiter(limit=3, window_s=60)
    assert [lim.allow("u1") for _ in range(4)] == [True, True, True, False]
    assert lim.allow("u2") is True
