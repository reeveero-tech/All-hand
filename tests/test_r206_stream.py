"""R-206 (D-160): streaming through the model gateway — contract tests, no real provider.

A REAL loopback HTTP server writes Server-Sent Events in pieces (with pauses
and abrupt closes) and the gateway reads them through its real network path
(``safety.safe_urlopen``), with FAKE keys only. Contract tests, NOT live
evidence.

Pinned behaviour:
* ``Gateway.stream`` yields ``route`` and ``delta`` events and ends with a
  ``Result`` (same shape as ``complete``): full text, tool calls assembled from
  their pieces, tokens, cost with its source, the provider's request id;
* OpenAI-compatible streams (``stream: true``, usage asked for where the
  provider supports it) and Anthropic's event stream are both understood;
* retries and fallbacks happen ONLY before the first text reached the caller;
  after that any failure (drop, timeout, error event, bad chunk, deadline) is
  ``stream_interrupted`` — ``ok=False``, ``text=None``, never another model's
  text glued on, and the attempt keeps the underlying reason;
* a stream that ends without its end marker is interrupted, not complete;
* the caller can cancel (``cancel()``/``close()``): ``client_disconnected``,
  with the route that was streaming and unknown (None) tokens and cost;
* the key, the prompt, the answer and provider error text are never in an
  event, a result or a log line.
"""
from __future__ import annotations

import http.server
import json
import logging
import threading
import time
import uuid

import pytest

from agos_v2 import gateway
from agos_v2.gateway import ChatRequest, Credential, Gateway, InvalidRequest, Route

FAKE = "sk-test-FAKE-" + uuid.uuid4().hex + "-wxyz"
ANSWER = "ANS-" + uuid.uuid4().hex
PROMPT = "PROMPT-" + uuid.uuid4().hex
LEAK = "LEAK-" + uuid.uuid4().hex                       # provider error text that must never surface


def sse(obj, event=None) -> bytes:
    data = obj if isinstance(obj, str) else json.dumps(obj)
    return ((f"event: {event}\n" if event else "") + f"data: {data}\n\n").encode()


class _Stream(http.server.BaseHTTPRequestHandler):
    """Answers POSTs from ``server.script``: (status, headers, parts). Each part is
    bytes (written and flushed), ("sleep", s), or ("drop",) = close the socket now."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self.server.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                 "body": json.loads(body) if body else None})
        status, headers, parts = self.server.script.pop(0) if self.server.script else (500, {}, [])
        self.send_response(status)
        headers = {"Content-Type": "text/event-stream", **headers}
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        for p in parts:
            if isinstance(p, tuple) and p[0] == "sleep":
                time.sleep(p[1])
                continue
            if isinstance(p, tuple) and p[0] == "drop":
                break
            try:
                self.wfile.write(p)
                self.wfile.flush()
            except OSError:
                return
        self.close_connection = True


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stream)
    srv.daemon_threads = True
    srv.script, srv.seen = [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def _gw(server, **kw):
    kw.setdefault("sleep", lambda s: None)
    return Gateway(endpoints={p: server.base + "/v1" for p in gateway.ADAPTERS}, allow_private=True, **kw)


def key(provider="openai"):
    return lambda p: Credential(provider=p, secret=FAKE, source="byok", key_id="k-" + p) if p == provider else None


def keys(*providers):
    return lambda p: Credential(provider=p, secret=FAKE, source="byok", key_id="k-" + p) if p in providers else None


REQ = ChatRequest(messages=[{"role": "user", "content": PROMPT}], max_tokens=16)


def oa(content=None, *, finish=None, tool_calls=None, cid="chatcmpl-1"):
    delta = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return sse({"id": cid, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


def oa_usage(pt=9, ct=4, cost=None, cid="chatcmpl-1"):
    u = {"prompt_tokens": pt, "completion_tokens": ct}
    if cost is not None:
        u["cost"] = cost
    return sse({"id": cid, "choices": [], "usage": u})


DONE = sse("[DONE]")


def oa_ok(*pieces, cost=None):
    return [oa(p) for p in pieces] + [oa(finish="stop"), oa_usage(cost=cost), DONE]


def run(s):
    events = list(s)
    return events, s.result


def deltas(events):
    return "".join(e["text"] for e in events if e["type"] == "delta")


# ================================================================ OpenAI-compatible

def test_openai_stream_answers_in_pieces_with_usage(server):
    server.script = [(200, {}, oa_ok(ANSWER[:10], ANSWER[10:]))]
    events, r = run(_gw(server).stream(REQ, [Route("openai", "gpt-x")], key()))
    assert events[0] == {"type": "route", "provider": "openai", "model": "gpt-x", "key_source": "byok",
                         "fallback_used": False}
    assert [e for e in events if e["type"] == "delta"] == [{"type": "delta", "text": ANSWER[:10]},
                                                          {"type": "delta", "text": ANSWER[10:]}]
    assert r.ok and r.text == ANSWER and r.finish_reason == "stop" and r.reason == "ok"
    assert (r.tokens_in, r.tokens_out, r.provider_request_id, r.key_id) == (9, 4, "chatcmpl-1", "k-openai")
    assert r.attempts[0]["ok"] and r.attempts[0]["reason"] == "ok"
    seen = server.seen[0]
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {"include_usage": True}
    assert seen["headers"]["accept"] == "text/event-stream"
    assert seen["headers"]["authorization"] == f"Bearer {FAKE}"


def test_openrouter_comments_ignored_and_reported_cost_kept(server):
    server.script = [(200, {}, [b": OPENROUTER PROCESSING\n\n"] + oa_ok("a", "b", cost=0.0000123))]
    events, r = run(_gw(server).stream(REQ, [Route("openrouter", "x/y")], key("openrouter")))
    assert r.ok and r.text == "ab" and str(r.cost_usd) == "0.0000123" and r.cost_source == "provider"
    body = server.seen[0]["body"]
    assert body["usage"] == {"include": True} and body["stream_options"] == {"include_usage": True}


def test_catalog_estimate_when_no_cost_is_reported(server):
    from decimal import Decimal
    server.script = [(200, {}, oa_ok("a"))]
    gw = _gw(server, price_for=lambda p, m: (Decimal("0.000001"), Decimal("0.000002")))
    _, r = run(gw.stream(REQ, [Route("groq", "m")], key("groq")))
    assert r.ok and r.cost_source == "catalog_estimate" and r.cost_usd == Decimal("0.000017")


def test_mistral_gets_no_stream_options(server):
    server.script = [(200, {}, oa_ok("a"))]
    _, r = run(_gw(server).stream(REQ, [Route("mistral", "m")], key("mistral")))
    assert r.ok and server.seen[0]["body"]["stream"] is True and "stream_options" not in server.seen[0]["body"]


def test_unknown_usage_stays_unknown(server):
    server.script = [(200, {}, [oa("x"), oa(finish="stop"), DONE])]
    _, r = run(_gw(server).stream(REQ, [Route("openai", "m")], key()))
    assert r.ok and (r.tokens_in, r.tokens_out, r.cost_usd, r.cost_source) == (None, None, None, None)


def test_finish_then_eof_without_done_is_complete(server):
    server.script = [(200, {}, [oa("x"), oa(finish="stop"), oa_usage()])]
    _, r = run(_gw(server).stream(REQ, [Route("google", "m")], key("google")))
    assert r.ok and r.text == "x" and r.tokens_out == 4


def test_openai_tool_calls_assembled_from_pieces(server):
    tools = [{"name": "get_weather", "parameters": {"type": "object"}}]
    req = ChatRequest(messages=REQ.messages, tools=tools)
    server.script = [(200, {}, [
        oa(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                        "function": {"name": "get_weather", "arguments": ""}}]),
        oa(tool_calls=[{"index": 0, "function": {"arguments": '{"city":'}}]),
        oa(tool_calls=[{"index": 0, "function": {"arguments": '"Jeddah"}'}}]),
        oa(tool_calls=[{"index": 1, "id": "call_2", "function": {"name": "get_weather", "arguments": "{bad"}}]),
        oa(finish="tool_calls"), oa_usage(), DONE])]
    events, r = run(_gw(server).stream(req, [Route("openai", "m")], key()))
    assert r.ok and r.text == "" and r.finish_reason == "tool_calls"
    assert r.tool_calls == [
        {"id": "call_1", "name": "get_weather", "arguments": '{"city":"Jeddah"}', "arguments_valid": True},
        {"id": "call_2", "name": "get_weather", "arguments": "{bad", "arguments_valid": False}]
    assert not [e for e in events if e["type"] == "delta"]
    assert server.seen[0]["body"]["tools"][0]["function"]["name"] == "get_weather"


@pytest.mark.parametrize("parts", [
    [b"data: {not json\n\n"],
    [sse([1, 2])],
    [sse({"choices": "x"})],
    [sse({"choices": [{"delta": {"content": 7}}]})],
    [sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]},
                       "finish_reason": "tool_calls"}]}), DONE],          # a tool call with no id/name
])
def test_malformed_stream_before_text_is_malformed_and_falls_back(server, parts):
    server.script = [(200, {}, parts), (200, {}, oa_ok("fine"))]
    _, r = run(_gw(server).stream(REQ, [Route("openai", "a"), Route("groq", "b")], keys("openai", "groq")))
    assert r.ok and r.text == "fine" and r.fallback_used and r.provider == "groq"
    assert r.attempts[0]["reason"] == "malformed_response" and r.attempts[0]["retries"] == 0


def test_not_an_event_stream_is_malformed(server):
    server.script = [(200, {"Content-Type": "application/json"}, [json.dumps({"choices": []}).encode()])]
    _, r = run(_gw(server).stream(REQ, [Route("openai", "m")], key()))
    assert not r.ok and r.reason == "malformed_response" and r.text is None


# ================================================================ Anthropic

def an(obj):
    return sse(obj, event=obj["type"])


def test_anthropic_stream_text_tool_use_and_usage(server):
    tools = [{"name": "clock", "parameters": {"type": "object"}}]
    req = ChatRequest(messages=REQ.messages, tools=tools)
    server.script = [(200, {}, [
        an({"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        an({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        b"event: ping\ndata: {\"type\": \"ping\"}\n\n",
        an({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let "}}),
        an({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "me."}}),
        an({"type": "content_block_stop", "index": 0}),
        an({"type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "clock", "input": {}}}),
        an({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"tz": '}}),
        an({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"UTC"}'}}),
        an({"type": "content_block_stop", "index": 1}),
        an({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}}),
        an({"type": "message_stop"})])]
    events, r = run(_gw(server).stream(req, [Route("anthropic", "claude-x")], key("anthropic")))
    assert deltas(events) == "Let me."
    assert r.ok and r.text == "Let me." and r.finish_reason == "tool_use"
    assert (r.tokens_in, r.tokens_out, r.provider_request_id) == (12, 30, "msg_1")
    assert r.tool_calls == [{"id": "tu_1", "name": "clock", "arguments": '{"tz":"UTC"}', "arguments_valid": True}]
    seen = server.seen[0]
    assert seen["path"] == "/v1/messages" and seen["body"]["stream"] is True and "stream_options" not in seen["body"]
    assert seen["headers"]["x-api-key"] == FAKE and "authorization" not in seen["headers"]


def test_anthropic_tool_use_without_arguments_is_an_empty_object(server):
    server.script = [(200, {}, [
        an({"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}}),
        an({"type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "t", "name": "clock", "input": {}}}),
        an({"type": "content_block_stop", "index": 0}),
        an({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}),
        an({"type": "message_stop"})])]
    req = ChatRequest(messages=REQ.messages, tools=[{"name": "clock"}])
    _, r = run(_gw(server).stream(req, [Route("anthropic", "c")], key("anthropic")))
    assert r.ok and r.tool_calls == [{"id": "t", "name": "clock", "arguments": "{}", "arguments_valid": True}]


def test_anthropic_without_message_stop_is_interrupted(server):
    server.script = [(200, {}, [
        an({"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}}),
        an({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "partial"}})])]
    events, r = run(_gw(server).stream(REQ, [Route("anthropic", "c")], key("anthropic")))
    assert deltas(events) == "partial"
    assert not r.ok and r.reason == "stream_interrupted" and r.text is None
    assert r.provider == "anthropic" and r.attempts[0]["reason"] == "stream_interrupted"


def test_anthropic_error_event_mid_stream_never_leaks(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.script = [(200, {}, [
        an({"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}}),
        an({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}}),
        an({"type": "error", "error": {"type": "overloaded_error", "message": LEAK}})])]
    events, r = run(_gw(server).stream(REQ, [Route("anthropic", "c")], key("anthropic")))
    assert not r.ok and r.reason == "stream_interrupted" and r.attempts[0]["reason"] == "provider_unavailable"
    assert LEAK not in json.dumps(r.to_dict()) + json.dumps(events) + caplog.text


# ================================================================ retries and fallback

def test_http_error_before_the_stream_is_retried(server):
    server.script = [(503, {"Content-Type": "application/json"}, [b"{}"]), (200, {}, oa_ok("ok"))]
    _, r = run(_gw(server).stream(REQ, [Route("openai", "m")], key()))
    assert r.ok and r.text == "ok" and r.attempts[0]["retries"] == 1


def test_drop_before_any_text_is_retried_then_falls_back(server):
    server.script = [(200, {}, [("drop",)])] * 3 + [(200, {}, oa_ok("second"))]
    events, r = run(_gw(server).stream(REQ, [Route("openai", "a"), Route("groq", "b")], keys("openai", "groq")))
    assert r.ok and r.text == "second" and r.provider == "groq" and r.fallback_used
    assert r.attempts[0]["reason"] == "stream_interrupted" and r.attempts[0]["retries"] == gateway.MAX_RETRIES
    assert deltas(events) == "second"
    assert [e["provider"] for e in events if e["type"] == "route"][-1] == "groq"


def test_error_event_before_text_is_retried(server):
    server.script = [(200, {}, [sse({"error": {"message": LEAK, "code": 502}})]), (200, {}, oa_ok("ok"))]
    _, r = run(_gw(server).stream(REQ, [Route("openrouter", "m")], key("openrouter")))
    assert r.ok and r.attempts[0]["retries"] == 1 and LEAK not in json.dumps(r.to_dict())


@pytest.mark.parametrize("tail,reason", [
    ([("drop",)], "stream_interrupted"),                                  # connection lost
    ([sse({"error": {"message": LEAK}})], "provider_unavailable"),        # provider error event
    ([b"data: {broken\n\n"], "malformed_response"),                       # bad chunk
    ([oa("more")], "stream_interrupted"),                                 # EOF without finish or [DONE]
])
def test_failure_after_text_is_interrupted_never_retried_or_replaced(server, tail, reason):
    server.script = [(200, {}, [oa(ANSWER)] + tail), (200, {}, oa_ok("OTHER"))]
    events, r = run(_gw(server).stream(REQ, [Route("openai", "a"), Route("groq", "b")], keys("openai", "groq")))
    assert not r.ok and r.reason == "stream_interrupted" and r.text is None and not r.fallback_used
    assert r.provider == "openai" and r.model == "a" and r.key_source == "byok" and r.key_id == "k-openai"
    assert r.attempts[-1]["reason"] == reason and r.attempts[-1]["retries"] == 0 and len(r.attempts) == 1
    assert len(server.seen) == 1 and "OTHER" not in deltas(events)
    assert (r.tokens_in, r.tokens_out, r.cost_usd) == (None, None, None)     # unknown, never 0
    assert LEAK not in json.dumps(r.to_dict())


def test_read_timeout_after_text_is_interrupted(server):
    server.script = [(200, {}, [oa("x"), ("sleep", 1.5), oa("late"), oa(finish="stop"), DONE])]
    _, r = run(_gw(server, timeout_s=0.4).stream(REQ, [Route("openai", "m")], key()))
    assert not r.ok and r.reason == "stream_interrupted" and r.attempts[0]["reason"] == "timeout"


def test_deadline_mid_stream(server):
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]
    server.script = [(200, {}, [oa("x")] * 50 + [oa(finish="stop"), DONE])]
    _, r = run(_gw(server, clock=clock, stream_deadline_s=10).stream(REQ, [Route("openai", "m")], key()))
    assert not r.ok and r.reason == "stream_interrupted" and r.attempts[0]["reason"] == "deadline_exceeded"


def test_size_cap(server, monkeypatch):
    monkeypatch.setattr(gateway, "MAX_BODY", 2000)
    server.script = [(200, {}, [oa("y" * 100)] * 40 + [oa(finish="stop"), DONE])]
    _, r = run(_gw(server).stream(REQ, [Route("openai", "m")], key()))
    assert not r.ok and r.reason == "stream_interrupted" and r.attempts[0]["reason"] == "malformed_response"


# ================================================================ cancel

def test_cancel_after_first_text_stops_and_reports_client_disconnected(server, caplog):
    caplog.set_level(logging.INFO)
    server.script = [(200, {}, [oa("first")] + [("sleep", 0.05), oa("more")] * 40 + [oa(finish="stop"), DONE])]
    s = _gw(server).stream(REQ, [Route("openai", "m")], key())
    got = []
    for e in s:
        got.append(e)
        if e["type"] == "delta":
            s.cancel()
    r = s.result
    assert not r.ok and r.reason == "client_disconnected" and r.text is None
    assert r.provider == "openai" and r.key_id == "k-openai" and r.attempts[0]["reason"] == "client_disconnected"
    assert deltas(got).count("more") <= 1
    assert "reason=client_disconnected" in caplog.text


def test_close_without_reading_on(server):
    server.script = [(200, {}, [oa("first"), ("sleep", 0.3), oa(finish="stop"), DONE])]
    s = _gw(server).stream(REQ, [Route("openai", "m")], key())
    assert next(s)["type"] == "route" and next(s)["type"] == "delta"
    s.close()
    assert s.result.reason == "client_disconnected" and not s.result.ok
    s.close()                                                     # idempotent
    assert list(s) == []


def test_close_before_start_records_no_route(server):
    s = _gw(server).stream(REQ, [Route("openai", "m")], key())
    s.close()
    assert s.result.reason == "client_disconnected" and s.result.attempts == [] and not server.seen


# ================================================================ refusals before any request

def test_no_key_sends_nothing(server):
    events, r = run(_gw(server).stream(REQ, [Route("openai", "m")], lambda p: None))
    assert events == [] and not r.ok and r.reason == "no_key" and not server.seen


def test_capability_refusal_applies_to_streams(server):
    req = ChatRequest(messages=REQ.messages, tools=[{"name": "t"}])
    gw = _gw(server, capability_for=lambda p, m: {"tools": False, "structured_output": True})
    events, r = run(gw.stream(req, [Route("openrouter", "m")], key("openrouter")))
    assert events == [] and r.reason == "tools_not_supported" and not server.seen


def test_no_routes_is_invalid(server):
    with pytest.raises(InvalidRequest):
        _gw(server).stream(REQ, [], key())


# ================================================================ no leaks

def test_nothing_secret_in_events_results_or_logs(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.script = [(401, {"Content-Type": "application/json"}, [json.dumps({"error": FAKE + LEAK}).encode()]),
                     (200, {}, oa_ok(ANSWER))]
    s = _gw(server).stream(REQ, [Route("openai", "a"), Route("groq", "b")], keys("openai", "groq"))
    events, r = run(s)
    assert r.ok and r.attempts[0]["reason"] == "invalid_key"
    blob = json.dumps(events) + json.dumps(r.to_dict()) + repr(s) + caplog.text
    assert FAKE not in blob and LEAK not in blob
    assert ANSWER not in caplog.text and PROMPT not in caplog.text


# ================================================================ the HTTP body's slot (gateway_api)

async def test_a_disconnected_stream_holds_its_slot_until_its_call_is_recorded():
    """The client leaves between two events (the server closes the body at its
    ``yield``): the provider stream is stopped, the call is recorded in the
    background, and the slot stays taken until that record is written."""
    import asyncio
    import gc

    from agos_v2 import gateway_api

    class Stream1:
        result = None

        def __init__(self):
            self.cancelled = self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return {"type": "delta", "text": "a"}

        def cancel(self):
            self.cancelled = True

        def close(self):
            self.closed = True
            self.result = gateway.Result(ok=False, reason="client_disconnected")

    recorded, gate = [], asyncio.Event()

    async def finish(result):
        await gate.wait()
        recorded.append(result.reason)
        return "call-1"

    sem = gateway_api._STREAM_SLOTS
    before = sem._value
    assert sem.acquire(blocking=False)
    stream = Stream1()
    body = gateway_api._stream_body(stream, finish)
    assert (await body.__anext__()).startswith(b"event: delta")
    await body.aclose()                            # the client left
    del body
    gc.collect()
    await asyncio.sleep(0.05)
    assert stream.cancelled and stream.closed and not recorded
    assert sem._value == before - 1                # still held: the call is not recorded yet
    gate.set()
    for _ in range(100):
        if recorded:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.02)
    assert recorded == ["client_disconnected"] and sem._value == before
