"""R-206 (D-157): the unified model gateway — contract tests, no real provider.

Every test talks to a REAL loopback HTTP server (scripted answers) through the
gateway's real network path (``safety.safe_urlopen``), with FAKE keys only.
These are contract tests; they are NOT live evidence (R-206 acceptance needs
live calls — docs/audit/M10/logs/raw/).

Pinned behaviour:
* one request shape for every provider: OpenAI-compatible wire format for
  openai/openrouter/groq/mistral/google/ollama, the native Messages API for
  anthropic; the key goes only in that provider's auth header;
* the credential is chosen user key first, then the platform key only when the
  policy allows it; no key = an honest ``no_key`` failure without any request;
* bounded retries (429/5xx/timeout only, Retry-After honoured but capped),
  never on 4xx auth/validation errors;
* fallbacks only across routes the caller listed, and the result always says
  which provider/model really answered and every attempt that failed;
* a failure is a failure: ``ok=False``, ``text=None`` — never invented text;
* tokens and cost per request: provider-reported cost first, else a catalog
  estimate marked as such, else ``None`` (unknown, never 0);
* the key never appears in a result, a repr, an error or a log line, and the
  provider's error body is never read into the result.
"""
from __future__ import annotations

import http.server
import json
import logging
import threading
import time
import uuid
from decimal import Decimal

import pytest

from agos_v2 import gateway
from agos_v2.gateway import (ChatRequest, Credential, Gateway, InvalidRequest, Route,
                             parse_openrouter_catalog)

FAKE = "sk-test-FAKE-" + uuid.uuid4().hex + "-wxyz"   # never a real key
PLATFORM = "sk-or-PLATFORM-FAKE-" + uuid.uuid4().hex


class _Script(http.server.BaseHTTPRequestHandler):
    """Answers POSTs from ``server.script`` (a list of (status, headers, body|callable|'sleep:N'))."""

    def log_message(self, *a):  # quiet
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self.server.seen.append({"path": self.path, "method": self.command,
                                 "headers": {k.lower(): v for k, v in self.headers.items()},
                                 "body": json.loads(body) if body else None})
        step = self.server.script.pop(0) if self.server.script else (500, {}, {"error": "no script"})
        status, headers, payload = step
        if isinstance(payload, str) and payload.startswith("sleep:"):
            time.sleep(float(payload.split(":")[1]))
            payload = {"late": True}
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_POST = _answer
    do_GET = _answer


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Script)
    srv.script, srv.seen = [], []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def _gw(server, **kw):
    eps = {p: server.base + ("/v1" if p != "anthropic" else "/v1") for p in gateway.ADAPTERS}
    kw.setdefault("sleep", lambda s: None)
    return Gateway(endpoints=eps, allow_private=True, **kw)


def _openai_ok(text="hello", cost=None, pt=11, ct=3):
    usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "gen-123", "model": "m", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": text}}], "usage": usage}


REQ = ChatRequest(messages=[{"role": "system", "content": "be brief"},
                            {"role": "user", "content": "hi"}], max_tokens=16)


def user_key(provider="openai"):
    return lambda p: Credential(provider=p, secret=FAKE, source="byok", key_id="k1") if p == provider else None


# ================================================================ shapes

def test_openai_compatible_request_shape_auth_usage_and_provider_cost(server):
    server.script = [(200, {}, _openai_ok("hello", cost=0.000123))]
    r = _gw(server).complete(REQ, [Route("openrouter", "vendor/model-a")], user_key("openrouter"))
    assert r.ok and r.text == "hello"
    assert (r.provider, r.model, r.key_source) == ("openrouter", "vendor/model-a", "byok")
    assert r.tokens_in == 11 and r.tokens_out == 3
    assert r.cost_usd == Decimal("0.000123") and r.cost_source == "provider"
    assert r.provider_request_id == "gen-123" and r.fallback_used is False
    seen = server.seen[0]
    assert seen["path"] == "/v1/chat/completions" and seen["headers"]["authorization"] == f"Bearer {FAKE}"
    assert seen["body"]["model"] == "vendor/model-a" and seen["body"]["max_tokens"] == 16
    assert seen["body"]["messages"] == REQ.messages
    assert seen["body"]["usage"] == {"include": True}          # OpenRouter reports the real cost


def test_anthropic_native_shape_system_split_and_usage(server):
    server.script = [(200, {}, {"id": "msg_1", "type": "message", "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "hel"}, {"type": "text", "text": "lo"}],
                                "usage": {"input_tokens": 9, "output_tokens": 2}})]
    r = _gw(server).complete(REQ, [Route("anthropic", "claude-x")], user_key("anthropic"))
    assert r.ok and r.text == "hello" and (r.tokens_in, r.tokens_out) == (9, 2)
    seen = server.seen[0]
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == FAKE and "authorization" not in seen["headers"]
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "be brief"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert r.cost_usd is None and r.cost_source is None           # unknown, never 0


def test_json_mode_is_forwarded(server):
    server.script = [(200, {}, _openai_ok('{"a": 1}'))]
    req = ChatRequest(messages=[{"role": "user", "content": "json"}], max_tokens=8, json_mode=True)
    r = _gw(server).complete(req, [Route("openai", "gpt-x")], user_key())
    assert r.ok and server.seen[0]["body"]["response_format"] == {"type": "json_object"}


# ================================================================ key selection

def test_user_key_first_then_platform_only_when_allowed(server):
    def creds(p):
        return None  # the user has no key
    server.script = [(200, {}, _openai_ok("p"))]
    no = _gw(server).complete(REQ, [Route("openrouter", "m")], creds)
    assert not no.ok and no.reason == "no_key" and server.seen == []    # no request at all
    gw = _gw(server, platform_keys={"openrouter": PLATFORM}, platform_policy=lambda route: True)
    yes = gw.complete(REQ, [Route("openrouter", "m")], creds)
    assert yes.ok and yes.key_source == "platform"
    assert server.seen[0]["headers"]["authorization"] == f"Bearer {PLATFORM}"
    # user key wins over the platform key
    server.script = [(200, {}, _openai_ok("u"))]
    mine = gw.complete(REQ, [Route("openrouter", "m")], user_key("openrouter"))
    assert mine.key_source == "byok" and server.seen[-1]["headers"]["authorization"] == f"Bearer {FAKE}"


def test_platform_policy_refusal_is_reported(server):
    gw = _gw(server, platform_keys={"openrouter": PLATFORM}, platform_policy=lambda route: False)
    r = gw.complete(REQ, [Route("openrouter", "paid/model")], lambda p: None)
    assert not r.ok and r.reason == "platform_key_not_allowed" and server.seen == []


# ================================================================ retries

def test_429_is_retried_with_capped_retry_after(server):
    slept = []
    server.script = [(429, {"Retry-After": "120"}, {"error": "slow down"}), (200, {}, _openai_ok("ok"))]
    r = _gw(server, sleep=slept.append).complete(REQ, [Route("openai", "m")], user_key())
    assert r.ok and r.attempts[0]["retries"] == 1 and len(server.seen) == 2
    assert slept and max(slept) <= gateway.RETRY_AFTER_CAP_S


def test_5xx_retries_are_bounded_then_fallback_answers_and_is_reported(server):
    server.script = [(503, {}, {})] * (gateway.MAX_RETRIES + 1) + [(200, {}, _openai_ok("from b"))]

    def creds(p):
        return Credential(provider=p, secret=FAKE, source="byok", key_id="k-" + p)
    r = _gw(server).complete(REQ, [Route("openai", "a"), Route("groq", "b")], creds)
    assert r.ok and r.text == "from b" and (r.provider, r.model) == ("groq", "b")
    assert r.fallback_used is True
    first = r.attempts[0]
    assert first["provider"] == "openai" and not first["ok"] and first["reason"] == "provider_unavailable"
    assert first["retries"] == gateway.MAX_RETRIES and first["http_status"] == 503
    assert len(server.seen) == gateway.MAX_RETRIES + 2


def test_401_is_not_retried_and_failure_is_honest(server):
    server.script = [(401, {}, {"error": {"message": f"bad key {FAKE}"}})]
    r = _gw(server).complete(REQ, [Route("openai", "m")], user_key())
    assert not r.ok and r.text is None and r.reason == "invalid_key" and r.http_status == 401
    assert len(server.seen) == 1
    assert FAKE not in json.dumps(r.to_dict()) and FAKE[:-4] not in repr(r)   # body never read


def test_timeout_is_bounded(server):
    server.script = [(200, {}, "sleep:1.5")] * (gateway.MAX_RETRIES + 1)
    r = _gw(server, timeout_s=0.3).complete(REQ, [Route("openai", "m")], user_key())
    assert not r.ok and r.reason == "timeout" and r.attempts[0]["retries"] == gateway.MAX_RETRIES


def test_deadline_stops_retries(server):
    server.script = [(503, {}, {})] * 10
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    gw = _gw(server, deadline_s=30.0, clock=lambda: next(clock))
    r = gw.complete(REQ, [Route("openai", "m"), Route("groq", "b")], user_key())
    assert not r.ok and r.reason == "deadline_exceeded" and len(server.seen) == 1


# ================================================================ honesty

def test_all_routes_fail_no_invented_text(server):
    server.script = [(400, {}, {}), (404, {}, {})]

    def creds(p):
        return Credential(provider=p, secret=FAKE, source="byok", key_id="k")
    r = _gw(server).complete(REQ, [Route("openai", "a"), Route("mistral", "b")], creds)
    assert not r.ok and r.text is None and r.provider is None
    assert [a["reason"] for a in r.attempts] == ["bad_request", "model_or_endpoint_not_found"]
    assert r.reason == "model_or_endpoint_not_found"


def test_malformed_and_empty_choices_are_failures(server):
    server.script = [(200, {}, b"not json"), (200, {}, {"choices": []})]

    def creds(p):
        return Credential(provider=p, secret=FAKE, source="byok", key_id="k")
    r = _gw(server).complete(REQ, [Route("openai", "a"), Route("groq", "b")], creds)
    assert not r.ok and [a["reason"] for a in r.attempts] == ["malformed_response", "malformed_response"]


def test_redirect_is_refused_and_neither_key_nor_prompt_is_forwarded(server):
    server.script = [(302, {"Location": server.base + "/elsewhere"}, {}), (200, {}, _openai_ok("evil"))]
    r = _gw(server).complete(REQ, [Route("openai", "m")], user_key())
    assert not r.ok and r.text is None and r.reason == "unexpected_redirect"
    moved = [s for s in server.seen if s["path"] == "/elsewhere"]
    assert moved and "authorization" not in moved[0]["headers"] and moved[0]["body"] is None


def test_private_address_blocked_without_operator_opt_in():
    gw = Gateway(endpoints={"ollama": "http://127.0.0.1:9/v1"}, allow_private=False, sleep=lambda s: None)
    cred = lambda p: Credential(provider=p, secret=None, source="byok", key_id="k",  # noqa: E731
                                base_url="http://127.0.0.1:9")
    r = gw.complete(REQ, [Route("ollama", "llama3")], cred)
    assert not r.ok and r.reason == "blocked_address"


def test_ollama_uses_the_users_base_url(server):
    server.script = [(200, {}, _openai_ok("local"))]
    gw = Gateway(allow_private=True, sleep=lambda s: None)
    cred = lambda p: Credential(provider=p, secret=None, source="byok", key_id="k",  # noqa: E731
                                base_url=server.base)
    r = gw.complete(REQ, [Route("ollama", "llama3")], cred)
    assert r.ok and r.text == "local" and server.seen[0]["path"] == "/v1/chat/completions"
    assert "authorization" not in server.seen[0]["headers"]


# ================================================================ cost

def test_catalog_estimate_when_provider_reports_no_cost(server):
    server.script = [(200, {}, _openai_ok("x", pt=1000, ct=500))]
    prices = {("openai", "m"): (Decimal("0.000002"), Decimal("0.000008"))}
    r = _gw(server, price_for=lambda p, m: prices.get((p, m))).complete(REQ, [Route("openai", "m")], user_key())
    assert r.cost_usd == Decimal("0.006") and r.cost_source == "catalog_estimate"


def test_parse_openrouter_catalog():
    doc = {"data": [
        {"id": "a/vision-tools", "context_length": 128000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "architecture": {"input_modalities": ["text", "image"]},
         "supported_parameters": ["tools", "response_format", "max_tokens"]},
        {"id": "b/free:free", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"]}, "supported_parameters": []},
        {"id": "c/bad", "pricing": {"prompt": "-1", "completion": "x"}},
    ]}
    cat = parse_openrouter_catalog(doc, fetched_at="2026-11-15T00:00:00Z")
    a, b, c = (cat[k] for k in ("a/vision-tools", "b/free:free", "c/bad"))
    assert a["vision"] and a["tools"] and a["structured_output"] and a["context_length"] == 128000
    assert a["price_in_per_token"] == Decimal("0.000001") and a["source"].startswith("openrouter")
    assert b["free"] is True and not b["vision"] and not b["tools"]
    assert c["price_in_per_token"] is None and c["free"] is False and c["context_length"] is None


# ================================================================ validation & secrecy

@pytest.mark.parametrize("bad", [
    dict(messages=[]),
    dict(messages=[{"role": "tool", "content": "x"}]),
    dict(messages=[{"role": "user", "content": 5}]),
    dict(messages=[{"role": "user", "content": "x"}], max_tokens=0),
    dict(messages=[{"role": "user", "content": "x"}], max_tokens=10**6),
    dict(messages=[{"role": "user", "content": "x"}], temperature=3),
])
def test_invalid_requests_are_refused(bad):
    with pytest.raises(InvalidRequest):
        ChatRequest(**bad)


def test_unknown_route_provider_refused():
    with pytest.raises(InvalidRequest):
        Route("nope", "m")


def test_secret_never_in_repr_dict_or_logs(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.script = [(500, {}, {"echo": FAKE})] * (gateway.MAX_RETRIES + 1)
    c = Credential(provider="openai", secret=FAKE, source="byok", key_id="k")
    assert FAKE not in repr(c) and FAKE[:-4] not in str(c)
    r = _gw(server).complete(REQ, [Route("openai", "m")], lambda p: c)
    blob = repr(r) + json.dumps(r.to_dict()) + caplog.text
    assert FAKE not in blob and FAKE[:-4] not in blob
    assert "be brief" not in caplog.text and "hi" not in [rec.getMessage() for rec in caplog.records]

