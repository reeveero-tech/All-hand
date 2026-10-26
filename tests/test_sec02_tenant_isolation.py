"""SEC-02 (R-138, D-056): authentication + tenant isolation of tenant data.

Before SEC-02 GET /api/tasks, /api/tasks/{id}, /api/memory, /api/learning,
/api/events and the SSE stream were open to anyone; memory recall returned
other clients' prompts/answers inside a task outcome; self_developer retested
everyone's remembered failures; DELETE /api/memory wiped every client's memory.

These tests use real HTTP calls through the FastAPI app (TestClient), real
agents and real memory, and — for the database path — a small in-process
PostgREST stand-in that applies ``tenant_id=eq.<x>`` filters exactly as
PostgREST does (NULL never matches). Keys are random per test; no real secret.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent_civilization.core import experience, tenancy
from agent_civilization.core.tenancy import ALL_TENANTS, CredentialsError


def _sha(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


@pytest.fixture
def keys():
    return {"A": "a-" + secrets.token_urlsafe(24), "B": "b-" + secrets.token_urlsafe(24),
            "ADM": "adm-" + secrets.token_urlsafe(24)}


def _tenant_env(keys):
    return json.dumps([
        {"tenant": "tenant_a", "key_sha256": _sha(keys["A"]), "role": "user"},
        {"tenant": "tenant_b", "key_sha256": _sha(keys["B"])},
        {"tenant": "ops", "key_sha256": _sha(keys["ADM"]), "role": "admin"},
    ])


def _reload(monkeypatch, **env):
    for k in ("AGOS_TENANT_KEYS", "AGOS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("AGOS_RATE_LIMIT_REQUESTS", "100000")
    import server
    return importlib.reload(server)


@pytest.fixture
def srv(monkeypatch, keys):
    s = _reload(monkeypatch, AGOS_TENANT_KEYS=_tenant_env(keys))
    yield s
    for k in ("AGOS_TENANT_KEYS", "AGOS_RATE_LIMIT_REQUESTS"):
        monkeypatch.delenv(k, raising=False)
    import server
    importlib.reload(server)


@pytest.fixture
def client(srv):
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c


def H(key):
    return {"X-API-Key": key}


def _wait(client, key, task_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}", headers=H(key))
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def _task(client, key, prompt, spec="analyst"):
    r = client.post("/api/tasks", json={"specialization": spec, "prompt": prompt}, headers=H(key))
    assert r.status_code == 202, r.text
    tid = r.json()["task_id"]
    return tid, _wait(client, key, tid)


# Every route that reads or changes tenant data (method, path, json body).
TENANT_ROUTES = [
    ("get", "/api/tasks", None),
    ("get", "/api/tasks/{tid}", None),
    ("get", "/api/memory", None),
    ("get", "/api/learning", None),
    ("delete", "/api/memory", None),
    ("get", "/api/events", None),
    ("get", "/api/events/stream", None),
    ("post", "/api/tasks", {"specialization": "analyst", "prompt": "1 2 3"}),
    ("post", "/api/chat", {"message": "summarise 1 2 3"}),
    ("post", "/civilization/execute", {"message": "summarise 1 2 3"}),
]


def _call(client, method, path, body, tid="x", **kw):
    path = path.format(tid=tid)
    if body is not None:
        kw["json"] = body
    return getattr(client, method)(path, **kw)


# ------------------------------------------------------------------ authentication

@pytest.mark.parametrize("method,path,body", TENANT_ROUTES)
def test_no_credentials_rejected_on_every_tenant_route(client, keys, method, path, body):
    tid, _ = _task(client, keys["A"], "secret-sec02-nocred 4 5 6")
    r = _call(client, method, path, body, tid=tid)
    assert r.status_code == 401, (path, r.status_code, r.text)
    assert r.json()["detail"] == "API key required"
    assert "secret-sec02-nocred" not in r.text


@pytest.mark.parametrize("bad", ["wrong", "", " ", "a-" + "x" * 32])
@pytest.mark.parametrize("method,path,body", TENANT_ROUTES)
def test_invalid_key_rejected_on_every_tenant_route(client, keys, method, path, body, bad):
    r = _call(client, method, path, body, headers=H(bad))
    assert r.status_code == 401, (path, bad, r.text)
    assert r.json()["detail"] in ("Invalid API key", "API key required")


def test_key_hash_as_key_is_rejected(client, keys):
    # the configured value is the SHA-256; presenting it must not authenticate
    assert client.get("/api/tasks", headers=H(_sha(keys["A"]))).status_code == 401


@pytest.mark.parametrize("method,path,body", TENANT_ROUTES)
def test_no_auth_configuration_means_no_access(monkeypatch, method, path, body):
    s = _reload(monkeypatch)  # neither AGOS_TENANT_KEYS nor AGOS_API_KEY
    try:
        assert s.CREDENTIALS == {}
        with TestClient(s.app, raise_server_exceptions=False) as c:
            for headers in ({}, H("anything"), {"X-Tenant-Id": "tenant_a"}):
                r = _call(c, method, path, body, headers=headers)
                assert r.status_code == 401, (path, headers, r.text)
                assert r.json()["detail"].startswith("authentication is not configured")
            assert s.TASKS == {}  # nothing was created anonymously
    finally:
        monkeypatch.delenv("AGOS_RATE_LIMIT_REQUESTS", raising=False)
        importlib.reload(s)


@pytest.mark.parametrize("value", [
    "not json", "{}", "[]", '[{"tenant": "a"}]',
    '[{"tenant": "a", "key_sha256": "abc"}]',
    '[{"tenant": "bad tenant!", "key_sha256": "' + "0" * 64 + '"}]',
    '[{"tenant": "a", "key_sha256": "' + "0" * 64 + '", "role": "root"}]',
    '[{"tenant": "a", "key_sha256": "' + "0" * 64 + '", "tenant_override": "b"}]',
    '[{"tenant": "a", "key_sha256": "' + "0" * 64 + '"}, {"tenant": "b", "key_sha256": "' + "0" * 64 + '"}]',
    '[{"tenant": "a", "key_sha256": "' + "A" * 64 + '"}]',
])
def test_malformed_auth_configuration_fails_startup(monkeypatch, value):
    with pytest.raises(CredentialsError):
        tenancy.load_credentials({"AGOS_TENANT_KEYS": value})
    monkeypatch.setenv("AGOS_TENANT_KEYS", value)
    import server
    with pytest.raises(CredentialsError):
        importlib.reload(server)
    monkeypatch.delenv("AGOS_TENANT_KEYS")
    importlib.reload(server)


def test_legacy_key_also_listed_as_tenant_key_fails_startup():
    with pytest.raises(CredentialsError):
        tenancy.load_credentials({"AGOS_API_KEY": "k1", "AGOS_TENANT_KEYS": json.dumps(
            [{"tenant": "a", "key_sha256": _sha("k1")}])})


def test_legacy_api_key_is_the_operator_admin():
    creds = tenancy.load_credentials({"AGOS_API_KEY": "op-key"})
    assert tenancy.authenticate("op-key", creds) == tenancy.Principal("operator", "admin")
    assert tenancy.authenticate("op-key2", creds) is None


def test_key_rotation_two_keys_one_tenant():
    creds = tenancy.load_credentials({"AGOS_TENANT_KEYS": json.dumps([
        {"tenant": "a", "key_sha256": _sha("old")}, {"tenant": "a", "key_sha256": _sha("new")}])})
    assert tenancy.authenticate("old", creds).tenant_id == tenancy.authenticate("new", creds).tenant_id == "a"


FORGED_HEADERS = [{"X-Tenant-Id": "tenant_a"}, {"X-User-Id": "tenant_a"}, {"X-Tenant": "tenant_a"},
                  {"Authorization": "Bearer tenant_a"}, {"X-Forwarded-User": "tenant_a"}]


@pytest.mark.parametrize("forged", FORGED_HEADERS)
def test_forged_identity_headers_and_params_do_not_authenticate(client, keys, forged):
    tid, _ = _task(client, keys["A"], "secret-sec02-forged 7 8 9")
    for path in ("/api/tasks", f"/api/tasks/{tid}", "/api/memory", "/api/events"):
        for q in ("", "?tenant_id=tenant_a", "?user_id=tenant_a"):
            r = client.get(path + q, headers=forged)
            assert r.status_code == 401, (path, q, forged)
            assert "secret-sec02-forged" not in r.text


@pytest.mark.parametrize("forged", FORGED_HEADERS)
def test_forged_identity_does_not_change_an_authenticated_tenant(client, keys, forged):
    tid_a, _ = _task(client, keys["A"], "secret-sec02-alpha 1 2")
    for q in ("", "?tenant_id=tenant_a", "?user_id=tenant_a", "?tenant=tenant_a"):
        r = client.get(f"/api/tasks{q}", headers={**H(keys["B"]), **forged})
        assert r.status_code == 200
        assert all(t["tenant_id"] == "tenant_b" for t in r.json()["tasks"])
        assert "secret-sec02-alpha" not in r.text
        r = client.get(f"/api/tasks/{tid_a}{q}", headers={**H(keys["B"]), **forged})
        assert r.status_code == 404
        r = client.get(f"/api/memory{q}", headers={**H(keys["B"]), **forged})
        assert "secret-sec02-alpha" not in r.text


# ------------------------------------------------------------------ task isolation

def test_each_tenant_lists_only_its_own_tasks(client, keys):
    a1, _ = _task(client, keys["A"], "secret-a-one 1 2 3")
    a2, _ = _task(client, keys["A"], "secret-a-two 4 5 6")
    b1, _ = _task(client, keys["B"], "secret-b-one 7 8 9")
    ra = client.get("/api/tasks?limit=50", headers=H(keys["A"])).json()
    rb = client.get("/api/tasks?limit=50", headers=H(keys["B"])).json()
    assert {t["id"] for t in ra["tasks"]} == {a1, a2}
    assert {t["id"] for t in rb["tasks"]} == {b1}
    assert "secret-b-one" not in json.dumps(ra) and "secret-a-" not in json.dumps(rb)
    # pagination (limit) never fills a page with another tenant's rows
    page = client.get("/api/tasks?limit=1", headers=H(keys["B"])).json()
    assert [t["id"] for t in page["tasks"]] == [b1]
    assert client.get("/api/tasks?limit=1", headers=H(keys["A"])).json()["tasks"][0]["id"] in {a1, a2}


def test_reading_another_tenants_task_by_id_is_indistinguishable_from_missing(client, keys):
    tid_b, _ = _task(client, keys["B"], "secret-sec02-bravo 3 4 5")
    r_other = client.get(f"/api/tasks/{tid_b}", headers=H(keys["A"]))
    r_missing = client.get("/api/tasks/000000000000", headers=H(keys["A"]))
    assert r_other.status_code == r_missing.status_code == 404
    assert r_other.json() == r_missing.json() == {"detail": "task not found"}
    assert "secret-sec02-bravo" not in r_other.text
    assert client.get(f"/api/tasks/{tid_b}", headers=H(keys["B"])).status_code == 200


def test_chat_and_civilization_tasks_are_owned_and_isolated(client, keys):
    r = client.post("/api/chat", json={"message": "secret-chat-a summarise 1 2 3"}, headers=H(keys["A"]))
    assert r.status_code == 200, r.text
    chat_tid = r.json()["task_id"]
    r = client.post("/civilization/execute", json={"message": "secret-civ-a summarise 1 2 3"},
                    headers=H(keys["A"]))
    assert r.status_code == 200, r.text
    civ_tid = r.json()["task_id"]
    for tid in (chat_tid, civ_tid):
        assert client.get(f"/api/tasks/{tid}", headers=H(keys["A"])).status_code == 200
        assert client.get(f"/api/tasks/{tid}", headers=H(keys["B"])).status_code == 404
    listed_b = client.get("/api/tasks", headers=H(keys["B"])).text
    assert "secret-chat-a" not in listed_b and "secret-civ-a" not in listed_b


def test_forged_ownership_on_create_is_ignored(client, keys, srv):
    r = client.post("/api/tasks", json={"specialization": "analyst", "prompt": "secret-forge 1 2",
                                        "tenant_id": "tenant_b", "owner": "tenant_b"},
                    headers={**H(keys["A"]), "X-Tenant-Id": "tenant_b"})
    assert r.status_code == 202
    tid = r.json()["task_id"]
    _wait(client, keys["A"], tid)
    assert srv.TASKS[tid]["tenant_id"] == "tenant_a"
    assert client.get(f"/api/tasks/{tid}", headers=H(keys["B"])).status_code == 404


def test_ownership_cannot_be_changed_through_the_api(client, keys, srv):
    tid, _ = _task(client, keys["A"], "secret-move 1 2")
    for method in ("put", "patch", "post"):
        r = getattr(client, method)(f"/api/tasks/{tid}", json={"tenant_id": "tenant_b"}, headers=H(keys["A"]))
        assert r.status_code in (404, 405), (method, r.status_code)
    assert srv.TASKS[tid]["tenant_id"] == "tenant_a"
    assert client.get(f"/api/tasks/{tid}", headers=H(keys["B"])).status_code == 404


def test_legacy_task_without_owner_is_not_readable(client, keys, srv):
    srv.TASKS["legacy000001"] = {"id": "legacy000001", "specialization": "analyst",
                                 "prompt": "secret-legacy", "status": "completed",
                                 "created_at": time.time(), "result": None}
    for k in ("A", "B", "ADM"):
        assert client.get("/api/tasks/legacy000001", headers=H(keys[k])).status_code == 404
        assert "secret-legacy" not in client.get("/api/tasks", headers=H(keys[k])).text
    assert client.get("/api/tasks/legacy000001").status_code == 401
    # only the admin, explicitly, sees quarantined records
    r = client.get("/api/tasks/legacy000001?all_tenants=true", headers=H(keys["ADM"]))
    assert r.status_code == 200 and r.json()["prompt"] == "secret-legacy"


def test_cross_tenant_scope_is_admin_only_and_explicit(client, keys):
    a, _ = _task(client, keys["A"], "secret-x-a 1 2")
    b, _ = _task(client, keys["B"], "secret-x-b 3 4")
    for path in ("/api/tasks", f"/api/tasks/{a}", "/api/memory", "/api/learning", "/api/events"):
        r = client.get(path + "?all_tenants=true", headers=H(keys["B"]))
        assert r.status_code == 403, path
        assert "secret-x-a" not in r.text
    # admin is scoped to its own tenant unless it asks
    assert client.get("/api/tasks", headers=H(keys["ADM"])).json()["tasks"] == []
    assert client.get(f"/api/tasks/{a}", headers=H(keys["ADM"])).status_code == 404
    ids = {t["id"] for t in client.get("/api/tasks?all_tenants=true", headers=H(keys["ADM"])).json()["tasks"]}
    assert {a, b} <= ids


def test_background_tasks_keep_their_tenant(client, keys, srv):
    """Interleaved tasks of A and B: every stored memory carries the owner's tenant."""
    tids = {}
    for i in range(6):
        who = "A" if i % 2 == 0 else "B"
        r = client.post("/api/tasks", json={"specialization": "analyst", "prompt": f"bg-{who}-{i} 1 2 3"},
                        headers=H(keys[who]))
        tids[r.json()["task_id"]] = who
    for tid, who in tids.items():
        _wait(client, keys[who], tid)
    entries = list(experience.memory()._memories.values())
    assert len(entries) == 6
    for e in entries:
        who = e.metadata["prompt"].split("-")[1]
        assert e.metadata["tenant_id"] == {"A": "tenant_a", "B": "tenant_b"}[who]


# ------------------------------------------------------------------ memory / learning isolation

def test_memory_recall_never_returns_another_tenants_task(client, keys):
    """The indirect leak: before SEC-02 B's task outcome carried A's prompt/answer
    in experience.memory.recalled."""
    _task(client, keys["A"], "secret quarterly revenue numbers 10 20 30")
    _, own = _task(client, keys["A"], "secret quarterly revenue numbers 10 20 30")
    assert own["result"]["experience"]["memory"]["recalled"], "A recalls its own task"
    _, other = _task(client, keys["B"], "secret quarterly revenue numbers 10 20 30")
    assert other["result"]["experience"]["memory"]["recalled"] == []
    assert other["result"]["experience"]["learning"]["prior"]["samples"] == 0


def test_memory_search_filters_and_stats_are_per_tenant(client, keys):
    _task(client, keys["A"], "secret-memory-alpha 1 2 3")
    _task(client, keys["B"], "secret-memory-bravo 4 5 6")
    ra = client.get("/api/memory?q=secret-memory-alpha", headers=H(keys["A"])).json()
    rb = client.get("/api/memory?q=secret-memory-alpha", headers=H(keys["B"])).json()
    assert any("secret-memory-alpha" in m["prompt"] for m in ra["memories"])
    assert all("secret-memory-alpha" not in m["prompt"] for m in rb["memories"])  # B may match its own
    for flt in ("", "?specialization=analyst", "?limit=100", "?q=secret"):
        body = client.get(f"/api/memory{flt}", headers=H(keys["B"])).text
        assert "secret-memory-alpha" not in body
    assert ra["stats"]["memories"] == 1 and rb["stats"]["memories"] == 1
    la = client.get("/api/learning", headers=H(keys["A"])).json()
    assert la["learning"]["tasks"] == 1 and la["tenant_id"] == "tenant_a"


def test_delete_memory_only_clears_the_callers_tenant(client, keys):
    _task(client, keys["A"], "secret-keep-a 1 2 3")
    _task(client, keys["B"], "secret-drop-b 4 5 6")
    r = client.delete("/api/memory", headers=H(keys["B"]))
    assert r.status_code == 200 and r.json() == {"cleared": 1}
    assert client.get("/api/memory", headers=H(keys["B"])).json()["count"] == 0
    assert client.get("/api/memory", headers=H(keys["A"])).json()["count"] == 1
    assert client.delete("/api/memory?all_tenants=true", headers=H(keys["B"])).status_code == 403
    assert client.get("/api/memory", headers=H(keys["A"])).json()["count"] == 1


def test_legacy_memory_without_tenant_is_quarantined(client, keys):
    async def plant():
        await experience._ensure_loaded()
        await experience.memory().store(
            content="legacy", memory_type=experience.MemoryType.EPISODIC, tags={"spec:analyst"},
            metadata={"layer": "experience", "specialization": "analyst", "prompt": "secret-old-memory 1 2 3",
                      "answer": "x", "ok": True, "input_kind": "numbers", "words": ["secret", "old", "memory"]})
    asyncio.run(plant())
    for k in ("A", "B", "ADM"):
        assert "secret-old-memory" not in client.get("/api/memory", headers=H(keys[k])).text
    _, out = _task(client, keys["A"], "secret old memory 1 2 3")
    assert out["result"]["experience"]["memory"]["recalled"] == []
    adm = client.get("/api/memory?all_tenants=true", headers=H(keys["ADM"])).json()
    assert adm["stats"]["quarantined_untenanted"] == 1
    assert any(m["tenant_id"] is None for m in adm["memories"])


def test_no_tenant_context_means_no_memory():
    async def go():
        async def call():
            return {"ok": True, "answer": "secret"}
        return await experience.run_with_experience("analyst", "x", {"prompt": "p 1 2"}, call)
    token = tenancy.set_tenant(None)
    try:
        result, exp = asyncio.run(go())
    finally:
        tenancy.reset_tenant(token)
    assert exp is None and experience.memory()._memories == {}
    with pytest.raises(ValueError):
        asyncio.run(experience.search("", scope=None))


def test_self_developer_retests_only_the_callers_failures(keys):
    from agent_civilization.core.agents import real_dispatcher as rd

    async def fail_as(tenant, goal):
        token = tenancy.set_tenant(tenant)
        try:
            async def call():
                return {"ok": False, "error": "boom"}
            await experience.run_with_experience("analyst", "t", {"prompt": goal, "goal": goal}, call)
        finally:
            tenancy.reset_tenant(token)

    async def selfdev_as(tenant):
        token = tenancy.set_tenant(tenant)
        try:
            return await rd.dispatch("self_developer", {"prompt": "self improve"})
        finally:
            tenancy.reset_tenant(token)

    asyncio.run(fail_as("tenant_a", "secret goal of tenant a 1 2 3"))
    out_b = asyncio.run(selfdev_as("tenant_b"))
    assert out_b["tool_output"]["retests"] == []
    assert "secret goal of tenant a" not in json.dumps(out_b)
    out_none = asyncio.run(selfdev_as(None))
    assert out_none["tool_output"]["retests"] == []
    out_a = asyncio.run(selfdev_as("tenant_a"))
    assert [r["goal"] for r in out_a["tool_output"]["retests"]] == ["secret goal of tenant a 1 2 3"]


# ------------------------------------------------------------------ events: stream + database

def _sse_request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/api/events/stream", "headers": [],
                    "query_string": b"", "client": ("127.0.0.1", 1)})


def test_event_stream_is_per_tenant(srv, keys):
    # SEC-04 (R-144): both streams are connected first; events are broadcast
    # to connected streams only (before R-144 they were queued in advance).
    async def go():
        resp_a = await srv.event_stream(_sse_request(), api_key=keys["A"])
        resp_b = await srv.event_stream(_sse_request(), api_key=keys["B"])
        await srv._publish("tenant_b", {"type": "secret-event-b"})
        await srv._publish("tenant_a", {"type": "event-a"})
        chunk_a = await asyncio.wait_for(resp_a.body_iterator.__anext__(), timeout=3)
        chunk_b = await asyncio.wait_for(resp_b.body_iterator.__anext__(), timeout=3)
        await resp_a.body_iterator.aclose()
        await resp_b.body_iterator.aclose()
        return chunk_a, chunk_b
    chunk_a, chunk_b = asyncio.run(go())
    assert "event-a" in chunk_a and "secret-event-b" not in chunk_a
    assert "secret-event-b" in chunk_b


def test_event_stream_requires_a_valid_key(srv):
    for key in (None, "", "wrong"):
        with pytest.raises(HTTPException) as e:
            asyncio.run(srv.event_stream(_sse_request(), api_key=key))
        assert e.value.status_code == 401


def test_task_events_reach_only_the_owners_stream(client, keys, srv):
    # SEC-04 (R-144): one subscription per tenant, registered on the bus the
    # stream route uses, before the task runs
    sub_a, sub_b = srv.EVENT_BUS.open("tenant_a", "t-a"), srv.EVENT_BUS.open("tenant_b", "t-b")
    try:
        tid, _ = _task(client, keys["A"], "secret-evt 1 2 3")
        qa, qb = sub_a.queue, sub_b.queue
        seen_a = [qa.get_nowait()["task_id"] for _ in range(qa.qsize())]
        assert seen_a and set(seen_a) == {tid}
        assert qb.qsize() == 0
    finally:
        srv.EVENT_BUS.close(sub_a)
        srv.EVENT_BUS.close(sub_b)


class _PostgREST(BaseHTTPRequestHandler):
    """Minimal PostgREST: POST rows, PATCH ?task_id=eq., GET with eq. filters,
    order=created_at.desc and limit. ``col=eq.v`` never matches NULL."""
    rows = {"tasks": [], "events": [], "agent_stats": []}
    seq = [0]

    def log_message(self, *a):
        pass

    def _table_q(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], urllib.parse.parse_qs(u.query)

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        table, _ = self._table_q()
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seq[0] += 1
        row.setdefault("created_at", f"2026-10-16T00:00:{self.seq[0]:02d}")
        row.setdefault("id", self.seq[0])
        self.rows[table].append(row)
        self._send(201)

    def do_PATCH(self):
        table, q = self._table_q()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        # R-188: the durable queue (now the default) claims via a conditional
        # PATCH with Prefer: return=representation — the fake must answer it
        # like PostgREST (200 with the updated rows) or no task ever runs.
        matched = []
        for r in self.rows[table]:
            hit = all(
                (str(r.get(k)) in v[0][4:-1].split(",") if v[0].startswith("in.(")
                 else str(r.get(k)) == v[0].removeprefix("eq."))
                for k, v in q.items() if k != "select")
            if hit:
                r.update({k: v for k, v in patch.items() if k != "tenant_id"})
                matched.append(r)
        if "return=representation" in (self.headers.get("Prefer") or ""):
            self._send(200, matched)
        else:
            self._send(204)

    def do_GET(self):
        table, q = self._table_q()
        out = list(self.rows[table])
        for col, vals in q.items():
            if col in ("order", "limit", "select"):
                continue
            want = vals[0].removeprefix("eq.")
            out = [r for r in out if r.get(col) is not None and str(r.get(col)) == want]
        out.sort(key=lambda r: r["created_at"], reverse=True)
        if "limit" in q:
            out = out[: int(q["limit"][0])]
        self._send(200, out)


@pytest.fixture
def db(monkeypatch, srv):
    _PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "sec02-test-not-a-real-key")
    monkeypatch.setattr(ss, "_storage", None)
    yield _PostgREST.rows
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


def test_database_tasks_and_events_are_scoped_to_the_tenant(client, keys, db):
    a, _ = _task(client, keys["A"], "secret-db-a 1 2 3")
    b, _ = _task(client, keys["B"], "secret-db-b 4 5 6")
    assert {r["task_id"]: r["tenant_id"] for r in db["tasks"]} == {a: "tenant_a", b: "tenant_b"}
    assert {r["tenant_id"] for r in db["events"]} == {"tenant_a", "tenant_b"}
    # a legacy row with no owner, as left by data stored before SEC-02
    db["tasks"].append({"task_id": "legacyrow", "prompt": "secret-db-legacy", "tenant_id": None,
                        "created_at": "2026-10-16T00:00:99"})
    db["events"].append({"event_type": "task_created", "event_data": {"task_id": "legacyrow"},
                         "tenant_id": None, "created_at": "2026-10-16T00:00:99"})
    ra = client.get("/api/tasks", headers=H(keys["A"])).json()
    assert ra["source"] == "database" and {t["task_id"] for t in ra["tasks"]} == {a}
    for limit in (1, 2, 50):
        rb = client.get(f"/api/tasks?limit={limit}", headers=H(keys["B"])).json()
        assert {t["task_id"] for t in rb["tasks"]} == {b}
    ea = client.get("/api/events", headers=H(keys["A"])).json()["events"]
    assert ea and {e["event_data"]["task_id"] for e in ea} == {a}
    assert "secret-db-legacy" not in client.get("/api/tasks", headers=H(keys["ADM"])).text
    adm = client.get("/api/tasks?all_tenants=true", headers=H(keys["ADM"])).json()
    assert {t["task_id"] for t in adm["tasks"]} == {a, b, "legacyrow"}
    # no-credential read of the database path
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/events").status_code == 401


def test_storage_layer_refuses_unscoped_reads(db):
    from agent_civilization.storage import supabase_store as ss

    async def go():
        st = ss.SupabaseStorage()
        await st.insert_task("t1", "analyst", "secret", tenant_id="tenant_a")
        await st.insert_event("e", {"task_id": "t1"}, tenant_id="tenant_a")
        out = (await st.get_tasks(), await st.get_events(), await st.get_tasks(scope="tenant_b"),
               await st.get_tasks(scope="tenant_a"), await st.get_tasks(scope=ALL_TENANTS))
        for bad in ("tenant_a&tenant_id=is.null", "", "a b"):
            with pytest.raises(ValueError):
                await st.get_tasks(scope=bad)
        await st.close()
        return out
    none_t, none_e, other, own, every = asyncio.run(go())
    assert none_t == [] and none_e == [] and other == []
    assert [t["task_id"] for t in own] == ["t1"] and [t["task_id"] for t in every] == ["t1"]


# ------------------------------------------------------------------ shared resources + regression

def test_external_agent_registration_needs_the_admin_role(client, keys):
    body = {"name": "sec02-ext", "url": "http://agent.example.org:9/x", "model": "", "api_key": ""}
    assert client.post("/api/agents/external", json=body).status_code == 401
    assert client.post("/api/agents/external", json=body, headers=H(keys["A"])).status_code == 403
    assert client.delete("/api/agents/external/sec02-ext", headers=H(keys["A"])).status_code == 403
    assert client.post("/api/agents/external", json=body, headers=H(keys["ADM"])).status_code == 201
    assert client.delete("/api/agents/external/sec02-ext", headers=H(keys["ADM"])).status_code == 200


def test_regression_unauthenticated_client_receives_no_private_data(client, keys):
    tid, _ = _task(client, keys["A"], "secret-regression-zulu 1 2 3")
    client.post("/api/chat", json={"message": "secret-regression-chat 1 2"}, headers=H(keys["A"]))
    for path in ("/api/tasks", f"/api/tasks/{tid}", "/api/memory", "/api/learning", "/api/events",
                 "/api/system/status", "/api/agents", "/api", "/health", "/metrics",
                 "/civilization/registry"):
        r = client.get(path)
        assert "secret-regression" not in r.text, path
    status = client.get("/api/system/status").json()
    assert set(status["experience"]) == {"enabled", "max_entries", "persistence_file", "scope"}
