"""R-206 — the M10/028 findings, pinned (contract tests, no real provider).

Finding 1: a call that came back with no text and no tool call (a reasoning
model that spent ``max_tokens`` before writing) was ``ok=true`` with
``text=""``. Now it is ``ok=false``, ``reason=empty_output``, ``text=None``;
it was billed, so the result keeps route, tokens, cost and request id, and it
is NOT retried on another route (one call = one bill line).

Finding 3: a model the loaded catalog does not list was refused on the
platform key as ``platform_key_not_allowed``. Now it is ``model_not_found``
(no request sent); when the catalog cannot say (not loaded, not OpenRouter)
the refusal stays ``platform_key_not_allowed`` — never a guess.

Real loopback HTTP servers through the gateway's real network path; FAKE keys.
NOT live evidence.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from agos_v2 import gateway
from agos_v2.gateway import ChatRequest, Route
from agos_v2.gateway_api import Catalog, build_gateway, platform_policy_from_env

from tests import test_r206_gateway as plain
from tests import test_r206_stream as streaming
from tests.test_r206_gateway import server  # noqa: F401  (fixture)

PROMPT = "PROMPT-" + uuid.uuid4().hex
REQ = ChatRequest(messages=[{"role": "user", "content": PROMPT}], max_tokens=16)
TOOLS_REQ = ChatRequest(messages=[{"role": "user", "content": PROMPT}], max_tokens=16,
                        tools=[{"name": "lookup", "parameters": {"type": "object", "properties": {}}}])


def _length_answer(content, cost=0.00002, finish="length", extra=None):
    msg = {"role": "assistant", "content": content}
    msg.update(extra or {})
    return {"id": "gen-empty-1", "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 16, "cost": cost}}


# ================================================================ finding 1 — plain calls

def test_empty_text_at_token_limit_is_a_billed_failure_not_a_success(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.script = [(200, {}, _length_answer(""))]
    r = plain._gw(server).complete(REQ, [Route("openrouter", "vendor/reasoner")], plain.user_key("openrouter"))
    assert r.ok is False and r.reason == "empty_output" and r.text is None
    assert r.finish_reason == "length" and r.http_status == 200
    # what was billed is kept, so the record can be matched to the provider's bill
    assert (r.provider, r.model, r.key_source, r.key_id) == ("openrouter", "vendor/reasoner", "byok", "k1")
    assert (r.tokens_in, r.tokens_out) == (12, 16)
    assert r.cost_usd == Decimal("0.00002") and r.cost_source == "provider"
    assert r.provider_request_id == "gen-empty-1"
    assert [(a["ok"], a["reason"]) for a in r.attempts] == [(False, "empty_output")]
    assert PROMPT not in caplog.text and plain.FAKE not in caplog.text


def test_null_content_with_hidden_reasoning_is_empty_output(server):
    # OpenRouter reasoning models: content null, the thinking in a separate field (never returned)
    server.script = [(200, {}, _length_answer(None, extra={"reasoning": "thinking " * 50}))]
    r = plain._gw(server).complete(REQ, [Route("openrouter", "vendor/reasoner")], plain.user_key("openrouter"))
    assert r.ok is False and r.reason == "empty_output" and r.text is None
    assert "thinking" not in repr(r.to_dict())


def test_blank_text_is_empty_output_even_with_finish_stop(server):
    server.script = [(200, {}, _length_answer(" \n\t ", finish="stop"))]
    r = plain._gw(server).complete(REQ, [Route("openai", "m")], plain.user_key("openai"))
    assert r.ok is False and r.reason == "empty_output" and r.finish_reason == "stop"


def test_empty_output_is_not_retried_or_sent_to_the_next_route(server):
    """One billed call = one bill line: no hidden second (billed) call."""
    server.script = [(200, {}, _length_answer("")), (200, {}, plain._openai_ok("second"))]
    r = plain._gw(server).complete(REQ, [Route("openai", "a"), Route("openai", "b")], plain.user_key("openai"))
    assert r.reason == "empty_output" and r.model == "a" and len(server.seen) == 1
    assert len(r.attempts) == 1 and r.fallback_used is False


def test_empty_output_after_a_fallback_says_fallback_used(server):
    server.script = [(500, {}, {"e": 1})] * 3 + [(200, {}, _length_answer(""))]
    r = plain._gw(server).complete(REQ, [Route("openai", "a"), Route("openai", "b")], plain.user_key("openai"))
    assert r.reason == "empty_output" and r.model == "b" and r.fallback_used is True
    assert [a["reason"] for a in r.attempts] == ["provider_unavailable", "empty_output"]


def test_tool_call_with_no_text_is_still_a_success(server):
    server.script = [(200, {}, _length_answer(None, finish="tool_calls", extra={"tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]}))]
    r = plain._gw(server).complete(TOOLS_REQ, [Route("openai", "m")], plain.user_key("openai"))
    assert r.ok is True and r.reason == "ok" and r.text == "" and r.tool_calls[0]["name"] == "lookup"


def test_real_text_is_untouched(server):
    server.script = [(200, {}, _length_answer("partial answer cut by the limit"))]
    r = plain._gw(server).complete(REQ, [Route("openai", "m")], plain.user_key("openai"))
    assert r.ok is True and r.text == "partial answer cut by the limit" and r.finish_reason == "length"


def test_anthropic_max_tokens_with_no_text_block_is_empty_output(server):
    server.script = [(200, {}, {"id": "msg_e", "type": "message", "stop_reason": "max_tokens",
                                "content": [{"type": "thinking", "thinking": "hidden"}],
                                "usage": {"input_tokens": 8, "output_tokens": 16}})]
    r = plain._gw(server).complete(REQ, [Route("anthropic", "claude-x")], plain.user_key("anthropic"))
    assert r.ok is False and r.reason == "empty_output" and r.finish_reason == "max_tokens"
    assert (r.tokens_in, r.tokens_out, r.provider_request_id) == (8, 16, "msg_e")
    assert r.cost_usd is None and r.cost_source is None            # unknown stays unknown


def test_empty_output_is_a_known_reason_that_fits_the_call_log():
    import re
    for reason in ("empty_output", "model_not_found"):
        assert reason in gateway.REASONS
        assert re.fullmatch(r"[a-z_]{1,40}", reason)              # 006 check on model_calls.reason


# ================================================================ finding 1 — streams

def test_stream_with_no_text_at_token_limit_is_empty_output(streaming_server):
    srv = streaming_server
    srv.script = [(200, {}, [streaming.oa(finish="length"), streaming.oa_usage(pt=12, ct=16, cost=0.00002),
                             streaming.DONE]),
                  (200, {}, streaming.oa_ok("second"))]
    s = streaming._gw(srv).stream(REQ, [Route("openrouter", "a"), Route("openrouter", "b")],
                                  streaming.key("openrouter"))
    events = list(s)
    assert [e["type"] for e in events] == ["route"]                # no delta, no second route
    r = s.result
    assert r.ok is False and r.reason == "empty_output" and r.text is None and r.model == "a"
    assert (r.tokens_in, r.tokens_out) == (12, 16) and r.cost_usd == Decimal("0.00002")
    assert r.provider_request_id == "chatcmpl-1" and len(srv.seen) == 1


def test_stream_with_text_is_untouched(streaming_server):
    srv = streaming_server
    srv.script = [(200, {}, streaming.oa_ok("a", "b"))]
    s = streaming._gw(srv).stream(REQ, [Route("openai", "m")], streaming.key("openai"))
    list(s)
    assert s.result.ok is True and s.result.text == "ab"


# ================================================================ finding 3 — unknown model

def _catalog(models):
    cat = Catalog("http://127.0.0.1:9/never")
    cat.models = lambda: models
    return cat


FREE_POLICY = {"AGOS_PLATFORM_MODELS": "free"}
LISTED = {"vendor/free:free": {"free": True}, "vendor/priced": {"free": False}}


def test_free_policy_names_an_unlisted_model_model_not_found():
    pol = platform_policy_from_env(_catalog(LISTED), FREE_POLICY)
    assert pol(Route("openrouter", "vendor/free:free")) is True
    assert pol(Route("openrouter", "vendor/priced")) is False
    assert pol(Route("openrouter", "agos-r206/does-not-exist:free")) == "model_not_found"


def test_free_policy_without_a_catalog_does_not_guess():
    pol = platform_policy_from_env(_catalog({}), FREE_POLICY)        # fetch failed: no good copy
    assert pol(Route("openrouter", "agos-r206/does-not-exist:free")) is False
    assert _catalog({}).has_model("openrouter", "x") is None
    assert _catalog(LISTED).has_model("openai", "x") is None          # other providers: unknown


def test_policy_off_never_says_model_not_found():
    pol = platform_policy_from_env(_catalog(LISTED), {})
    assert pol(Route("openrouter", "agos-r206/does-not-exist:free")) is False


def test_gateway_reports_model_not_found_without_any_request(server):
    gw = build_gateway({"OPENROUTER_API_KEY": plain.PLATFORM, **FREE_POLICY}, _catalog(LISTED))
    gw._endpoints = {"openrouter": server.base + "/v1"}
    gw._allow_private = True
    r = gw.complete(REQ, [Route("openrouter", "agos-r206/does-not-exist:free")], lambda p: None)
    assert r.ok is False and r.reason == "model_not_found" and r.text is None and server.seen == []
    r = gw.complete(REQ, [Route("openrouter", "vendor/priced")], lambda p: None)
    assert r.reason == "platform_key_not_allowed" and server.seen == []


def test_unlisted_model_then_free_fallback_says_why(server):
    gw = build_gateway({"OPENROUTER_API_KEY": plain.PLATFORM, **FREE_POLICY}, _catalog(LISTED))
    gw._endpoints = {"openrouter": server.base + "/v1"}
    gw._allow_private = True
    server.script = [(200, {}, plain._openai_ok("pong", cost=0))]
    r = gw.complete(REQ, [Route("openrouter", "nope/none"), Route("openrouter", "vendor/free:free")], lambda p: None)
    assert r.ok and r.fallback_used and r.model == "vendor/free:free"
    assert [a["reason"] for a in r.attempts] == ["model_not_found", "ok"] and len(server.seen) == 1


def test_users_own_key_is_not_judged_by_the_platform_catalog(server):
    """The catalog check is part of the PLATFORM policy only: the user's key goes to
    the provider, which answers for its own models (404 -> model_or_endpoint_not_found)."""
    gw = build_gateway({"OPENROUTER_API_KEY": plain.PLATFORM, **FREE_POLICY}, _catalog(LISTED))
    gw._endpoints = {"openrouter": server.base + "/v1"}
    gw._allow_private = True
    server.script = [(404, {}, {"error": "no such model"})]
    r = gw.complete(REQ, [Route("openrouter", "nope/none")], plain.user_key("openrouter"))
    assert r.reason == "model_or_endpoint_not_found" and r.key_source is None and len(server.seen) == 1


def test_only_true_or_a_known_reason_counts_from_a_policy(server):
    for verdict, want in ((1, "platform_key_not_allowed"), ("yes", "platform_key_not_allowed"),
                          ("ok", "platform_key_not_allowed"), (None, "platform_key_not_allowed"),
                          ("model_not_found", "model_not_found")):
        gw = plain._gw(server, platform_keys={"openrouter": plain.PLATFORM},
                       platform_policy=lambda route, v=verdict: v)
        r = gw.complete(REQ, [Route("openrouter", "m")], lambda p: None)
        assert r.reason == want and server.seen == [], verdict


import http.server as _hs  # noqa: E402
import threading as _th  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def streaming_server():
    srv = _hs.ThreadingHTTPServer(("127.0.0.1", 0), streaming._Stream)
    srv.daemon_threads = True
    srv.script, srv.seen = [], []
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
