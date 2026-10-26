"""R-159 (D-071): safe, correlated telemetry.

Evidence classes (TEST_MATRIX): ``unit`` = the telemetry module alone;
``integration (local)`` = the real FastAPI app in-process with LOCAL doubles
over real loopback HTTP (a fake OpenRouter and a fake PostgREST) — these are
MOCK/LOCAL integrations, not live: no paid LLM, no cloud Supabase is reached.

Markers used below are synthetic secrets; a test fails if any of them appears
in ANY log record of ANY logger (not only agos.telemetry) or in any
telemetry record or metric line.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import threading
import urllib.parse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.core import experience, llm as llm_mod, telemetry, tenancy
from agent_civilization.core.tenancy import Principal
from agent_civilization.storage import supabase_store as ss
from agos_kernel.infrastructure.metrics import get_metrics

PROMPT_SECRET = "R159-PROMPT-SECRET-c41f"
ANSWER_SECRET = "R159-ANSWER-SECRET-9b2e"
BACKEND_SECRET = "R159-BACKEND-SECRET-77aa"
LLM_KEY = "sk-or-R159-LLM-KEY-5e5e"
STORAGE_KEY = "r159-storage-service-key-0d0d"
TENANT = "tenant_r159_raw_id"


# ----------------------------------------------------------------- fixtures --
@pytest.fixture(autouse=True)
def _clean_telemetry(monkeypatch):
    monkeypatch.delenv("AGOS_TELEMETRY", raising=False)
    monkeypatch.delenv("AGOS_TELEMETRY_TENANT_KEY", raising=False)
    telemetry.reset_for_tests()
    yield
    telemetry.reset_for_tests()


@pytest.fixture
def all_logs():
    """Every log record of every logger, formatted (message + args)."""
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(f"{record.name} {record.levelname} {record.getMessage()}")
    h = H(level=logging.DEBUG)
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    yield records
    root.removeHandler(h)
    root.setLevel(old)


@pytest.fixture
def auth(monkeypatch):
    key = "r159-tenant-api-key-" + hashlib.sha256(b"r159").hexdigest()[:16]
    creds = {hashlib.sha256(key.encode()).hexdigest(): Principal(TENANT, "user")}
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    monkeypatch.setattr(server, "ENABLE_AUTH", True)
    return {"X-API-Key": key}


class _FakeOpenRouter(BaseHTTPRequestHandler):
    mode = "ok"            # ok | http500 | cut
    route_to = "test_runner"
    seen_auth: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.seen_auth.append(self.headers.get("Authorization"))
        if self.mode == "cut":
            self.connection.close()
            return
        if self.mode == "http500":
            data = json.dumps({"error": BACKEND_SECRET}).encode()
            self.send_response(500)
        else:
            user = body.get("messages", [{}])[-1].get("content", "")
            content = ("self_developer" if "self" in user.lower() else self.route_to) if body.get("max_tokens") == 20 \
                else ANSWER_SECRET
            data = json.dumps({"model": "fake/r159-model", "choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_llm(monkeypatch):
    _FakeOpenRouter.mode, _FakeOpenRouter.seen_auth = "ok", []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenRouter)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(llm_mod, "OPENROUTER_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat")
    monkeypatch.setenv("OPENROUTER_API_KEY", LLM_KEY)
    monkeypatch.setattr(llm_mod, "_client", None)
    yield _FakeOpenRouter
    httpd.shutdown()
    monkeypatch.setattr(llm_mod, "_client", None)


class _FakePostgREST(BaseHTTPRequestHandler):
    reject: int = 0
    calls: list = []
    rows: dict = {"tasks": [], "events": [], "agent_stats": []}

    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body_json = json.loads(self.rfile.read(n)) if n else None
        self.calls.append((self.command, self.path.split("?")[0]))
        table = self.path.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        # R-188: the durable queue (now the default) drives the background
        # task through the store — the fake must actually KEEP rows, apply
        # PATCHes and answer Prefer: return=representation, or the dispatcher
        # never sees a claimable task and the run never happens.
        if self.reject:
            code, body = self.reject, {"message": BACKEND_SECRET}
        elif self.command == "POST":
            self.rows.setdefault(table, []).append(body_json)
            code, body = 201, []
        elif self.command == "PATCH":
            matched = []
            for r in self.rows.get(table, []):
                hit = all(
                    (str(r.get(k)) in v[0][4:-1].split(",") if v[0].startswith("in.(")
                     else str(r.get(k)) == v[0].removeprefix("eq."))
                    for k, v in q.items() if k not in ("select",))
                if hit:
                    r.update(body_json)
                    matched.append(r)
            if "return=representation" in (self.headers.get("Prefer") or ""):
                code, body = 200, matched
            else:
                code, body = 204, None
        else:  # GET
            out = list(self.rows.get(table, []))
            for col, vals in q.items():
                if col in ("order", "limit", "select"):
                    continue
                want = vals[0].removeprefix("eq.")
                out = [r for r in out if r.get(col) is not None and str(r.get(col)) == want]
            if "limit" in q:
                out = out[: int(q["limit"][0])]
            code, body = 200, out
        # a 204 carries no body (a body on 204 made aiohttp fail intermittently)
        data = b"" if code == 204 or body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_POST = do_PATCH = do_GET = _answer


@pytest.fixture
def fake_storage(monkeypatch):
    _FakePostgREST.reject, _FakePostgREST.calls = 0, []
    _FakePostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakePostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", STORAGE_KEY)
    monkeypatch.setattr(ss, "SUPABASE_KEY_SOURCE", "SUPABASE_SERVICE_KEY")
    monkeypatch.setattr(ss, "_storage", None)
    yield _FakePostgREST
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


def _records(trace_id=None):
    recs = telemetry.recent()
    return [r for r in recs if trace_id is None or r["trace_id"] == trace_id]


def _assert_no_secrets(texts, extra=()):
    blob = "\n".join(texts)
    for m in (PROMPT_SECRET, ANSWER_SECRET, BACKEND_SECRET, LLM_KEY, STORAGE_KEY, TENANT, *extra):
        assert m not in blob, f"secret marker {m!r} leaked"


def _seed_failed_goal(goal: str):
    """Precondition for the self_developer retest path: one remembered failure
    of this tenant (what an earlier failed workflow stage stores)."""
    tok = tenancy.set_tenant(TENANT)
    try:
        asyncio.run(experience.memory().store(
            content="test_runner: seeded failure", memory_type=experience.MemoryType.EPISODIC,
            tags={"spec:test_runner", "failure", f"tenant:{TENANT}"},
            metadata={"layer": "experience", "tenant_id": TENANT, "specialization": "test_runner",
                      "ok": False, "goal": goal, "prompt": goal, "answer": "", "words": []},
            importance=0.9))
    finally:
        tenancy.reset_tenant(tok)


# ===================================================================== unit ==
def test_record_has_only_fixed_and_allowlisted_fields():
    with telemetry.span("tool", "analyst", tool="analyst", prompt=PROMPT_SECRET, api_key=LLM_KEY):
        pass
    (r,) = _records()
    assert set(r) == set(telemetry.BASE_FIELDS) | {"tool"}
    assert r["schema"] == telemetry.SCHEMA and r["kind"] == "tool" and r["outcome"] == "ok"
    assert len(r["trace_id"]) == 32 and len(r["span_id"]) == 16 and r["parent_span_id"] is None
    _assert_no_secrets([json.dumps(r)])


@pytest.mark.parametrize("value", [
    PROMPT_SECRET + " with spaces", "Bearer " + LLM_KEY, "https://user:pw@host/x?key=1",
    'a"b', "x" * 81, "line\nbreak", "حروف عربية", {"nested": PROMPT_SECRET}, [PROMPT_SECRET]])
def test_unsafe_values_are_redacted(value):
    with telemetry.span("llm", "openrouter", model=value):
        pass
    (r,) = _records()
    assert r["model"] == telemetry.REDACTED


def test_safe_values_pass_unchanged():
    with telemetry.span("http", "/api/tasks/{task_id}", route="/api/tasks/{task_id}", method="GET", status=404):
        pass
    (r,) = _records()
    assert (r["route"], r["method"], r["status"]) == ("/api/tasks/{task_id}", "GET", 404)


def test_tenant_is_a_keyed_pseudonym_never_the_raw_id(monkeypatch):
    tok = tenancy.set_tenant(TENANT)
    try:
        with telemetry.span("memory", "recall"):
            pass
        a = telemetry.tenant_ref()
        monkeypatch.setenv("AGOS_TELEMETRY_TENANT_KEY", "k1")
        k1 = telemetry.tenant_ref()
        assert k1 == telemetry.tenant_ref()                                     # stable with a key
        monkeypatch.setenv("AGOS_TELEMETRY_TENANT_KEY", "k2")
        assert telemetry.tenant_ref() != k1                                     # keyed
    finally:
        tenancy.reset_tenant(tok)
    (r,) = _records()
    assert r["tenant"] == a and a.startswith("t_") and len(a) == 14 and TENANT not in json.dumps(r)
    assert telemetry.tenant_ref("other") != a
    assert telemetry.tenant_ref(None, _lookup=False) == "none"
    assert telemetry.tenant_ref(tenancy.ALL_TENANTS) == "admin"


def test_exception_is_reported_by_type_only():
    with pytest.raises(RuntimeError):
        with telemetry.span("tool", "x"):
            raise RuntimeError(BACKEND_SECRET)
    (r,) = _records()
    assert (r["outcome"], r["error_type"]) == ("error", "RuntimeError")
    _assert_no_secrets([json.dumps(r)])


def test_cancellation_is_recorded_and_context_restored():
    async def main():
        started = asyncio.Event()

        async def work():
            with telemetry.span("llm", "openrouter"):
                started.set()
                await asyncio.sleep(10)
        t = asyncio.create_task(work())
        await started.wait()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return telemetry.current(), telemetry.current_span()
    ctx, sp = asyncio.run(main())
    (r,) = _records()
    assert (r["outcome"], r["error_type"]) == ("cancelled", "CancelledError")
    assert ctx is None and sp is None


def test_nested_spans_link_parent_and_share_trace():
    with telemetry.span("agent", "analyst") as a:
        with telemetry.span("tool", "analyst") as t:
            with telemetry.span("llm", "openrouter"):
                pass
    llm, tool, agent = _records()
    assert len({llm["trace_id"], tool["trace_id"], agent["trace_id"]}) == 1
    assert (llm["parent_span_id"], tool["parent_span_id"], agent["parent_span_id"]) == (t.span_id, a.span_id, None)
    assert telemetry.current() is None                     # detached root trace was reset


def test_bind_task_in_a_child_task_does_not_touch_the_parent():
    async def main():
        tok = telemetry.start_trace()
        parent = telemetry.current()

        async def child():
            telemetry.bind_task("childtask01")
            return telemetry.current()
        c = await asyncio.create_task(child())
        after = telemetry.current()
        telemetry.end_trace(tok)
        return parent, c, after
    parent, c, after = asyncio.run(main())
    assert c.trace_id == parent.trace_id and c.task_id == "childtask01"
    assert after is parent and after.task_id is None


def test_disabled_emits_nothing(monkeypatch):
    monkeypatch.setenv("AGOS_TELEMETRY", "off")
    before = get_metrics().export_prometheus().count("agos_ops_total{")
    with telemetry.span("tool", "x"):
        pass
    telemetry.event("task", "queued")
    assert _records() == [] and get_metrics().export_prometheus().count("agos_ops_total{") == before


def test_bounded_op_allowlists_and_cap():
    assert telemetry.bounded_op("storage", "insert_task") == "insert_task"
    assert telemetry.bounded_op("storage", "drop_table_x") == "other"
    assert telemetry.bounded_op("memory", PROMPT_SECRET) == "other"
    assert telemetry.bounded_op("agent", "has space") == "other"
    ops = {telemetry.bounded_op("tool", f"op{i}") for i in range(telemetry.OP_CAP * 3)}
    assert len(ops) == telemetry.OP_CAP + 1 and "other" in ops


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        telemetry.span("prompt", "x")


# ======================================================= metrics cardinality ==
def _ops_series():
    m = get_metrics()
    keys = [k for k in list(m._counters) + list(m._hist_agg) if k.startswith(("agos_ops_total", "agos_op_duration"))]
    return set(keys), [m._series_labels[k] for k in keys]


def test_series_do_not_grow_with_task_or_trace_ids():
    def run(n):
        for i in range(n):
            tok = telemetry.start_trace(task_id=f"task{i:06d}")
            try:
                with telemetry.span("task", "run"):
                    with telemetry.span("agent", "analyst"):
                        with telemetry.span("storage", "update_task"):
                            pass
            finally:
                telemetry.end_trace(tok)
    run(10)
    s10, _ = _ops_series()
    run(500)
    s510, labels = _ops_series()
    assert s510 == s10                                       # constant, not linear in ids
    # R-165: the length heuristic (len >= 32 == "an id") misfired on a real
    # route template (`/civilization/runs/{run_id}/resume`, 34 chars). The
    # check is now exact and stricter: no value is id-shaped, and every http
    # op is a route *template* (literal segments + `{param}` placeholders —
    # never a concrete id) or "<unmatched>". The set of registered routes is
    # not used: `/{full_path}` exists only when web/dist was built at import.
    import re
    template = re.compile(r"^(/([A-Za-z0-9_.-]+|\{[a-z_]+\}))+/?$|^/$|^<unmatched>$")
    for lab in labels:
        assert set(lab) <= {"kind", "op", "outcome"}
        for v in lab.values():
            assert not v.startswith("task0"), lab
            assert not re.search(r"[0-9a-f]{12,}", v), lab            # task/trace/run ids
        if lab.get("kind") == "http":
            assert template.match(lab["op"]), lab
            assert not re.search(r"/\d+(/|$)", lab["op"]), lab           # numeric ids
        else:
            assert len(lab.get("op", "")) < 32, lab


# =============================================== integration (local, mocks) ==
def test_chain_api_llm_agent_tool_memory_workflow_storage_one_trace(auth, fake_llm, fake_storage, all_logs):
    """mock/local integration: real app + real handlers + real experience layer;
    LLM = local fake OpenRouter, storage = local fake PostgREST (loopback)."""
    _seed_failed_goal("run unit tests")
    with TestClient(server.app) as c:
        r = c.post("/api/chat", json={"message": f"self check please {PROMPT_SECRET}"}, headers=auth)
    assert r.status_code == 200 and r.json()["routed_to"] == "self_developer"
    task_id = r.json()["task_id"]
    http = [x for x in _records() if x["kind"] == "http" and x["route"] == "/api/chat"]
    assert len(http) == 1
    trace = http[0]["trace_id"]
    recs = _records(trace)
    kinds = {x["kind"] for x in recs}
    assert {"http", "llm", "agent", "memory", "tool", "workflow", "storage", "task"} <= kinds
    # one tree rooted at the http span
    ids = {x["span_id"]: x for x in recs}
    for x in recs:
        if x is not http[0]:
            assert x["parent_span_id"] in ids, x
    # workflow stage -> agent -> tool chain inside the self_developer tool span
    wf = [x for x in recs if x["kind"] == "workflow"]
    assert wf and all(ids[w["parent_span_id"]]["kind"] == "tool" for w in wf)
    stage_agents = [x for x in recs if x["kind"] == "agent" and ids[x["parent_span_id"]]["kind"] == "workflow"]
    assert stage_agents
    assert any(x["kind"] == "tool" and ids[x["parent_span_id"]] in stage_agents for x in recs)
    llm = [x for x in recs if x["kind"] == "llm"]
    assert llm[0]["provider"] == "openrouter" and llm[0]["outcome"] == "ok" and llm[0]["model"] == "fake/r159-model"
    st = [x for x in recs if x["kind"] == "storage"]
    assert {x["op"] for x in st} >= {"insert_task", "update_task", "insert_event"}
    assert all(x["task_id"] == task_id and x["outcome"] == "ok" for x in st)
    assert all(x["tenant"].startswith("t_") for x in recs if x["kind"] != "http" or x["tenant"] != "none")
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_background_task_trace_links_request_task_and_storage(auth, fake_storage, all_logs):
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": f"numbers 1 2 3 {PROMPT_SECRET}"},
                   headers=auth)
        assert r.status_code == 202
        task_id = r.json()["task_id"]
        deadline = time.time() + 10
        # R-188: scope to THIS task — the durable queue (now the default) runs
        # background tasks in other tests too and their run spans stay in the
        # bounded telemetry window.
        while time.time() < deadline and not any(
                x["kind"] == "task" and x["op"] == "run" and x.get("task_id") == task_id
                for x in _records()):
            time.sleep(0.05)
    run = [x for x in _records() if x["kind"] == "task" and x["op"] == "run"
           and x.get("task_id") == task_id]
    assert len(run) == 1 and run[0]["task_id"] == task_id and run[0]["outcome"] == "ok"
    trace = run[0]["trace_id"]
    recs = _records(trace)
    # R-188: the accept request's trace_id travels in the row payload
    # (reserved _agos_trace_id key) and the dispatcher ADOPTS it before
    # running — the request and the run are ONE trace, preserving R-159's
    # background-task guarantee through the queue boundary (the original
    # assertion, restored).
    assert {x["kind"] for x in recs} >= {"http", "task", "agent", "tool", "memory", "storage"}
    queued = [x for x in recs if x["kind"] == "task" and x["op"] == "queued"]
    assert len(queued) == 1 and queued[0]["task_id"] == task_id
    assert all(x["task_id"] == task_id for x in recs if x["kind"] in ("agent", "tool", "storage"))
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_durable_trace_id_accepts_only_known_internal_trace_ids():
    """R-159 no-leak (advisor rounds 4+5): a value under the reserved
    ``_agos_trace_id`` key is accepted ONLY from the KNOWN INTERNAL ENVELOPE
    that ``_durable_payload`` writes (the reserved ``_agos_prompt`` key is
    always present in it) AND in the internal trace-id format
    (telemetry.new_trace_id = token_hex(16) → 32 lowercase hex chars).
    A raw dict whose value merely MATCHES the format — or a sensitive
    experimental value in an internal-looking envelope — is ignored and the
    run starts its own trace: payload data must never become an emitted
    trace_id."""
    good = secrets.token_hex(16)
    # the internal envelope (as _durable_payload writes it) + valid id
    assert server._durable_trace_id({"payload": {"_agos_prompt": "p", "data": None,
                                                 "_agos_trace_id": good}}) == good
    # a RAW dict whose value merely MATCHES the internal format — rejected:
    # the envelope is unknown (foreign/legacy payload)
    assert server._durable_trace_id({"payload": {"_agos_trace_id": "a" * 32}}) is None
    # internal envelope + a sensitive/foreign value — rejected
    assert server._durable_trace_id(
        {"payload": {"_agos_prompt": "p", "_agos_trace_id": "SECRET-VALUE-do-not-emit"}}) is None
    assert server._durable_trace_id(
        {"payload": {"_agos_prompt": "p", "_agos_trace_id": "not hex!"}}) is None
    # wrong case / shape / length / type
    assert server._durable_trace_id(
        {"payload": {"_agos_prompt": "p", "_agos_trace_id": "ABCDEF0123456789ABCDEF0123456789"}}) is None
    assert server._durable_trace_id(
        {"payload": {"_agos_prompt": "p", "_agos_trace_id": "abc"}}) is None
    assert server._durable_trace_id(
        {"payload": {"_agos_prompt": "p", "_agos_trace_id": 12345}}) is None
    # legacy raw-data payloads / no payload — no trace to adopt
    assert server._durable_trace_id({"payload": "raw legacy data"}) is None
    assert server._durable_trace_id({"payload": {"other": True}}) is None
    assert server._durable_trace_id({}) is None


def test_foreign_payload_trace_value_never_becomes_an_emitted_trace(auth, fake_storage):
    """R-159 no-leak through the RUN path (advisor round 5): a row whose
    payload carries a foreign value under the reserved key — even one that
    MATCHES the internal format — is never adopted. The run starts a FRESH
    trace: not the payload value, and not an inherited ambient trace (the
    queue loops can be started from a REQUEST context via the self-healing
    start — a leaked ambient trace would silently join unrelated requests to
    this run). Verified via the emitted records, not just the extractor."""
    foreign = "a" * 32  # matches the internal FORMAT — never written by us
    with TestClient(server.app) as c:
        # a normal accept starts the queue loops (self-healing) from the
        # REQUEST context — the dispatcher loop inherits that request's trace
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "numbers 1 2 3"}, headers=auth)
        assert r.status_code == 202, r.text
        # a queued row whose payload carries the foreign value (no internal
        # envelope — nothing a foreign writer could forge by format alone)
        fake_storage.rows["tasks"].append({
            "task_id": "foreign_trace_row", "specialization": "analyst",
            "prompt": "numbers 4 5 6", "status": "queued",
            "tenant_id": "any_tenant", "payload": {"_agos_trace_id": foreign},
        })
        deadline = time.time() + 20
        while time.time() < deadline and not any(
            x["kind"] == "task" and x["op"] == "run" and x.get("task_id") == "foreign_trace_row"
            for x in telemetry.recent()):
            time.sleep(0.05)
    recs = telemetry.recent()
    # the foreign value NEVER appears as an emitted trace_id
    assert all(x["trace_id"] != foreign for x in recs)
    run = [x for x in recs if x["kind"] == "task" and x["op"] == "run"
           and x.get("task_id") == "foreign_trace_row"]
    assert run, "the dispatcher must run the foreign row"
    # the run's trace is FRESH: valid internal format, not the payload value,
    # and NOT the trace of any http request (nothing inherited from the
    # dispatcher's ambient context)
    http_traces = {x["trace_id"] for x in recs if x["kind"] == "http"}
    assert re.fullmatch(r"[0-9a-f]{32}", run[0]["trace_id"])
    assert run[0]["trace_id"] != foreign
    assert run[0]["trace_id"] not in http_traces


def test_idempotent_replay_is_attributed_to_the_original_task(auth, fake_storage):
    """R-159 (advisor round 4): the idempotent replay is attributed to the
    EXISTING task — the newly generated id is discarded and never bound to
    the trace. The replay request's http span (recorded at request end, in
    the bound context) carries the ORIGINAL task id, and no telemetry record
    carries any other task id."""
    with TestClient(server.app) as c:
        r1 = c.post("/api/tasks",
                    json={"specialization": "analyst", "prompt": "numbers 1 2 3",
                          "idempotency_key": "replay-attr-1"}, headers=auth)
        assert r1.status_code == 202, r1.text
        tid = r1.json()["task_id"]
        r2 = c.post("/api/tasks",
                    json={"specialization": "analyst", "prompt": "numbers 1 2 3",
                          "idempotency_key": "replay-attr-1"}, headers=auth)
        assert r2.status_code == 202, r2.text
        body = r2.json()
        assert body["task_id"] == tid and body["duplicate"] is True
    task_recs = [r for r in _records() if r.get("task_id")]
    assert task_recs, "the accepts' spans must be attributed to the task"
    # the discarded new id never appears — every attributed record is the
    # ORIGINAL task's
    assert all(r["task_id"] == tid for r in task_recs)
    # the replay request's http span (the LAST http span) carries the
    # original task id — not the id generated for the replay attempt
    https = [r for r in _records() if r["kind"] == "http"]
    assert https and https[-1]["task_id"] == tid


def test_provider_error_is_diagnosable_without_leaking(auth, fake_llm, all_logs):
    fake_llm.mode = "http500"
    with TestClient(server.app) as c:
        r = c.post("/api/chat", json={"message": f"run unit tests {PROMPT_SECRET}"}, headers=auth)
    assert r.status_code == 200                               # routing falls back to keywords
    (llm,) = [x for x in _records() if x["kind"] == "llm"]
    assert (llm["outcome"], llm["http_status"], llm["reason"]) == ("fail", 500, "provider_error")
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_provider_transport_error_is_typed(auth, fake_llm, all_logs):
    fake_llm.mode = "cut"
    with TestClient(server.app) as c:
        c.post("/api/chat", json={"message": "run unit tests"}, headers=auth)
    (llm,) = [x for x in _records() if x["kind"] == "llm"]
    assert llm["outcome"] == "error" and llm["reason"] == "exception" and llm["error_type"]
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_storage_rejection_is_diagnosable_without_leaking(auth, fake_storage, all_logs):
    fake_storage.reject = 401
    with TestClient(server.app) as c:
        r = c.post("/api/chat", json={"message": f"run unit tests {PROMPT_SECRET}"}, headers=auth)
    assert r.status_code == 200
    st = [x for x in _records() if x["kind"] == "storage"]
    assert st and all((x["outcome"], x["http_status"]) == ("fail", 401) for x in st)
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_tool_exception_is_typed_and_not_leaked(auth, monkeypatch, all_logs):
    from agent_civilization.core.agents import real_dispatcher as rd

    async def boom(work):
        raise RuntimeError(BACKEND_SECRET)
    monkeypatch.setitem(rd._HANDLERS, "test_runner", boom)
    with TestClient(server.app) as c:
        c.post("/api/chat", json={"message": "run unit tests"}, headers=auth)
    (tool,) = [x for x in _records() if x["kind"] == "tool"]
    assert (tool["outcome"], tool["error_type"], tool["op"]) == ("error", "RuntimeError", "test_runner")
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_memory_layer_failure_logs_type_only(auth, monkeypatch, all_logs):
    async def broken_recall(*a, **k):
        raise OSError(f"/srv/secret/{PROMPT_SECRET}.json")
    monkeypatch.setattr(experience, "recall", broken_recall)
    with TestClient(server.app) as c:
        r = c.post("/api/chat", json={"message": "run unit tests"}, headers=auth)
    assert r.status_code == 200
    (mem,) = [x for x in _records() if x["kind"] == "memory"]
    assert (mem["op"], mem["outcome"], mem["error_type"]) == ("recall", "error", "OSError")
    assert any("experience recall failed: OSError" in line for line in all_logs)
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()] + all_logs)


def test_workflow_endpoint_traces_each_stage(auth):
    with TestClient(server.app) as c:
        r = c.post("/civilization/execute", json={"message": f"analyze numbers 4 8 15 {PROMPT_SECRET}"},
                   headers=auth)
    assert r.status_code == 200, r.text
    (http,) = [x for x in _records() if x["kind"] == "http"]
    recs = _records(http["trace_id"])
    wf = sorted((x for x in recs if x["kind"] == "workflow"), key=lambda x: x["stage_index"])
    assert len(wf) == len(r.json()["stages"]) and [w["stage_index"] for w in wf] == list(range(len(wf)))
    assert all(w["parent_span_id"] == http["span_id"] and w["stage_count"] == len(wf) for w in wf)
    _assert_no_secrets([json.dumps(x) for x in telemetry.recent()])


def test_consecutive_requests_never_share_or_inherit_a_trace(auth):
    with TestClient(server.app, raise_server_exceptions=False) as c:
        c.post("/api/chat", json={"message": "run unit tests"}, headers=auth)
        c.get("/api/nonexistent-r159")
        c.post("/api/chat", json={"message": "run unit tests"}, headers=auth)
    http = [x for x in _records() if x["kind"] == "http"]
    assert len(http) == 3 and len({x["trace_id"] for x in http}) == 3
    assert all(x["parent_span_id"] is None for x in http)
    # the SPA catch-all `/{full_path:path}` exists only when web/dist is built
    # (log 106): then the label is its template, otherwise "<unmatched>" —
    # never the raw path either way.
    catch_all = any(getattr(rt, "path", "") == "/{full_path:path}" for rt in server.app.routes)
    assert http[1]["route"] == ("/{full_path}" if catch_all else "<unmatched>")
    assert "nonexistent" not in json.dumps(http[1])
    for h in http:                                   # every span of a trace hangs under its own http root
        for x in _records(h["trace_id"]):
            assert x is h or x["parent_span_id"] is not None


def test_failed_request_then_next_request_is_clean():
    async def run():
        async def failing_app(scope, receive, send):
            with telemetry.span("tool", "x"):
                raise RuntimeError(BACKEND_SECRET)

        async def ok_app(scope, receive, send):
            seen.append((telemetry.current(), telemetry.current_span().kind))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def recv():
            return {"type": "http.request"}

        async def snd(m):
            pass
        scope = {"type": "http", "path": "/x", "method": "GET"}
        with pytest.raises(RuntimeError):
            await telemetry.TelemetryMiddleware(failing_app)(dict(scope), recv, snd)
        after_fail = (telemetry.current(), telemetry.current_span())
        await telemetry.TelemetryMiddleware(ok_app)(dict(scope), recv, snd)
        return after_fail
    seen = []
    after_fail = asyncio.run(run())
    assert after_fail == (None, None)
    tool, http1, http2 = _records()
    assert http1["outcome"] == "error" and tool["trace_id"] == http1["trace_id"] != http2["trace_id"]
    assert seen[0][0].trace_id == http2["trace_id"] and seen[0][1] == "http"


def test_metrics_endpoint_has_bounded_ops_labels_only(auth):
    assert telemetry.current() is None
    with TestClient(server.app) as c:
        ids = [c.post("/api/chat", json={"message": "run unit tests"}, headers=auth).json()["task_id"]
               for _ in range(5)]
        body = c.get("/metrics").text
    lines = [ln for ln in body.splitlines() if ln.startswith(("agos_ops_total{", "agos_op_duration_seconds"))]
    assert lines
    traces = {x["trace_id"] for x in _records()}
    for ln in lines:
        assert not any(i in ln for i in ids) and not any(t in ln for t in traces)
        assert TENANT not in ln and not re.search(r"t_[0-9a-f]{12}", ln)


def test_run_task_binds_its_own_task_id_whatever_the_caller(tenant_context):
    """_run_task is bound to task_id by itself (not only by inheriting the
    creating request's context) — caught mutant M13 in log 101."""
    tid = "r159runtask01"
    server.TASKS[tid] = {"task_id": tid, "tenant_id": tenant_context, "status": "queued",
                         "specialization": "analyst", "prompt": "numbers 1 2 3", "created_at": time.time()}
    try:
        asyncio.run(server._run_task(tid, "analyst", "numbers 1 2 3", None))
    finally:
        server.TASKS.pop(tid, None)
    (run,) = [x for x in _records() if x["kind"] == "task" and x["op"] == "run"]
    assert run["task_id"] == tid and run["outcome"] == "ok"
    assert all(x["task_id"] == tid for x in _records(run["trace_id"]))
