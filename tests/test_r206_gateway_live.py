"""R-206 (D-158): the model gateway over HTTP, end to end — real Supabase Auth
(GoTrue) + PostgreSQL with migrations 001-006 + server.py's /api/v2 routes +
the BYOK vault + a loopback fake provider (OpenAI-compatible chat, OpenRouter
model list).

FAKE keys and a FAKE provider only: this proves the server's wiring (key
choice from the vault, platform policy, fallback, honest failure, the call
record, isolation, no leaks). It is NOT evidence of a live integration with
any real provider — that is the R-206 live log.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import pathlib
import threading
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.test_r204_accounts_live import (ADMIN_URI, GOTRUE_BIN, SQL1, SQL2, SQL3, STUB,  # noqa: E402
                                           live_stack, new_ws, register, sql)
from tests.test_r205_byok_live import _contains, add, dump_everything, fake_key, kpath  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL4 = ROOT / "docs" / "sql" / "v2" / "004_byok.sql"
SQL5 = ROOT / "docs" / "sql" / "v2" / "005_user_fk.sql"
SQL6 = ROOT / "docs" / "sql" / "v2" / "006_model_calls.sql"
SQL6_RB = ROOT / "docs" / "sql" / "v2" / "006_model_calls_rollback.sql"
DB = "agos_v2_r206_gateway_test"
PLATFORM_KEY = "sk-or-PLATFORM-" + uuid.uuid4().hex
PROMPT_MARK = "PROMPT-" + uuid.uuid4().hex
ANSWER_MARK = "ANSWER-" + uuid.uuid4().hex
ARG_MARK = "ARG-" + uuid.uuid4().hex

pytestmark = pytest.mark.skipif(not (ADMIN_URI and GOTRUE_BIN),
                                reason="needs GOTRUE_BIN and V2_SCHEMA_ADMIN_URI (CI job storage)")


class ChatProvider:
    """Loopback stand-in: POST /<p>/v1/chat/completions answers when the bearer
    key is registered (401 echoing the key otherwise, as real providers do);
    GET /api/v1/models is OpenRouter's public list (one free, one paid model)."""

    def __init__(self):
        self.valid, self.calls, self.outage = set(), [], set()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/api/v1/models":
                    return self._send(200, {"data": [
                        {"id": "vendor/free-model:free", "context_length": 8192,
                         "pricing": {"prompt": "0", "completion": "0"},
                         "architecture": {"input_modalities": ["text"]}, "supported_parameters": []},
                        {"id": "vendor/paid-model", "context_length": 128000,
                         "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                         "architecture": {"input_modalities": ["text", "image"]},
                         "supported_parameters": ["tools", "response_format"]}]})
                self._send(404, {})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                provider = self.path.split("/")[1]
                key = (self.headers.get("Authorization") or "")[7:]
                outer.calls.append({"provider": provider, "key": key, "body": body})
                if provider in outer.outage:
                    return self._send(503, {"error": "overloaded"})
                if key not in outer.valid:
                    return self._send(401, {"error": f"Incorrect API key provided: {key}"})
                usage = {"prompt_tokens": 11, "completion_tokens": 7}
                if provider == "openrouter":
                    usage["cost"] = 0.0000123
                if body.get("tools"):                  # D-159: ask for the first tool
                    name = body["tools"][0]["function"]["name"]
                    return self._send(200, {"id": f"gen-{uuid.uuid4().hex}", "choices": [
                        {"message": {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "call_1", "type": "function",
                             "function": {"name": name, "arguments": json.dumps({"q": ARG_MARK})}}]},
                         "finish_reason": "tool_calls"}], "usage": usage})
                self._send(200, {"id": f"gen-{uuid.uuid4().hex}", "choices": [
                    {"message": {"role": "assistant", "content": ANSWER_MARK}, "finish_reason": "stop"}],
                    "usage": usage})

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def gw():
    from agos_v2.byok import KeyVault
    from agos_v2.gateway import ADAPTERS, Gateway
    from agos_v2.gateway_api import Catalog, platform_policy_from_env
    prov = ChatProvider()
    prov.valid.add(PLATFORM_KEY)
    vault = KeyVault(os.urandom(32))
    catalog = Catalog(prov.url + "/api/v1/models", allow_private=True)
    gateway = Gateway(endpoints={p: f"{prov.url}/{p}/v1" for p in ADAPTERS if p != "ollama"},
                      platform_keys={"openrouter": PLATFORM_KEY},
                      platform_policy=platform_policy_from_env(catalog, {"AGOS_PLATFORM_MODELS": "free"}),
                      price_for=catalog.price_for, capability_for=catalog.capability_for,
                      allow_private=True, sleep=lambda s: None)
    gateway.catalog = catalog
    with live_stack(DB, (STUB, SQL1, SQL2, SQL3, SQL4, SQL5, SQL6),
                    {"vault": vault, "gateway": gateway}) as st:
        st.prov, st.vault, st.gateway = prov, vault, gateway
        yield st
    prov.srv.shutdown()


def call(st, u, ws, routes, prompt=None, **extra):
    body = {"messages": [{"role": "user", "content": prompt or f"hello {PROMPT_MARK}"}], "routes": routes, **extra}
    return st.client.post(f"/api/v2/workspaces/{ws}/model-calls", json=body, headers=u.h)


def calls_of(st, u, ws):
    return st.client.get(f"/api/v2/workspaces/{ws}/model-calls", headers=u.h)


def valid_key(st, tag):
    k = fake_key(tag)
    st.prov.valid.add(k)
    return k


# ================================================================ BYOK call

def test_user_key_call_answers_counts_and_is_recorded(gw):
    st, c = gw, gw.client
    u = register(st, "byok")
    ws = new_ws(st, u)
    k = valid_key(st, "oa")
    kid = add(st, u, ws, "openai", k, "mine").json()["id"]
    r = call(st, u, ws, [{"provider": "openai", "model": "gpt-x"}])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["text"] == ANSWER_MARK
    assert (out["provider"], out["model"], out["key_source"], out["key_id"]) == ("openai", "gpt-x", "byok", kid)
    assert (out["tokens_in"], out["tokens_out"]) == (11, 7)
    # no provider-reported cost and no catalog price for openai: unknown, never 0
    assert out["cost_usd"] is None and out["cost_source"] is None
    assert st.prov.calls[-1]["key"] == k and st.prov.calls[-1]["provider"] == "openai"
    rows = calls_of(st, u, ws).json()
    assert len(rows) == 1 and rows[0]["id"] == out["call_id"]
    assert (rows[0]["ok"], rows[0]["key_id"], rows[0]["tokens_in"], rows[0]["cost_usd"]) == (True, kid, 11, None)
    assert rows[0]["provider_request_id"] == out["provider_request_id"]
    meta = c.get(kpath(ws), headers=u.h).json()[0]
    assert (meta["use_count"], meta["tokens_in"], meta["tokens_out"], meta["cost_usd"]) == (1, 11, 7, "0.000000")


def test_provider_reported_cost_is_kept_at_full_precision(gw):
    st, c = gw, gw.client
    u = register(st, "cost")
    ws = new_ws(st, u)
    kid = add(st, u, ws, "openrouter", valid_key(st, "or"), "or").json()["id"]
    out = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/paid-model"}]).json()
    assert out["ok"] and out["key_source"] == "byok"
    assert (out["cost_usd"], out["cost_source"]) == ("0.0000123", "provider")
    assert st.prov.calls[-1]["body"]["usage"] == {"include": True}
    row = sql(st, "select cost_usd::text, cost_source from agos.model_calls where id = %s", (out["call_id"],))[0]
    assert row == ("0.0000123", "provider")                      # the call log: exact
    meta = c.get(kpath(ws), headers=u.h).json()[0]
    assert meta["id"] == kid and meta["cost_usd"] == "0.000012"   # the key total (004): 6 decimals


# ================================================================ platform key

def test_platform_key_only_for_free_models_and_refusal_is_reported(gw):
    st = gw
    u = register(st, "plat")
    ws = new_ws(st, u)
    ok = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/free-model:free"}]).json()
    assert ok["ok"] and ok["key_source"] == "platform" and ok["key_id"] is None
    assert st.prov.calls[-1]["key"] == PLATFORM_KEY
    n = len(st.prov.calls)
    no = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/paid-model"}]).json()
    assert no["ok"] is False and no["text"] is None and no["reason"] == "platform_key_not_allowed"
    assert len(st.prov.calls) == n                               # nothing was sent
    none = call(st, u, ws, [{"provider": "groq", "model": "llama"}]).json()
    assert none["ok"] is False and none["reason"] == "no_key"
    rows = calls_of(st, u, ws).json()
    assert [(x["ok"], x["reason"], x["key_source"]) for x in rows] == [
        (False, "no_key", None), (False, "platform_key_not_allowed", None), (True, "ok", "platform")]


def test_platform_policy_off_by_default():
    from agos_v2.gateway import Route
    from agos_v2.gateway_api import Catalog, build_gateway, platform_policy_from_env
    cat = Catalog("http://127.0.0.1:9/never")
    cat.models = lambda: {"m:free": {"free": True}}          # even a free model
    assert platform_policy_from_env(cat, {})(Route("openrouter", "m:free")) is False
    assert platform_policy_from_env(cat, {"AGOS_PLATFORM_MODELS": "all"})(Route("openrouter", "m:free")) is False
    assert platform_policy_from_env(cat, {"AGOS_PLATFORM_MODELS": "free"})(Route("openrouter", "m:free")) is True
    assert build_gateway({}, cat).platform_providers() == []
    assert build_gateway({"OPENROUTER_API_KEY": "x"}, cat).platform_providers() == ["openrouter"]


def test_built_gateway_uses_catalog_capabilities():
    """D-159: the served gateway asks the catalog; a model listed without tools is refused
    before any request, an unknown model or provider is left to the provider."""
    from agos_v2.gateway import ChatRequest, Route
    from agos_v2.gateway_api import Catalog, build_gateway
    cat = Catalog("http://127.0.0.1:9/never")
    cat.models = lambda: {"m:free": {"free": True, "tools": False, "structured_output": True}}
    assert cat.capability_for("openrouter", "m:free") == {"tools": False, "structured_output": True}
    assert cat.capability_for("openrouter", "other") is None and cat.capability_for("openai", "m:free") is None
    gw = build_gateway({}, cat)
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], tools=[{"name": "t"}])
    assert gw._missing_capability(Route("openrouter", "m:free"), req) == "tools_not_supported"
    assert gw._missing_capability(Route("openai", "m:free"), req) is None


# ================================================================ fallback and failure

def test_fallback_is_reported_and_outage_is_honest(gw):
    st = gw
    u = register(st, "fb")
    ws = new_ws(st, u)
    add(st, u, ws, "openai", fake_key("bad"), "bad")              # not registered -> 401
    kid = add(st, u, ws, "mistral", valid_key(st, "mi"), "mi").json()["id"]
    out = call(st, u, ws, [{"provider": "openai", "model": "a"}, {"provider": "mistral", "model": "b"}]).json()
    assert out["ok"] and out["fallback_used"] is True and out["provider"] == "mistral" and out["key_id"] == kid
    assert [(a["provider"], a["reason"]) for a in out["attempts"]] == [("openai", "invalid_key"), ("mistral", "ok")]
    st.prov.outage.add("mistral")
    try:
        bad = call(st, u, ws, [{"provider": "mistral", "model": "b"}]).json()
    finally:
        st.prov.outage.discard("mistral")
    assert bad["ok"] is False and bad["text"] is None and bad["reason"] == "provider_unavailable"
    assert bad["attempts"][0]["retries"] == 2
    row = calls_of(st, u, ws).json()[0]
    assert (row["ok"], row["reason"], row["key_source"], row["key_id"], row["http_status"]) == (
        False, "provider_unavailable", "byok", kid, 503)


# ================================================================ keys: choice, revoke, isolation

def test_named_key_rules_and_revoked_key_is_not_used(gw):
    st, c = gw, gw.client
    u = register(st, "named")
    ws = new_ws(st, u)
    k1 = add(st, u, ws, "openai", valid_key(st, "n1"), "one").json()["id"]
    k2v = valid_key(st, "n2")
    k2 = add(st, u, ws, "openai", k2v, "two").json()["id"]
    out = call(st, u, ws, [{"provider": "openai", "model": "m", "key_id": k2}]).json()
    assert out["ok"] and out["key_id"] == k2 and st.prov.calls[-1]["key"] == k2v
    assert call(st, u, ws, [{"provider": "groq", "model": "m", "key_id": k1}]).status_code == 422
    assert call(st, u, ws, [{"provider": "openai", "model": "m", "key_id": k1},
                            {"provider": "openai", "model": "n", "key_id": k2}]).status_code == 422
    assert c.post(kpath(ws, k2, "/revoke"), headers=u.h).status_code == 200
    n = len(st.prov.calls)
    assert call(st, u, ws, [{"provider": "openai", "model": "m", "key_id": k2}]).status_code == 410
    assert len(st.prov.calls) == n
    assert c.delete(kpath(ws, k1), headers=u.h).status_code == 204
    gone = call(st, u, ws, [{"provider": "openai", "model": "m"}]).json()   # no active openai key left
    assert gone["ok"] is False and gone["reason"] == "no_key" and len(st.prov.calls) == n
    # the caller's key in ANOTHER of their workspaces is not used here, even by id
    ws2 = new_ws(st, u, "other")
    k3 = add(st, u, ws2, "openai", valid_key(st, "n3"), "three").json()["id"]
    assert call(st, u, ws, [{"provider": "openai", "model": "m", "key_id": k3}]).status_code == 404
    other = call(st, u, ws, [{"provider": "openai", "model": "m"}]).json()
    assert other["ok"] is False and other["reason"] == "no_key" and len(st.prov.calls) == n


def test_other_users_cannot_use_keys_or_see_calls(gw):
    st, c = gw, gw.client
    a, b = register(st, "iso-a"), register(st, "iso-b")
    ws = new_ws(st, a)
    kid = add(st, a, ws, "openai", valid_key(st, "iso"), "a").json()["id"]
    assert call(st, a, ws, [{"provider": "openai", "model": "m"}]).json()["ok"]
    ws_b = new_ws(st, b)
    n = len(st.prov.calls)
    assert call(st, b, ws, [{"provider": "openai", "model": "m"}]).status_code == 404       # not a member
    assert calls_of(st, b, ws).status_code == 404
    assert call(st, b, ws_b, [{"provider": "openai", "model": "m", "key_id": kid}]).status_code == 404
    assert len(st.prov.calls) == n
    # a fellow member of the same workspace sees neither A's key nor A's calls
    inv = c.post(f"/api/v2/workspaces/{ws}/invitations", json={"email": b.email, "role": "admin"},
                 headers=a.h).json()
    assert c.post("/api/v2/invitations/accept", json={"token": inv["token"]}, headers=b.h).status_code == 200
    assert calls_of(st, b, ws).json() == []
    assert call(st, b, ws, [{"provider": "openai", "model": "m", "key_id": kid}]).status_code == 404
    nokey = call(st, b, ws, [{"provider": "openai", "model": "m"}]).json()
    assert nokey["ok"] is False and nokey["reason"] == "no_key" and len(st.prov.calls) == n
    # B calling the record function directly with A's key id: the row keeps no key and
    # A's key totals do not move
    import asyncio
    from agos_v2.auth import Identity
    from agos_v2.db import AccountsDB
    from tests.test_r205_byok_live import claims_of
    cl = claims_of(b)
    ident = Identity(user_id=cl["sub"], session_id=cl["session_id"], email=cl.get("email"), claims=cl)
    before = sql(st, "select tokens_in, cost_usd from agos.provider_keys where id = %s", (kid,))

    async def forge():
        db = AccountsDB(st.db, min_size=1, max_size=1)
        try:
            async with db.user_tx(ident) as conn:
                cur = await conn.execute(
                    "select agos.model_call_record(%s,%s,'byok','openai','m',true,'ok',200,'stop',false,"
                    "'[]'::jsonb,1000,1000,5,'provider',null,1)", (ws, kid))
                return (await cur.fetchone())[0]
        finally:
            await db.close()
    rid = asyncio.run(forge())
    assert sql(st, "select key_id, user_id::text from agos.model_calls where id = %s", (rid,)) == [(None, b.id)]
    assert sql(st, "select tokens_in, cost_usd from agos.provider_keys where id = %s", (kid,)) == before


def test_signed_out_and_bad_requests_send_nothing(gw):
    st, c = gw, gw.client
    u = register(st, "bad")
    ws = new_ws(st, u)
    n = len(st.prov.calls)
    r = c.post(f"/api/v2/workspaces/{ws}/model-calls", json={"messages": [], "routes": []})
    assert r.status_code == 401
    for body in ({"messages": [{"role": "user", "content": "x"}], "routes": []},
                 {"messages": [{"role": "robot", "content": "x"}], "routes": [{"provider": "openai", "model": "m"}]},
                 {"messages": [{"role": "user", "content": "x"}], "routes": [{"provider": "nope", "model": "m"}]},
                 {"messages": [{"role": "user", "content": "x"}], "routes": [{"provider": "openai", "model": "m"}],
                  "stream": True},
                 {"messages": [{"role": "user", "content": PROMPT_MARK}],
                  "routes": [{"provider": "openai", "model": "m"}], "max_tokens": 0}):
        r = c.post(f"/api/v2/workspaces/{ws}/model-calls", json=body, headers=u.h)
        assert r.status_code == 422, (body, r.text)
        assert PROMPT_MARK not in r.text                          # the input is never echoed
    assert c.post(f"/api/v2/workspaces/{ws}/model-calls", content=b"not json",
                  headers={**u.h, "Content-Type": "application/json"}).status_code == 422
    assert len(st.prov.calls) == n
    assert c.post("/api/v2/auth/logout", headers=u.h).status_code in (200, 204)
    assert call(st, u, ws, [{"provider": "openrouter", "model": "vendor/free-model:free"}]).status_code == 401
    assert len(st.prov.calls) == n


def test_no_call_log_means_no_provider_call(gw):
    """Migration 006 missing (the live database before the owner applies it): the call is
    refused 503 BEFORE any provider is contacted — an unrecordable call is never made."""
    st = gw
    u = register(st, "nolog")
    ws = new_ws(st, u)
    k = valid_key(st, "nolog")
    add(st, u, ws, "openai", k, "mine")
    sig = ("agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer, text, boolean, "
           "jsonb, bigint, bigint, numeric, text, text, integer)")
    n = len(st.prov.calls)
    sql(st, f"alter function {sig} rename to model_call_record_off")
    try:
        r = call(st, u, ws, [{"provider": "openai", "model": "gpt-x"}])
        assert r.status_code == 503 and "006" in r.json()["detail"]
        assert len(st.prov.calls) == n                               # nothing was sent
    finally:
        sql(st, "alter function agos.model_call_record_off(uuid, uuid, text, text, text, boolean, text, "
                "integer, text, boolean, jsonb, bigint, bigint, numeric, text, text, integer) "
                "rename to model_call_record")
    r = call(st, u, ws, [{"provider": "openai", "model": "gpt-x"}])
    assert r.status_code == 200 and r.json()["ok"] is True and len(st.prov.calls) == n + 1


TOOL = {"name": "search", "description": "find", "parameters": {"type": "object",
                                                                   "properties": {"q": {"type": "string"}}}}


def test_tool_call_through_the_api_is_returned_and_recorded_without_arguments(gw):
    """D-159: tool calls come back to the caller (never run by the server); the call log
    keeps metadata only — not the tool arguments."""
    st = gw
    u = register(st, "tools")
    ws = new_ws(st, u)
    add(st, u, ws, "openai", valid_key(st, "tools"), "mine")
    r = call(st, u, ws, [{"provider": "openai", "model": "gpt-x"}], tools=[TOOL], tool_choice="required")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["finish_reason"] == "tool_calls" and out["text"] == ""
    assert out["tool_calls"] == [{"id": "call_1", "name": "search", "arguments": json.dumps({"q": ARG_MARK}),
                                  "arguments_valid": True}]
    sent = st.prov.calls[-1]["body"]
    assert sent["tools"][0]["function"]["name"] == "search" and sent["tool_choice"] == "required"
    row = sql(st, "select finish_reason, row_to_json(c)::text from agos.model_calls c where id = %s",
              (out["call_id"],))[0]
    assert row[0] == "tool_calls" and ARG_MARK not in row[1]
    # the next turn carries the result back; the provider answers in text
    hist = [{"role": "user", "content": "find it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "name": "search", "arguments": json.dumps({"q": "x"})}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "found"}]
    r2 = st.client.post(f"/api/v2/workspaces/{ws}/model-calls", headers=u.h, json={
        "messages": hist, "routes": [{"provider": "openai", "model": "gpt-x"}]})
    assert r2.status_code == 422                       # a tool history needs its tools listed
    r2 = st.client.post(f"/api/v2/workspaces/{ws}/model-calls", headers=u.h, json={
        "messages": hist, "tools": [TOOL], "routes": [{"provider": "openai", "model": "gpt-x"}]})
    assert r2.status_code == 200 and r2.json()["tool_calls"] == [{"id": "call_1", "name": "search",
                                                                  "arguments": json.dumps({"q": ARG_MARK}),
                                                                  "arguments_valid": True}]
    assert st.prov.calls[-1]["body"]["messages"][2] == {"role": "tool", "tool_call_id": "call_1",
                                                         "content": "found"}


def test_catalog_capability_refuses_tools_on_a_model_without_them(gw):
    """The catalog's free model lists no tool support: the platform route is not called,
    the refusal is recorded, and an invalid tool body is 422 without echo."""
    st = gw
    u = register(st, "cap")
    ws = new_ws(st, u)
    n = len(st.prov.calls)
    r = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/free-model:free"}], tools=[TOOL])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is False and out["reason"] == "tools_not_supported" and len(st.prov.calls) == n
    assert sql(st, "select ok, reason from agos.model_calls where id = %s", (out["call_id"],)) == \
        [(False, "tools_not_supported")]
    bad = call(st, u, ws, [{"provider": "openai", "model": "m"}], tools=[dict(TOOL, name=f"bad {ARG_MARK}")])
    assert bad.status_code == 422 and ARG_MARK not in bad.text and len(st.prov.calls) == n


def test_call_rate_limit(gw):
    st = gw
    u = register(st, "rate")
    ws = new_ws(st, u)
    lim = st.svc.gateway_limiter
    saved = lim.limit
    lim.limit = 2
    try:
        codes = [call(st, u, ws, [{"provider": "groq", "model": "m"}]).status_code for _ in range(3)]
    finally:
        lim.limit = saved
    assert codes == [200, 200, 429]


# ================================================================ leaks

def test_no_key_prompt_or_answer_in_db_logs_or_errors(gw, caplog):
    st = gw
    u = register(st, "leak")
    ws = new_ws(st, u)
    k = valid_key(st, "leak")
    add(st, u, ws, "openrouter", k, "leak")
    caplog.set_level(logging.DEBUG)
    prompt = "PRIVATE-" + uuid.uuid4().hex
    ok = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/paid-model"}], prompt=prompt)
    assert ok.json()["ok"]
    st.prov.valid.discard(k)                                     # now the provider echoes the key in a 401
    bad = call(st, u, ws, [{"provider": "openrouter", "model": "vendor/paid-model"}], prompt=prompt)
    assert bad.json()["reason"] == "invalid_key"
    for r in (ok, bad):
        assert not _contains(r.text, k) and not _contains(str(r.headers), k)
        assert not _contains(r.text, PLATFORM_KEY)
    assert prompt not in bad.text
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in (k, PLATFORM_KEY, prompt, ANSWER_MARK):
        assert not _contains(logs, secret), secret
    for blob in dump_everything(st):
        for secret in (k, PLATFORM_KEY, prompt, ANSWER_MARK):
            assert not _contains(blob, secret), secret


# ================================================================ catalog

def test_catalog_route_lists_capabilities_and_prices(gw):
    st = gw
    u = register(st, "cat")
    r = st.client.get("/api/v2/models", headers=u.h)
    assert r.status_code == 200
    by_id = {m["id"]: m for m in r.json()["models"]}
    assert by_id["vendor/free-model:free"]["free"] is True
    paid = by_id["vendor/paid-model"]
    assert (paid["context_length"], paid["vision"], paid["tools"], paid["structured_output"]) == (128000, True, True,
                                                                                                    True)
    assert (paid["price_in_per_token"], paid["price_out_per_token"]) == ("0.000001", "0.000002")
    assert st.client.get("/api/v2/models").status_code == 401


# ================================================================ deletion and rollback

def test_deleted_user_and_deleted_key(gw):
    st, c = gw, gw.client
    u = register(st, "del")
    ws = new_ws(st, u)
    kid = add(st, u, ws, "openai", valid_key(st, "del"), "d").json()["id"]
    cid = call(st, u, ws, [{"provider": "openai", "model": "m"}]).json()["call_id"]
    assert c.delete(kpath(ws, kid), headers=u.h).status_code == 204
    assert sql(st, "select key_id from agos.model_calls where id = %s", (cid,)) == [(None,)]   # set null
    sql(st, "delete from auth.users where id = %s", (u.id,))
    assert sql(st, "select count(*) from agos.model_calls where user_id = %s", (u.id,)) == [(0,)]


def test_006_rollback_is_clean_and_reapplies():
    from tests.test_r204_accounts_live import _uri
    name = "agos_v2_r206_rollback_test"
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {name} with (force)")
        c.execute(f"create database {name}")
    try:
        with psycopg.connect(_uri(name), autocommit=True) as c:
            for f in (STUB, SQL1, SQL2, SQL3, SQL4, SQL5):
                c.execute(f.read_text(encoding="utf-8"))

            def objects():
                return c.execute("""select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                                     where n.nspname = 'agos'""").fetchone()[0]
            before = objects()
            c.execute(SQL6.read_text(encoding="utf-8"))
            assert objects() == before + 1
            assert c.execute("select to_regclass('agos.model_calls')").fetchone()[0] is not None
            fn = c.execute("""select p.prosecdef, p.proconfig from pg_proc p join pg_namespace n
                               on n.oid = p.pronamespace where n.nspname = 'agos'
                               and p.proname = 'model_call_record'""").fetchone()
            assert fn[0] is True and any(x.startswith("search_path=") for x in fn[1])
            assert c.execute("select has_function_privilege('anon', 'agos.model_call_record(uuid, uuid, text, "
                             "text, text, boolean, text, integer, text, boolean, jsonb, bigint, bigint, numeric, "
                             "text, text, integer)', 'execute')").fetchone()[0] is False
            assert c.execute("select relrowsecurity from pg_class where oid = 'agos.model_calls'::regclass"
                             ).fetchone()[0] is True
            c.execute(SQL6_RB.read_text(encoding="utf-8"))
            assert objects() == before
            assert c.execute("select to_regclass('agos.model_calls')").fetchone()[0] is None
            assert c.execute("select to_regclass('agos.provider_keys')").fetchone()[0] is not None
            c.execute(SQL6.read_text(encoding="utf-8"))
            assert objects() == before + 1
    finally:
        with psycopg.connect(_uri("postgres"), autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
