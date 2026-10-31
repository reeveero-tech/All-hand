"""G-160-1 (R-170 batch 20, D-105): credentials must never reach a
tenant-visible failure result.

R-149 already reduces an *unexpected exception* to its type. But handlers and
the LLM client catch exceptions themselves and embed their text in
``result["error"]`` (``f"http: {e}"``, ``f"LLM HTTP {status}: {body}"``,
``str(e)`` …) — that text reaches GET /api/tasks verbatim. These tests prove
that a credential inside such text (a configured secret value, URL userinfo,
a secret query parameter, a bearer/basic token, a known key format, a JWT) is
redacted at the two boundaries every path crosses: the agent runtime outcome
(real + specialists). ``LLMClient.complete`` is deliberately unchanged
(fingerprinted by the frozen R-169 experiments); its error text reaches a
tenant only through that outcome, where it is redacted. The non-secret diagnostic
around it is kept.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from agent_civilization.agents.real import real_agents
try:  # red-first: the module does not exist before this batch
    from agent_civilization.core import public_text
except ImportError:  # pragma: no cover - only on the pre-fix tree
    public_text = None
from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.llm import LLMClient
from agent_civilization.agents.real import create_civilization

ENV_KEY = "sk-or-v1-" + "a1b2c3d4e5f6" * 4          # a configured provider key
DB_PW = "Pw-db-9f8e7d6c5b"                           # password inside a URL-valued env var
QUERY_KEY = "AIzaSyD-qq1234567890abcdefghijklmnopqrs"  # Google-style key (39 chars)
BEARER = "tok_live_ZZZZYYYYXXXX1111"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZW5hbnQifQ.c2lnbmF0dXJlLXZhbHVl"
GH = "ghp_" + "Q" * 36
SECRETS = [ENV_KEY, DB_PW, QUERY_KEY, BEARER, JWT, GH, "u53r:p455w0rd"]


def _reset():
    if public_text is not None:
        public_text.reset_cache()


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", ENV_KEY)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://agos:{DB_PW}@db.internal:5432/agos")
    _reset()
    yield
    _reset()


LEAKY = (f"http: 401 from https://u53r:p455w0rd@api.example.com/v1?api_key={QUERY_KEY}&q=x "
         f"Authorization: Bearer {BEARER} key={ENV_KEY} db pw {DB_PW} jwt {JWT} gh {GH}")


def _no_secret(blob: str):
    for s in SECRETS:
        assert s not in blob, s


# ---------------------------------------------------------------- unit
def test_redact_removes_every_credential_kind_and_keeps_the_diagnostic(secret_env):
    out = public_text.redact(LEAKY)
    _no_secret(out)
    assert out.startswith("http: 401 from https://")
    assert "api.example.com/v1" in out and "&q=x" in out and "db pw" in out


@pytest.mark.parametrize("text", [
    "url rejected: host resolves to a private address",
    "http: HTTP Error 404: Not Found",
    "search: no sources found",
    "RuntimeError",
    "",
    "missing key: prompt",
    "tokenize: unexpected token: indent",
    "invalid password policy: too short",
])
def test_redact_leaves_secret_free_text_unchanged(secret_env, text):
    assert public_text.redact(text) == text


def test_public_failure_result_redacts_nested_strings_only(secret_env):
    res = {"ok": False, "error": LEAKY, "detail": {"url": f"https://x/?token={BEARER}"},
           "attempts": 3, "trace": [f"Bearer {BEARER}"]}
    out = public_text.public_failure_result(res)
    _no_secret(json.dumps(out))
    assert out["ok"] is False and out["attempts"] == 3 and set(out) == set(res)
    assert res["error"] == LEAKY            # input not mutated


def test_short_or_flag_like_env_values_are_not_treated_as_secrets(monkeypatch):
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.setenv("SOME_TOKEN_MODE", "true")
    _reset()
    try:
        assert public_text.redact("status 1 is true") == "status 1 is true"
    finally:
        _reset()


# ---------------------------------------------------------------- runtime boundary
@pytest.fixture
def leaky_dispatch(monkeypatch):
    async def leak(spec, work):
        return {"ok": False, "error": LEAKY}
    monkeypatch.setattr(real_agents, "_dispatch", leak)
    monkeypatch.setattr(real_dispatcher, "dispatch", leak)


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_handler_error_text_is_redacted_in_the_outcome(secret_env, leaky_dispatch, impl):
    agent = create_civilization(1, impl=impl)["api_integrator"][0]
    out = asyncio.run(agent.execute({"prompt": "GET https://api.example.com/v1"}))
    assert out["ok"] is False
    _no_secret(json.dumps(out, default=str))
    assert out["result"]["error"].startswith("http: 401")


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_successful_results_are_not_rewritten(secret_env, monkeypatch, impl):
    """Scope: only FAILED results are redacted (a success's content is the
    answer the tenant asked for — rewriting it would change semantics)."""
    async def good(spec, work):
        return {"ok": True, "answer": "value=42", "sources": ["https://example.com"]}
    monkeypatch.setattr(real_agents, "_dispatch", good)
    monkeypatch.setattr(real_dispatcher, "dispatch", good)
    agent = create_civilization(1, impl=impl)["analyst"][0]
    out = asyncio.run(agent.execute({"prompt": "x"}))
    assert out["result"].get("answer") == "value=42"


# ---------------------------------------------------------------- LLM boundary
class _Resp:
    def __init__(self, status, body):
        self.status, self._body, self.headers = status, body, {}

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    closed = False

    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc

    def post(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.resp


def _client(monkeypatch, session):
    c = LLMClient(api_key=ENV_KEY, model="m", url="https://openrouter.ai/api/v1/chat/completions")

    async def gs():
        return session
    monkeypatch.setattr(c, "_get_session", gs)
    return c


def _llm_handler(session, monkeypatch):
    """A handler that embeds the LLM client's error as real handlers do
    (``{"ok": False, "error": out["error"]}``) — the client itself is NOT
    changed (llm.py is fingerprinted by the frozen R-169 experiments); the
    tenant boundary is the agent outcome."""
    c = _client(monkeypatch, session)

    async def handler(spec, work):
        out = await c.complete("s", "p")
        return {"ok": False, "error": out["error"]}
    monkeypatch.setattr(real_agents, "_dispatch", handler)
    monkeypatch.setattr(real_dispatcher, "dispatch", handler)


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_llm_http_error_body_echoing_the_key_is_redacted(secret_env, monkeypatch, impl):
    body = json.dumps({"error": {"message": f"Incorrect API key provided: {ENV_KEY}", "code": 401}})
    _llm_handler(_Session(resp=_Resp(401, body)), monkeypatch)
    out = asyncio.run(create_civilization(1, impl=impl)["communicator"][0].execute({"prompt": "x"}))
    assert out["ok"] is False and out["result"]["error"].startswith("LLM HTTP 401")
    _no_secret(json.dumps(out, default=str))


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_llm_transport_exception_text_is_redacted(secret_env, monkeypatch, impl):
    exc = aiohttp.ClientError(f"Cannot connect to https://u53r:p455w0rd@llm.internal/v1?key={QUERY_KEY}")
    _llm_handler(_Session(exc=exc), monkeypatch)
    out = asyncio.run(create_civilization(1, impl=impl)["communicator"][0].execute({"prompt": "x"}))
    assert out["ok"] is False and "llm.internal" in out["result"]["error"]
    _no_secret(json.dumps(out, default=str))
