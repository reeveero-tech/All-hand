"""R-160 (M8/P2): verifiable contracts — API / task result / SSE / tool / LLM /
memory / queue / storage, versions and compatibility.

Evidence classes (kept apart on purpose — docs/governance/contracts/README.md):
- UNIT / MOCK: models, compat checker, fake Redis client, monkeypatched LLM
  transport, in-process ASGI app (httpx ASGITransport / TestClient);
- LOCAL INTEGRATION (fake server on 127.0.0.1): aiohttp servers standing in for
  PostgREST / an OpenAI-compatible endpoint, driven by the real clients;
- real uvicorn over TCP: tests/test_r160_local_integration.py.
Nothing here is a live integration (no internet, no cloud service, no secret).

The cases the P2 spec names: valid, invalid, unknown fields, malformed
outputs, version drift, cancelled request, duplicate request — each section
says which it covers.
"""
import asyncio
import copy
import json
import os
import pathlib
import re
import socket
import sys

try:  # the locked dev deps ship httpx2 (Starlette's test client uses it the same way)
    import httpx2 as httpx
except ImportError:  # pragma: no cover — an environment with the original httpx
    import httpx
import pytest
from aiohttp import web
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from agent_civilization.core import contracts as C

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "docs" / "governance" / "contracts" / "examples"
sys.path.insert(0, str(ROOT / "scripts"))
import export_contract_schemas as exporter  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _client(tenant_auth, admin=False):
    import server
    return TestClient(server.app, raise_server_exceptions=False,
                      headers=tenant_auth.admin_headers if admin else tenant_auth.headers)


def _wait_terminal(c, task_id, tries=200):
    import time
    for _ in range(tries):
        t = c.get(f"/api/tasks/{task_id}").json()
        if t.get("status") in ("completed", "failed"):
            return t
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish: {t}")


# ====================================================== versions / drift

def test_committed_schemas_match_the_code():
    """version drift (code vs committed schema): rc=0 only when in sync."""
    assert exporter.check(exporter.generated()) == 0


def test_every_contract_has_a_version_and_a_committed_file():
    schemas = exporter.generated()
    assert set(C.CONTRACTS) | set(C.REQUEST_MODELS) == set(schemas)
    for name, s in schemas.items():
        assert s["x-contract-version"] >= 1
        assert (ROOT / "docs" / "governance" / "contracts" / f"{name}.v{s['x-contract-version']}.schema.json").exists()


class _V1(BaseModel):
    a: int
    b: str = "x"
    s: str = "q"


def _schema(model):
    return model.model_json_schema()


@pytest.mark.parametrize("change,expect", [
    ("same", ([], [])),
    ("add_optional", (["c: added (optional)"], [])),
    ("add_required", ([], ["c: added (required)"])),
    ("remove", ([], ["b: removed"])),
    ("type", ([], ["a: types"])),
    ("now_required", ([], ["b: became required"])),
    ("not_required", ([], ["a: no longer required"])),
    ("forbid_extra", ([], ["unknown-field policy changed"])),
    ("enum_widened", ([], ["s: types"])),
])
def test_compat_checker_classifies_changes(change, expect):
    from typing import Literal, Optional

    from pydantic import ConfigDict
    old = _V1
    if change == "same":
        new = _V1
    elif change == "add_optional":
        class new(_V1):
            c: Optional[int] = None
    elif change == "add_required":
        class new(_V1):
            c: int
    elif change == "remove":
        class new(BaseModel):
            a: int
            s: str = "q"
    elif change == "type":
        class new(BaseModel):
            a: str
            b: str = "x"
            s: str = "q"
    elif change == "now_required":
        class new(BaseModel):
            a: int
            b: str
            s: str = "q"
    elif change == "not_required":
        class new(BaseModel):
            a: int = 0
            b: str = "x"
            s: str = "q"
    elif change == "forbid_extra":
        class new(_V1):
            model_config = ConfigDict(extra="forbid")
    elif change == "enum_widened":
        class old(BaseModel):
            a: int
            b: str = "x"
            s: Literal["q"] = "q"

        class new(BaseModel):
            a: int
            b: str = "x"
            s: Literal["q", "r"] = "q"
    out = C.classify_changes(_schema(old), _schema(new))
    add, brk = expect
    assert len(out["additive"]) == len(add) and all(any(x in a for a in out["additive"]) for x in add), out
    assert len(out["breaking"]) == len(brk) and all(any(x in b for b in out["breaking"]) for x in brk), out


def test_drift_in_a_real_contract_is_reported_as_breaking(tmp_path, monkeypatch, capsys):
    """Simulated version drift: the server drops ``agent`` from chat responses
    without a version bump -> --check fails and names the breaking field."""
    schemas = exporter.generated()
    for name, s in schemas.items():
        (tmp_path / f"{name}.v{s['x-contract-version']}.schema.json").write_text(exporter._dump(s))
    drifted = copy.deepcopy(schemas)
    drifted["chat_response"]["properties"].pop("agent")
    drifted["chat_response"]["required"].remove("agent")
    monkeypatch.setattr(exporter, "OUT", tmp_path)
    assert exporter.check(drifted) == 1
    out = capsys.readouterr().out
    assert "DRIFT chat_response v1 (BREAKING)" in out and ".agent: removed" in out
    assert "bump the contract version" in out


def test_additive_drift_is_reported_as_additive(tmp_path, monkeypatch, capsys):
    schemas = exporter.generated()
    for name, s in schemas.items():
        (tmp_path / f"{name}.v{s['x-contract-version']}.schema.json").write_text(exporter._dump(s))
    drifted = copy.deepcopy(schemas)
    drifted["task_request"]["properties"]["priority"] = {"type": "integer"}
    monkeypatch.setattr(exporter, "OUT", tmp_path)
    assert exporter.check(drifted) == 1
    out = capsys.readouterr().out
    assert "DRIFT task_request v1 (ADDITIVE)" in out and "priority: added (optional)" in out


def test_a_version_bump_keeps_the_previous_schema_file(tmp_path, monkeypatch, capsys):
    """D-064: v2 of a contract requires the v1 file to stay."""
    schemas = exporter.generated()
    bumped = copy.deepcopy(schemas)
    bumped["ready"]["x-contract-version"] = 2
    for name, s in bumped.items():
        (tmp_path / f"{name}.v{s['x-contract-version']}.schema.json").write_text(exporter._dump(s))
    monkeypatch.setattr(exporter, "OUT", tmp_path)
    assert exporter.check(bumped) == 1
    out = capsys.readouterr().out
    assert "MISSING PREVIOUS VERSION" in out and "ready.v1.schema.json" in out
    (tmp_path / "ready.v1.schema.json").write_text(exporter._dump(schemas["ready"]))
    assert exporter.check(bumped) == 0


def test_strict_response_models_detect_an_added_or_removed_field():
    body = json.loads((EXAMPLES / "chat_response.json").read_text())
    C.validate("chat_response", body)
    with pytest.raises(ValidationError):
        C.validate("chat_response", {**body, "trace_id": "abc"})     # added by the server
    with pytest.raises(ValidationError):
        C.validate("chat_response", {k: v for k, v in body.items() if k != "routing"})


# ============================================================ API: valid

def test_task_create_poll_list_follow_the_contracts(tenant_auth):
    c = _client(tenant_auth)
    r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "analyze 1 2 3 4", "data": [1, 2, 3, 4]})
    assert r.status_code == 202
    acc = C.validate("task_accepted", r.json())
    rec = _wait_terminal(c, acc.task_id)
    C.validate("task_record", rec)
    lst = c.get("/api/tasks?limit=50").json()
    C.validate("task_list", lst)
    assert lst["source"] == "memory"


def test_chat_and_its_record_follow_the_contracts(tenant_auth):
    c = _client(tenant_auth)
    body = c.post("/api/chat", json={"message": "sum the numbers 1 2 3"}).json()
    resp = C.validate("chat_response", body)
    C.validate("task_record", c.get(f"/api/tasks/{resp.task_id}").json())


def test_execute_and_its_record_follow_the_contracts(tenant_auth):
    c = _client(tenant_auth)
    body = c.post("/civilization/execute", json={"message": "sum 1 2 3"}).json()
    resp = C.validate("execute_response", body)
    rec = c.get(f"/api/tasks/{resp.task_id}").json()
    C.validate("execute_record", rec)
    # the list mixes variants: each item validates as one of them
    C.validate("task_list", c.get("/api/tasks").json())


def test_status_ready_memory_follow_the_contracts(tenant_auth):
    c = _client(tenant_auth)
    C.validate("system_status", c.get("/api/system/status").json())
    r = c.get("/health/ready")
    assert r.status_code == 200
    C.validate("ready", r.json())
    c.post("/api/chat", json={"message": "sum 4 5"})
    C.validate("memory_list", c.get("/api/memory?limit=5").json())


# ======================================================= API: invalid

@pytest.mark.parametrize("path,body,code", [
    ("/api/tasks", {"prompt": "x"}, 422),                                   # missing field
    ("/api/tasks", {"specialization": "analyst", "prompt": ""}, 422),       # min_length
    ("/api/tasks", {"specialization": "analyst", "prompt": 5}, 422),        # wrong type
    ("/api/tasks", {"specialization": "analyst", "prompt": "x", "allow_public_llm": "yes"}, 422),  # strict bool
    ("/api/tasks", [1, 2], 422),                                            # not an object
    ("/api/chat", {}, 422),
    ("/civilization/execute", {"message": 3}, 422),
])
def test_invalid_requests_are_422_with_the_validation_contract(tenant_auth, path, body, code):
    r = _client(tenant_auth).post(path, json=body)
    assert r.status_code == code
    C.validate("validation_error", r.json())


def test_malformed_json_body_is_422(tenant_auth):
    r = _client(tenant_auth).post("/api/tasks", content=b"{not json",
                                  headers={"Content-Type": "application/json", **tenant_auth.headers})
    assert r.status_code == 422
    C.validate("validation_error", r.json())


@pytest.mark.parametrize("req,code", [
    (("get", "/api/tasks/doesnotexist", None), 404),
    (("post", "/api/tasks", {"specialization": "nope", "prompt": "x"}), 400),
    (("post", "/api/tasks", {"specialization": "external", "prompt": "x"}), 409),
    (("post", "/civilization/execute", {"message": "   "}), 400),
])
def test_error_bodies_follow_the_error_contract(tenant_auth, req, code, monkeypatch):
    import server
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [])
    c = _client(tenant_auth)
    method, path, body = req
    r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == code
    C.validate("error_detail", r.json())


def test_unauthenticated_is_401_with_the_error_contract(tenant_auth):
    import server
    r = TestClient(server.app).get("/api/tasks")
    assert r.status_code == 401
    C.validate("error_detail", r.json())


# ================================================== API: unknown fields

def test_unknown_request_fields_are_ignored_not_honoured(tenant_auth):
    """Contract: unknown request fields are dropped (not rejected). A tenant_id
    in the body never changes the owner (SEC-02); no extra key reaches the record."""
    c = _client(tenant_auth)
    r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "analyze 2 4",
                                   "tenant_id": "someone_else", "priority": 9, "x": {"y": 1}})
    assert r.status_code == 202
    rec = _wait_terminal(c, r.json()["task_id"])
    C.validate("task_record", rec)      # strict: no 'priority'/'x' in the record
    assert rec["tenant_id"] == tenant_auth.tenant
    chat = c.post("/api/chat", json={"message": "sum 1 1", "unknown": True}).json()
    C.validate("chat_response", chat)


def test_unknown_fields_in_tool_output_are_allowed_but_core_keys_typed():
    C.validate("handler_result", {"ok": True, "answer": "a", "extra_metric": 3})
    with pytest.raises(ValidationError):
        C.validate("handler_result", {"ok": "yes"})
    with pytest.raises(ValidationError):
        C.validate("handler_result", {"ok": True, "answer": 3})


# ============================================ task result: real/specialists

SPEC_INPUTS = {"analyst": ("analyze", [1, 2, 3]), "data_processor": ("sum", [1, 2, 3])}


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_agent_outcomes_of_all_20_specializations_follow_the_contract(impl, tenant_context):
    """Both implementations, 20/20 specializations: every outcome (success or
    failure — LLM-dependent handlers fail without a provider) matches
    agent_outcome v1, and every handler result matches handler_result v1."""
    from agent_civilization.agents.real import create_civilization
    civ = create_civilization(agents_per_spec=1, impl=impl)
    assert len(civ) == 20

    async def run():
        out = {}
        for spec, agents in civ.items():
            prompt, data = SPEC_INPUTS.get(spec, (f"{spec}: sum 1 2 3", [1, 2, 3]))
            out[spec] = await agents[0].execute({"prompt": prompt, "data": data})
        return out
    outcomes = asyncio.run(run())
    for spec, o in outcomes.items():
        m = C.validate("agent_outcome", o)
        C.validate("handler_result", o["result"])
        assert (m.impl is not None) == (impl == "specialists"), spec


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_a_raising_handler_yields_a_failed_outcome_by_contract(impl, tenant_context, monkeypatch):
    """malformed output path: a handler that raises still produces a valid
    ok=False outcome whose error carries no exception text."""
    from agent_civilization.agents.real import create_civilization
    from agent_civilization.core import experience
    civ = create_civilization(agents_per_spec=1, impl=impl)

    async def boom(*a, **k):
        raise RuntimeError("SYNTHETIC-SECRET-r160-handler")
    # both implementations run their handler through run_with_experience
    monkeypatch.setattr(experience, "run_with_experience", boom)
    agent = civ["analyst"][0]
    o = asyncio.run(agent.execute({"prompt": "analyze", "data": [1, 2]}))
    m = C.validate("agent_outcome", o)
    assert m.ok is False and o["result"]["error"] == "RuntimeError"
    assert "SYNTHETIC-SECRET" not in json.dumps(o)


# ============================================================ LLM / tools

@pytest.mark.parametrize("body,reason", [
    ({"choices": [{"message": {"content": "hi"}}]}, None),
    ({"choices": [{"message": {"content": None}}]}, "empty completion"),
    ({"choices": [{"message": {"content": "   "}}]}, "empty completion"),
    ({"choices": [{"message": {"content": ["a"]}}]}, "malformed completion"),
    ({"choices": []}, "malformed completion"),
    ({"error": "x"}, "malformed completion"),
    ([1, 2], "malformed completion"),
    ("text", "malformed completion"),
    (None, "malformed completion"),
])
def test_completion_content_rule(body, reason):
    from agent_civilization.core.llm import MalformedCompletion, completion_content
    if reason is None:
        assert completion_content(body) == "hi"
    else:
        with pytest.raises(MalformedCompletion) as ei:
            completion_content(body)
        assert ei.value.reason == reason


async def _completion_server(box):
    async def handler(request):
        await request.read()
        box["calls"] = box.get("calls", 0) + 1
        return web.Response(status=200, text=box["text"], content_type="application/json")
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, port


@pytest.mark.parametrize("text,ok,err", [
    (json.dumps({"choices": [{"message": {"content": "hello"}}], "model": "m"}), True, None),
    (json.dumps({"choices": [{"message": {"content": None}}]}), False, "LLM empty completion"),
    (json.dumps({"choices": [{"message": {"content": ""}}]}), False, "LLM empty completion"),
    (json.dumps({"choices": "nope"}), False, "LLM malformed completion"),
    (json.dumps({"unexpected": True}), False, "LLM malformed completion"),
])
async def test_openrouter_path_malformed_completions_are_failures(text, ok, err, monkeypatch):
    """LOCAL INTEGRATION (fake OpenAI-compatible server on 127.0.0.1) through
    the real LLMClient: before R-160 content=None/"" came back ok=True."""
    from agent_civilization.core import llm as L
    box = {"text": text}
    runner, port = await _completion_server(box)
    try:
        monkeypatch.setattr(L, "OPENROUTER_URL", f"http://127.0.0.1:{port}/v1/chat")
        client = L.LLMClient(api_key="test-key-not-a-secret")
        assert client.provider == "openrouter"
        res = await client.complete("s", "p")
        await client.close()
    finally:
        await runner.cleanup()
    assert box.get("calls") == 1, "the fake endpoint must have been the one called"
    m = C.validate("llm_result", res)
    assert m.ok is ok and m.error == err


def test_keyless_malformed_completion_fails_fast_without_retrying(monkeypatch):
    """MOCK transport: a structurally wrong 200 is not re-sent (it used to be
    retried as a network error until the 90 s budget ended)."""
    from agent_civilization.core import llm as L
    from agent_civilization.core import pools
    calls = {"n": 0}

    async def fake_run(pool, fn, *a):
        calls["n"] += 1
        return {"choices": "malformed"}
    monkeypatch.setattr(pools, "run", fake_run)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    res = asyncio.run(L._keyless_attempts("s", "p", 10))
    assert calls["n"] == 1
    m = C.validate("llm_result", res)
    assert m.error == "keyless LLM malformed completion"


def test_keyless_empty_completion_is_still_retried_within_budget(monkeypatch):
    from agent_civilization.core import llm as L
    from agent_civilization.core import pools
    answers = iter([{"choices": [{"message": {"content": ""}}]},
                    {"choices": [{"message": {"content": "ok now"}}]}])

    async def fake_run(pool, fn, *a):
        return next(answers)
    monkeypatch.setattr(pools, "run", fake_run)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    res = asyncio.run(L._keyless_attempts("s", "p", 10))
    m = C.validate("llm_result", res)
    assert m.ok and m.content == "ok now" and res["attempts"] == 2


@pytest.mark.parametrize("text,ok,err", [
    (json.dumps({"choices": [{"message": {"content": "pong"}}]}), True, None),
    (json.dumps({"choices": [{"message": {"content": None}}]}), False, "empty completion"),
    (json.dumps({"choices": {}}), False, "malformed completion"),
    ("<html>not json</html>", False, "JSONDecodeError"),
])
async def test_external_agent_malformed_outputs(text, ok, err):
    """LOCAL INTEGRATION (fake endpoint on 127.0.0.1) through the real
    ExternalAgentAdapter: null content used to be ok=True with answer=None."""
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter
    box = {"text": text}
    runner, port = await _completion_server(box)
    try:
        a = ExternalAgentAdapter(name="ext_probe", url=f"http://127.0.0.1:{port}/v1/chat/completions",
                                 allow_private=True)
        out = await a.execute({"prompt": "ping"})
    finally:
        await runner.cleanup()
    C.validate("agent_outcome", out)
    assert out["ok"] is ok
    if ok:
        assert out["result"]["answer"] == "pong"
    else:
        assert out["result"]["error"] == err


# ================================================================== SSE

async def _asgi(tenant_auth):
    import server
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                             base_url="http://agos.test", headers=tenant_auth.headers)


async def test_sse_events_of_a_task_follow_the_frame_contract(tenant_auth):
    import server
    sub = server.EVENT_BUS.open(tenant_auth.tenant, "contract-test")
    try:
        async with await _asgi(tenant_auth) as c:
            r = await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "analyze 3 4"})
            tid = r.json()["task_id"]
            got = []
            for _ in range(10):
                ev = await sub.next(timeout=5)
                if ev is None:
                    break
                got.append(ev)
                if ev.get("type") == "task_completed" and ev.get("task_id") == tid:
                    break
    finally:
        server.EVENT_BUS.close(sub)
    mine = [e for e in got if e.get("task_id") == tid]
    assert [e["type"] for e in mine] == ["task_created", "task_started", "task_completed"]
    for e in got:
        C.SSEFrame.model_validate({"event": e})


def test_sse_examples_and_server_heartbeat_literal_follow_the_contract():
    for e in json.loads((EXAMPLES / "sse_frames.json").read_text()):
        C.SSEFrame.model_validate({"event": e})
    src = (ROOT / "server.py").read_text()
    assert "json.dumps({'type': 'heartbeat'})" in src     # the frame the server writes
    with pytest.raises(ValidationError):
        C.SSEFrame.model_validate({"event": {"type": "task_progress", "task_id": "x"}})   # unknown type


async def test_sse_overflow_yields_a_stream_gap_frame(tenant_auth, monkeypatch):
    from agent_civilization.core.sse import EventBus, SSELimits
    bus = EventBus(SSELimits(queue_size=2))
    sub = bus.open("t", "c")
    for i in range(5):
        bus.publish("t", {"type": "task_created", "task_id": f"{i:012x}", "specialization": "analyst"})
    first = await sub.next(timeout=1)
    C.SSEFrame.model_validate({"event": first})
    assert first["type"] == "stream_gap" and first["missed"] == 3
    bus.close(sub)


async def test_sse_disconnect_releases_and_reconnect_has_no_replay(tenant_auth):
    """cancelled request (stream side): closing a subscription frees its slot
    (idempotent); a new subscription receives only events published after it
    opened — no replay (the frames carry no id:, Last-Event-ID unsupported)."""
    from agent_civilization.core.sse import EventBus, SSELimits
    bus = EventBus(SSELimits())
    s1 = bus.open("t", "c")
    bus.publish("t", {"type": "task_created", "task_id": "a" * 12, "specialization": "analyst"})
    assert bus.total == 1
    bus.close(s1)
    bus.close(s1)                      # idempotent
    assert bus.total == 0
    s2 = bus.open("t", "c")
    assert await s2.next(timeout=0.2) is None          # the earlier event is not replayed
    bus.publish("t", {"type": "task_created", "task_id": "b" * 12, "specialization": "analyst"})
    assert (await s2.next(timeout=1))["task_id"] == "b" * 12
    bus.close(s2)


# ===================================================== cancelled request

async def test_cancelled_background_run_ends_the_record_failed(tenant_auth, monkeypatch):
    """cancelled request (server side): cancelling the background run (e.g. at
    shutdown) used to leave the record "running" forever. Now it ends failed
    with a fixed reason and a task_completed frame is published. The storage
    row is NOT rewritten during cancellation — documented gap G-160-2."""
    import server
    started = asyncio.Event()

    async def slow_execute(self, task):
        started.set()
        await asyncio.sleep(30)
    agent = server.CIVILIZATION["analyst"][0]
    monkeypatch.setattr(type(agent), "execute", slow_execute)
    sub = server.EVENT_BUS.open(tenant_auth.tenant, "cancel-test")
    try:
        async with await _asgi(tenant_auth) as c:
            tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "slow"})).json()["task_id"]
            await asyncio.wait_for(started.wait(), 5)
            runs = [t for t in server._BACKGROUND_TASKS if not t.done()]
            assert runs, "the run must be strongly referenced while it runs"
            for t in runs:
                t.cancel()
            await asyncio.gather(*runs, return_exceptions=True)
            rec = (await c.get(f"/api/tasks/{tid}")).json()
        frames = []
        while (ev := await sub.next(timeout=0.2)) is not None:
            frames.append(ev)
    finally:
        server.EVENT_BUS.close(sub)
    C.validate("task_record", rec)
    assert rec["status"] == "failed"
    assert rec["result"]["result"] == {"error": "task cancelled before completion"}
    done = [f for f in frames if f.get("type") == "task_completed" and f["task_id"] == tid]
    assert len(done) == 1 and done[0]["ok"] is False
    C.SSEFrame.model_validate({"event": done[0]})
    assert not [t for t in server._BACKGROUND_TASKS if t.done()]   # references released


async def test_unexpected_error_in_a_run_ends_the_record_failed(tenant_auth, monkeypatch):
    import server

    async def broken(self, task):
        raise RuntimeError("SYNTHETIC-SECRET-r160-run")
    agent = server.CIVILIZATION["analyst"][0]
    monkeypatch.setattr(type(agent), "execute", broken)
    async with await _asgi(tenant_auth) as c:
        tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x"})).json()["task_id"]
        for _ in range(100):
            rec = (await c.get(f"/api/tasks/{tid}")).json()
            if rec["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(0.02)
    C.validate("task_record", rec)
    assert rec["status"] == "failed" and rec["result"]["result"] == {"error": "task failed: internal error"}
    assert "SYNTHETIC-SECRET" not in json.dumps(rec)


def test_execute_route_error_does_not_leave_a_running_record(tenant_auth, monkeypatch):
    import server
    from agent_civilization import civilization_routes as cr

    async def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(cr, "execute_plan", broken)
    before = set(server.TASKS)
    r = _client(tenant_auth).post("/civilization/execute", json={"message": "sum 1 2"})
    assert r.status_code == 500
    new = [server.TASKS[t] for t in set(server.TASKS) - before]
    assert len(new) == 1 and new[0]["status"] == "failed"
    C.validate("execute_record", new[0])


# ===================================================== duplicate request

def test_duplicate_task_requests_are_two_tasks(tenant_auth):
    """duplicate request: POST /api/tasks has no idempotency key (contract v1):
    two identical requests are two tasks, each run and recorded once."""
    c = _client(tenant_auth)
    body = {"specialization": "analyst", "prompt": "analyze 5 6 7"}
    a, b = c.post("/api/tasks", json=body).json(), c.post("/api/tasks", json=body).json()
    assert a["task_id"] != b["task_id"]
    for t in (a, b):
        C.validate("task_record", _wait_terminal(c, t["task_id"]))
    ids = [t["id"] for t in c.get("/api/tasks?limit=200").json()["tasks"]]
    assert ids.count(a["task_id"]) == 1 and ids.count(b["task_id"]) == 1


def test_duplicate_chat_requests_are_two_records(tenant_auth):
    c = _client(tenant_auth)
    x = c.post("/api/chat", json={"message": "sum 2 2"}).json()
    y = c.post("/api/chat", json={"message": "sum 2 2"}).json()
    assert x["task_id"] != y["task_id"]


def test_duplicate_external_agent_delete_is_404_the_second_time(tenant_auth, monkeypatch):
    import server
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter
    monkeypatch.setattr(server, "EXTERNAL_AGENTS",
                        [ExternalAgentAdapter(name="dup_probe", url="http://127.0.0.1:9/v1", allow_private=True)])
    a = _client(tenant_auth, admin=True)
    assert a.delete("/api/agents/external/dup_probe").status_code == 200
    r = a.delete("/api/agents/external/dup_probe")
    assert r.status_code == 404
    C.validate("error_detail", r.json())


# ================================================================== queue

def _queue():
    from agos_kernel.infrastructure.redis_queue import RedisQueue
    return RedisQueue(redis_url="redis://localhost:6379")


def test_queue_fallback_lifecycle_and_payload_contract():
    from agos_kernel.infrastructure.redis_queue import TaskStatus
    q = _queue()

    async def run():
        tid = await q.enqueue("analyst", "p", data={"a": 1}, priority=2)
        C.validate("queue_task", q._fallback_tasks[tid].to_dict())
        t = await q.dequeue(timeout=0.1)
        assert t.id == tid and t.status == TaskStatus.RUNNING and t.attempts == 1
        C.validate("queue_task", t.to_dict())
        assert await q.fail(tid, "e1") is True          # retried (1 < 3)
        t = await q.dequeue(timeout=0.1)
        await q.complete(tid, {"ok": True})
        C.validate("queue_task", (await q.get_task(tid)).to_dict())
        return (await q.get_task(tid)).status
    assert asyncio.run(run()) == TaskStatus.COMPLETED


def test_queue_duplicate_enqueue_is_idempotent_in_fallback():
    """duplicate request (queue): before R-160 the fallback queued a known id
    twice (executed twice) while Redis deduplicated it."""
    q = _queue()

    async def run():
        await q.enqueue("analyst", "p", task_id="dup-1")
        await q.enqueue("analyst", "p-changed", task_id="dup-1")
        first = await q.dequeue(timeout=0.1)
        second = await q.dequeue(timeout=0.1)
        return first, second
    first, second = asyncio.run(run())
    assert first.id == "dup-1" and first.prompt == "p" and second is None


class FakeRedis:
    """MOCK of the redis.asyncio subset RedisQueue uses (in-memory)."""

    def __init__(self):
        self.h, self.z = {}, {}

    async def hset(self, name, key, value):
        self.h.setdefault(name, {})[key] = value

    async def hget(self, name, key):
        return self.h.get(name, {}).get(key)

    async def hexists(self, name, key):
        return key in self.h.get(name, {})

    async def zadd(self, name, mapping):
        self.z.setdefault(name, {}).update(mapping)

    async def zpopmin(self, name, count=1):
        z = self.z.get(name, {})
        if not z:
            return []
        k = min(z, key=z.get)
        return [(k, z.pop(k))]

    async def blpop(self, name, timeout=0):
        return None

    async def zcard(self, name):
        return len(self.z.get(name, {}))


def _redis_queue():
    q = _queue()
    q._use_fallback = False
    q._redis = FakeRedis()
    return q


def test_queue_redis_mode_retry_requeues_with_the_enqueue_priority(monkeypatch):
    """MOCK Redis: fail() used to call self.zadd (AttributeError, never
    requeued) with the opposite sign of enqueue()."""
    from agos_kernel.infrastructure import redis_queue as rq
    q = _redis_queue()

    async def no_sleep(_):
        return None
    monkeypatch.setattr(rq.asyncio, "sleep", no_sleep)

    async def run():
        await q.enqueue("analyst", "p", task_id="r-1", priority=5)
        score_enqueued = q._redis.z[q.queue_name]["r-1"]
        t = await q.dequeue(timeout=0)
        assert t.id == "r-1"
        retried = await q.fail("r-1", "e")
        return score_enqueued, retried, dict(q._redis.z.get(q.queue_name, {}))
    score, retried, z = asyncio.run(run())
    assert retried is True and z == {"r-1": score} and score == -5


def test_queue_redis_mode_duplicate_enqueue_is_idempotent():
    q = _redis_queue()

    async def run():
        await q.enqueue("analyst", "p", task_id="d-1")
        await q.enqueue("analyst", "other", task_id="d-1")
        return json.loads(q._redis.h[q.results_name]["d-1"])
    stored = asyncio.run(run())
    assert stored["prompt"] == "p"
    C.validate("queue_task", stored)


@pytest.mark.parametrize("raw", [
    "not json", json.dumps({"specialization": "a"}),                 # missing id
    json.dumps({"id": "x", "specialization": "a", "prompt": "p", "status": "weird"}),
])
def test_queue_malformed_stored_payload_is_none_not_a_crash(raw, caplog):
    q = _redis_queue()
    q._redis.h[q.results_name] = {"m-1": raw}
    assert asyncio.run(q.get_task("m-1")) is None
    assert "Failed to get task" in caplog.text


def test_queue_payload_from_a_newer_writer_is_read_forward_compatibly():
    """version drift (queue): extra keys written by a newer worker are ignored
    by Task.from_dict (readers stay compatible); the v1 contract itself is strict
    so the drift is visible to the tests."""
    from agos_kernel.infrastructure.redis_queue import Task
    t = Task(id="f-1", specialization="a", prompt="p").to_dict()
    newer = {**t, "lease_owner": "worker-9", "contract_version": 2}
    assert Task.from_dict(newer).id == "f-1"
    with pytest.raises(ValidationError):
        C.validate("queue_task", newer)


# ================================================================ storage

def _sql_columns():
    cols, table = {}, None
    for f in ("001_init.sql", "002_tenant_isolation.sql"):
        for line in (ROOT / "docs" / "sql" / f).read_text().splitlines():
            m = re.match(r"\s*create table if not exists (\w+)", line, re.I)
            if m:
                table = m.group(1)
                cols.setdefault(table, set())
                continue
            m = re.match(r"\s*alter table (\w+)\s+add column if not exists (\w+)", line, re.I)
            if m:
                cols.setdefault(m.group(1), set()).add(m.group(2))
                continue
            if table and line.strip().startswith(")"):
                table = None
            elif table:
                m = re.match(r"\s+(\w+)\s+\w+", line)
                if m:
                    cols[table].add(m.group(1))
    return cols


async def _fake_postgrest(box):
    async def handler(request):
        body = await request.read()
        box.setdefault("writes", []).append((request.method, request.path, json.loads(body) if body else None))
        if request.method == "GET":
            return web.json_response(box.get("read", []), status=200)
        if request.method == "PATCH":
            return web.Response(status=204)
        return web.json_response([], status=201)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, port


def _configure_storage(monkeypatch, port):
    from agent_civilization.storage import supabase_store as st
    monkeypatch.setattr(st, "SUPABASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(st, "SUPABASE_KEY", "sb_secret_" + "k" * 24)
    monkeypatch.setattr(st, "SUPABASE_KEY_SOURCE", "SUPABASE_SERVICE_KEY")
    monkeypatch.setattr(st, "_storage", None)     # a fresh client bound to this loop


async def test_storage_writes_of_a_task_and_a_chat_follow_the_contracts(tenant_auth, monkeypatch):
    """LOCAL INTEGRATION (fake PostgREST on 127.0.0.1) through the real server
    flow: every body written matches its storage contract, and every key is a
    column of docs/sql (001 + 002)."""
    import server
    box = {}
    runner, port = await _fake_postgrest(box)
    try:
        _configure_storage(monkeypatch, port)
        async with await _asgi(tenant_auth) as c:
            tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "analyze 1 2"})).json()["task_id"]
            for _ in range(100):
                if server.TASKS[tid]["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            await c.post("/api/chat", json={"message": "sum 3 4"})
    finally:
        await runner.cleanup()
    cols = _sql_columns()
    kinds = {"tasks:POST": "storage_task_insert", "tasks:PATCH": "storage_task_update",
             "events:POST": "storage_event_insert", "agent_stats:POST": "storage_agent_stats"}
    seen = set()
    for method, path, body in box.get("writes", []):
        if method == "GET":
            continue
        table = path.rsplit("/", 1)[-1]
        kind = kinds[f"{table}:{method}"]
        seen.add(kind)
        C.validate(kind, body)
        assert set(body) <= cols[table], (table, set(body) - cols[table])
    assert seen == set(kinds.values()), seen


@pytest.mark.parametrize("read,expect_rows", [
    ({"message": "not a list"}, 0),
    ([{"specialization": "a"}], 0),                     # row without task_id
    (["string-row"], 0),
    ([{"task_id": "abcdef012345", "specialization": "analyst", "status": "completed",
       "created_at": "2026-10-17T00:00:00+00:00"}], 1),
])
async def test_storage_malformed_reads_are_no_rows(read, expect_rows, monkeypatch):
    """malformed output (storage): a non-list body or a row without task_id is
    a recorded failure and no rows — never handed to the API as rows."""
    from agent_civilization.storage import supabase_store as st
    box = {"read": read}
    runner, port = await _fake_postgrest(box)
    try:
        _configure_storage(monkeypatch, port)
        s = st.SupabaseStorage()
        rows = await s.get_tasks(limit=5, scope="test_tenant")
        one = await s.get_task("abcdef012345", scope="test_tenant")
        other = await s.get_task("0123456789ab", scope="test_tenant")   # row id mismatch
        await s.close()
    finally:
        await runner.cleanup()
    assert len(rows) == expect_rows
    for r in rows:
        C.validate("task_row", r)
    assert (one is not None) == bool(expect_rows)
    assert other is None
    if not expect_rows:
        assert st.storage_stats()["last_ok"] is False


async def test_malformed_storage_row_is_404_not_500(tenant_auth, monkeypatch):
    """Before R-160: a stored row without task_id made GET /api/tasks/{id} a 500."""
    box = {"read": [{"specialization": "analyst"}]}
    runner, port = await _fake_postgrest(box)
    try:
        _configure_storage(monkeypatch, port)
        async with await _asgi(tenant_auth) as c:
            r = await c.get("/api/tasks/abcdef012345")
            lst = await c.get("/api/tasks")
    finally:
        await runner.cleanup()
    assert r.status_code == 404
    C.validate("error_detail", r.json())
    assert lst.status_code == 200 and lst.json()["source"] == "memory"   # falls back, no fake rows
    C.validate("task_list", lst.json())


# ========================================================= web <-> API

def test_shared_examples_validate_and_match_live_shapes(tenant_auth):
    """The example payloads the web tests consume (docs/governance/contracts/examples)
    validate against the contracts AND have the same keys as live responses of
    the in-process app — so the web tests cannot drift from the API."""
    mem = json.loads((EXAMPLES / "task_list.memory.json").read_text())
    db = json.loads((EXAMPLES / "task_list.database.json").read_text())
    chat = json.loads((EXAMPLES / "chat_response.json").read_text())
    C.validate("task_list", mem)
    C.validate("task_list", db)
    C.validate("chat_response", chat)
    c = _client(tenant_auth)
    live_chat = c.post("/api/chat", json={"message": "sum 1 2 3"}).json()
    # the examples are recorded under AGOS_AGENT_IMPL=real; =specialists adds
    # exactly one documented key to the outcome (agent_outcome.impl)
    extra = {"impl"} if os.environ.get("AGOS_AGENT_IMPL", "real") == "specialists" else set()
    assert set(live_chat) == set(chat) and set(live_chat["outcome"]) == set(chat["outcome"]) | extra
    ex = c.post("/civilization/execute", json={"message": "sum 1 2 3"}).json()
    live_exec_rec = c.get(f"/api/tasks/{ex['task_id']}").json()
    assert set(live_exec_rec) == set(mem["tasks"][1])
    queued = c.post("/api/tasks", json={"specialization": "designer", "prompt": "design"}).json()
    rec = c.get(f"/api/tasks/{queued['task_id']}").json()
    assert set(mem["tasks"][0]) <= set(rec)          # queued keys are a subset of any later state
    done = _wait_terminal(c, queued["task_id"])
    assert set(done) == set(mem["tasks"][2])


def test_every_field_the_web_task_view_reads_is_in_a_task_contract():
    src = (ROOT / "web" / "src" / "taskView.js").read_text()
    read = set(re.findall(r"\bt\.(\w+)", src)) | {f"plan.{x}" for x in re.findall(r"plan && str\(plan\.(\w+)\)", src)} \
        | {f"result.{x}" for x in re.findall(r"outcome && (?:str|num)\(outcome\.(\w+)\)", src)}
    props = set()
    for name in ("task_record", "execute_record", "task_row"):
        props |= set(C.CONTRACTS[name][1].model_fields)
    outcome = set(C.AgentOutcome.model_fields)
    for f in read:
        if f.startswith("plan."):
            continue                        # plan is an open object (auto_route output)
        if f.startswith("result."):
            assert f.split(".", 1)[1] in outcome, f
        else:
            assert f in props, f
    assert {"agent_name", "agent", "elapsed_s", "message", "task_id", "id"} <= read
