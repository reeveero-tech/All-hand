"""R-206 (D-159): tool calls through the model gateway — contract tests, no real provider.

Same harness as tests/test_r206_gateway.py: a REAL loopback HTTP server with
scripted answers, the gateway's real network path, FAKE keys only. These are
contract tests, NOT live evidence.

Pinned behaviour:
* one provider-neutral tool format in (``tools``, ``tool_choice``, assistant
  ``tool_calls`` and ``tool`` result messages) mapped to each wire format —
  OpenAI-compatible ``function`` tools and Anthropic ``tool_use`` /
  ``tool_result`` blocks — and one format out (``Result.tool_calls``);
* the gateway never runs a tool; it returns what the model asked for, and
  arguments that are not a JSON object are reported (``arguments_valid``),
  never repaired or hidden;
* a malformed tool call from a provider is a failure, not a partial answer;
* a request that cannot be valid (bad name, unknown tool, a tool result with
  no matching call, ...) is refused before any network call;
* a model the catalog says cannot use tools (or structured output) is not
  called — the attempt says why and a listed fallback may answer;
* tool arguments and results are never logged.
"""
from __future__ import annotations

import http.server
import json
import logging
import threading

import pytest

from agos_v2.gateway import ChatRequest, Credential, InvalidRequest, Route
from tests.test_r206_gateway import FAKE, _Script, _gw, _openai_ok, user_key


@pytest.fixture
def server():
    """The same scripted loopback provider as tests/test_r206_gateway.py."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Script)
    srv.script, srv.seen = [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()

ARG_MARK = "ARG-MARK-6f1c"
RES_MARK = "RESULT-MARK-93ab"

WEATHER = {"name": "get_weather", "description": "Weather for a city",
           "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                          "required": ["city"]}}
CLOCK = {"name": "clock", "parameters": {"type": "object", "properties": {}}}


def _req(**kw):
    kw.setdefault("messages", [{"role": "user", "content": "weather in Jeddah?"}])
    kw.setdefault("tools", [WEATHER, CLOCK])
    kw.setdefault("max_tokens", 64)
    return ChatRequest(**kw)


def _openai_tool_answer(calls, content=None):
    return {"id": "gen-tool-1", "choices": [{"index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": content, "tool_calls": calls}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 9}}


def _oa_call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


HISTORY = [
    {"role": "user", "content": "weather in Jeddah and the time?"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "name": "get_weather", "arguments": json.dumps({"city": "Jeddah"})},
        {"id": "call_2", "name": "clock", "arguments": "{}"}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "34C sunny " + RES_MARK},
    {"role": "tool", "tool_call_id": "call_2", "content": "14:05"},
]


# ================================================================ OpenAI-compatible

def test_openai_tools_shape_and_tool_calls_out(server):
    server.script = [(200, {}, _openai_tool_answer([_oa_call("call_9", "get_weather", '{"city":"Jeddah"}')]))]
    r = _gw(server).complete(_req(tool_choice="required"), [Route("openai", "gpt-x")], user_key())
    assert r.ok is True and r.reason == "ok" and r.text == "" and r.finish_reason == "tool_calls"
    assert r.tool_calls == [{"id": "call_9", "name": "get_weather", "arguments": '{"city":"Jeddah"}',
                             "arguments_valid": True}]
    body = server.seen[0]["body"]
    assert body["tools"] == [
        {"type": "function", "function": {"name": "get_weather", "description": "Weather for a city",
                                          "parameters": WEATHER["parameters"]}},
        {"type": "function", "function": {"name": "clock", "parameters": CLOCK["parameters"]}}]
    assert body["tool_choice"] == "required"
    assert r.to_dict()["tool_calls"] == r.tool_calls


@pytest.mark.parametrize("choice,wire", [("auto", "auto"), ("none", "none"),
                                         ({"name": "clock"}, {"type": "function", "function": {"name": "clock"}})])
def test_openai_tool_choice_mapping(server, choice, wire):
    server.script = [(200, {}, _openai_ok("x"))]
    assert _gw(server).complete(_req(tool_choice=choice), [Route("groq", "m")], user_key("groq")).ok
    assert server.seen[0]["body"]["tool_choice"] == wire


def test_no_tools_means_no_tool_fields_on_the_wire(server):
    server.script = [(200, {}, _openai_ok("x"))]
    r = _gw(server).complete(ChatRequest(messages=[{"role": "user", "content": "hi"}]),
                             [Route("openai", "m")], user_key())
    assert r.ok and r.tool_calls == []
    assert "tools" not in server.seen[0]["body"] and "tool_choice" not in server.seen[0]["body"]


def test_openai_history_with_tool_calls_and_results(server):
    server.script = [(200, {}, _openai_ok("It is 34C at 14:05."))]
    r = _gw(server).complete(_req(messages=HISTORY), [Route("openrouter", "a/b")], user_key("openrouter"))
    assert r.ok and r.text == "It is 34C at 14:05." and r.tool_calls == []
    assert server.seen[0]["body"]["messages"] == [
        HISTORY[0],
        {"role": "assistant", "content": None, "tool_calls": [
            _oa_call("call_1", "get_weather", json.dumps({"city": "Jeddah"})), _oa_call("call_2", "clock", "{}")]},
        {"role": "tool", "tool_call_id": "call_1", "content": "34C sunny " + RES_MARK},
        {"role": "tool", "tool_call_id": "call_2", "content": "14:05"},
    ]


def test_invalid_arguments_from_the_model_are_reported_not_repaired(server):
    server.script = [(200, {}, _openai_tool_answer([_oa_call("c1", "get_weather", '{"city": "Jed'),
                                                    _oa_call("c2", "clock", "[1]")], content="calling"))]
    r = _gw(server).complete(_req(), [Route("openai", "m")], user_key())
    assert r.ok and r.text == "calling"
    assert [(c["arguments"], c["arguments_valid"]) for c in r.tool_calls] == [('{"city": "Jed', False),
                                                                             ("[1]", False)]


@pytest.mark.parametrize("calls", [
    [{"type": "function", "function": {"name": "clock", "arguments": "{}"}}],          # no id
    [{"id": "c1", "type": "function", "function": "clock"}],                            # function not an object
    [{"id": "c1", "type": "function", "function": {"arguments": "{}"}}],               # no name
    [{"id": "c1", "type": "function", "function": {"name": "clock", "arguments": 7}}],  # arguments not text/object
    "not-a-list",
])
def test_malformed_tool_calls_are_a_failure(server, calls):
    server.script = [(200, {}, _openai_tool_answer(calls))]
    r = _gw(server).complete(_req(), [Route("openai", "m")], user_key())
    assert r.ok is False and r.reason == "malformed_response" and r.text is None and r.tool_calls == []


def test_object_arguments_are_kept_as_json_text(server):
    # some OpenAI-compatible servers send arguments as an object instead of a string
    server.script = [(200, {}, _openai_tool_answer([{"id": "c1", "type": "function",
                                                     "function": {"name": "get_weather",
                                                                  "arguments": {"city": "Jeddah"}}}]))]
    r = _gw(server).complete(_req(), [Route("mistral", "m")], user_key("mistral"))
    assert r.ok and json.loads(r.tool_calls[0]["arguments"]) == {"city": "Jeddah"}
    assert r.tool_calls[0]["arguments_valid"] is True


# ================================================================ Anthropic

def _anthropic_answer(blocks, stop="tool_use"):
    return {"id": "msg_01", "type": "message", "content": blocks, "stop_reason": stop,
            "usage": {"input_tokens": 30, "output_tokens": 12}}


def test_anthropic_tools_shape_and_tool_use_out(server):
    server.script = [(200, {}, _anthropic_answer([
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Jeddah"}}]))]
    r = _gw(server).complete(_req(tool_choice="required"), [Route("anthropic", "claude-x")], user_key("anthropic"))
    assert r.ok and r.text == "Let me check." and r.finish_reason == "tool_use"
    assert r.tool_calls == [{"id": "toolu_1", "name": "get_weather", "arguments": '{"city":"Jeddah"}',
                             "arguments_valid": True}]
    body = server.seen[0]["body"]
    assert body["tools"] == [{"name": "get_weather", "description": "Weather for a city",
                              "input_schema": WEATHER["parameters"]},
                             {"name": "clock", "input_schema": CLOCK["parameters"]}]
    assert body["tool_choice"] == {"type": "any"}


@pytest.mark.parametrize("choice,wire", [("auto", {"type": "auto"}), ("none", {"type": "none"}),
                                         ({"name": "clock"}, {"type": "tool", "name": "clock"})])
def test_anthropic_tool_choice_mapping(server, choice, wire):
    server.script = [(200, {}, _anthropic_answer([{"type": "text", "text": "x"}], stop="end_turn"))]
    assert _gw(server).complete(_req(tool_choice=choice), [Route("anthropic", "c")], user_key("anthropic")).ok
    assert server.seen[0]["body"]["tool_choice"] == wire


def test_anthropic_history_tool_use_and_merged_tool_results(server):
    server.script = [(200, {}, _anthropic_answer([{"type": "text", "text": "34C at 14:05"}], stop="end_turn"))]
    msgs = [{"role": "system", "content": "be brief"}] + HISTORY[:1] + [
        dict(HISTORY[1], content="checking")] + HISTORY[2:]
    r = _gw(server).complete(_req(messages=msgs), [Route("anthropic", "c")], user_key("anthropic"))
    assert r.ok and r.text == "34C at 14:05"
    body = server.seen[0]["body"]
    assert body["system"] == "be brief"
    assert body["messages"] == [
        HISTORY[0],
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Jeddah"}},
            {"type": "tool_use", "id": "call_2", "name": "clock", "input": {}}]},
        {"role": "user", "content": [   # consecutive results travel in ONE user turn
            {"type": "tool_result", "tool_use_id": "call_1", "content": "34C sunny " + RES_MARK},
            {"type": "tool_result", "tool_use_id": "call_2", "content": "14:05"}]},
    ]


@pytest.mark.parametrize("block", [
    {"type": "tool_use", "name": "clock", "input": {}},                 # no id
    {"type": "tool_use", "id": "t1", "input": {}},                      # no name
    {"type": "tool_use", "id": "t1", "name": "clock", "input": "{}"},   # input not an object
])
def test_anthropic_malformed_tool_use_is_a_failure(server, block):
    server.script = [(200, {}, _anthropic_answer([block]))]
    r = _gw(server).complete(_req(), [Route("anthropic", "c")], user_key("anthropic"))
    assert r.ok is False and r.reason == "malformed_response" and r.tool_calls == []


# ================================================================ refused before the network

def _tc(cid="c1", name="clock", args="{}"):
    return {"id": cid, "name": name, "arguments": args}


@pytest.mark.parametrize("kw", [
    {"tools": [dict(WEATHER, name="bad name")]},                                   # name pattern
    {"tools": [WEATHER, dict(WEATHER)]},                                            # duplicate name
    {"tools": [dict(WEATHER, extra=1)]},                                            # unknown field
    {"tools": [dict(WEATHER, parameters={"type": "array"})]},                       # schema not an object
    {"tools": [dict(WEATHER, parameters="{}")]},
    {"tools": [dict(WEATHER, description=7)]},
    {"tools": [dict(WEATHER, parameters={"type": "object", "description": "x" * 64_001})]},  # schema too large
    {"tools": []},
    {"tools": [{"name": f"t{i}"} for i in range(65)]},                              # too many
    {"tools": "get_weather"},
    {"tool_choice": "always"},
    {"tool_choice": {"name": "unknown_tool"}},
    {"tool_choice": {"type": "function"}},
    {"tools": None, "tool_choice": "auto"},                                         # choice without tools
    {"messages": [{"role": "tool", "tool_call_id": "c1", "content": "x"}]},         # result without a call
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc()]},
                  {"role": "tool", "tool_call_id": "c2", "content": "x"}]},         # wrong id
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc()]},
                  {"role": "tool", "tool_call_id": "c1", "content": "x"},
                  {"role": "tool", "tool_call_id": "c1", "content": "y"}]},         # answered twice
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc(name="nope")]}]},  # unknown tool
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc(args="[1]")]}]},   # not an object
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc(args="{bad")]}]},
    {"messages": [{"role": "assistant", "content": None, "tool_calls": []}]},
    {"messages": [{"role": "assistant", "content": None}]},                         # no content, no calls
    {"messages": [{"role": "assistant", "content": None, "tool_calls": [_tc(), _tc()]}]},     # duplicate id
    {"messages": [{"role": "user", "content": "x", "tool_calls": [_tc()]}]},        # calls on a user turn
    {"messages": [{"role": "tool", "content": "x"}]},
    {"tools": None, "messages": [{"role": "assistant", "content": None, "tool_calls": [_tc()]}]},  # history needs tools
])
def test_invalid_tool_requests_are_refused(kw):
    with pytest.raises(InvalidRequest):
        _req(**kw)


# ================================================================ capabilities

def test_model_without_tool_support_is_not_called_and_fallback_answers(server):
    caps = {("openrouter", "no/tools"): {"tools": False, "structured_output": True},
            ("openrouter", "yes/tools"): {"tools": True, "structured_output": True}}
    server.script = [(200, {}, _openai_tool_answer([_oa_call("c1", "clock", "{}")]))]
    gw = _gw(server, capability_for=lambda p, m: caps.get((p, m)))
    r = gw.complete(_req(), [Route("openrouter", "no/tools"), Route("openrouter", "yes/tools")],
                    user_key("openrouter"))
    assert r.ok and r.model == "yes/tools" and r.fallback_used is True
    assert r.attempts[0]["reason"] == "tools_not_supported" and r.attempts[0]["http_status"] is None
    assert len(server.seen) == 1 and server.seen[0]["body"]["model"] == "yes/tools"


def test_unknown_capability_is_not_a_refusal(server):
    server.script = [(200, {}, _openai_ok("x"))]
    r = _gw(server, capability_for=lambda p, m: None).complete(_req(), [Route("openai", "m")], user_key())
    assert r.ok and len(server.seen) == 1


def test_structured_output_not_supported_is_reported(server):
    gw = _gw(server, capability_for=lambda p, m: {"tools": True, "structured_output": False})
    r = gw.complete(ChatRequest(messages=[{"role": "user", "content": "hi"}], json_mode=True),
                    [Route("openrouter", "x/y")], user_key("openrouter"))
    assert r.ok is False and r.reason == "structured_output_not_supported" and server.seen == []


def test_capability_only_checked_when_the_request_needs_it(server):
    server.script = [(200, {}, _openai_ok("x"))]
    gw = _gw(server, capability_for=lambda p, m: {"tools": False, "structured_output": False})
    assert gw.complete(ChatRequest(messages=[{"role": "user", "content": "hi"}]),
                       [Route("openrouter", "x/y")], user_key("openrouter")).ok


# ================================================================ no leaks

def test_tool_arguments_and_results_never_logged(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.script = [(200, {}, _openai_tool_answer([_oa_call("c1", "get_weather",
                                                             json.dumps({"city": ARG_MARK}))]))]
    msgs = [HISTORY[0], HISTORY[1], dict(HISTORY[2]), HISTORY[3]]
    r = _gw(server).complete(_req(messages=msgs), [Route("openai", "m")], user_key())
    assert r.ok and ARG_MARK in r.tool_calls[0]["arguments"]
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert ARG_MARK not in logs and RES_MARK not in logs and FAKE not in logs
    assert "tool_calls=1" in logs                       # metadata only: how many, not what


def test_credential_lookup_unchanged_for_tool_requests(server):
    # a tool request still needs a key; no key = no request
    r = _gw(server).complete(_req(), [Route("openai", "m")], lambda p: None)
    assert r.ok is False and r.reason == "no_key" and server.seen == []
    assert isinstance(Credential(provider="openai", secret=FAKE, source="byok"), Credential)


def test_catalog_without_parameter_list_leaves_capability_unknown():
    """A model listed without supported_parameters is unknown (None), never refused."""
    from agos_v2.gateway import parse_openrouter_catalog
    cat = parse_openrouter_catalog({"data": [{"id": "a"}, {"id": "b", "supported_parameters": []}]},
                                   fetched_at="t")
    assert (cat["a"]["tools"], cat["a"]["structured_output"]) == (None, None)
    assert (cat["b"]["tools"], cat["b"]["structured_output"]) == (False, False)
