"""R-190 — the tool registry and its gates verified through the REAL task
journey (integration of R-171/R-172: a single coordinated task invoking tools
from the ONE registry through the gate, with impact measurement).

Evidence class of THESE tests: REAL code paths, no mocks of the components
under test —
  * the real FastAPI app (server.py) via TestClient;
  * the real persistent tool registry on a per-test sqlite store (the conftest
    gives every test its own AGOS_TOOL_REGISTRY_DB and resets the runtime
    registry — the store survives within the test across instances);
  * the real durable task queue: dispatcher + reaper loops, and the storage
    layer's REAL wire protocol against a fake PostgREST on loopback (the same
    evidence class as the R-163 suite: the HTTP wire, fencing and conditional
    transitions are exercised for real; only the remote database is a fake).

The journey each test walks: tenant-authenticated POST /api/tasks → durable
row → dispatcher claim (fenced lease) → agent run → ``tool_runtime.dispatch``
through the registry gate → terminal state + events. What R-190 adds on top
of the unit suites (R-171/R-172/R-163): the SINGLE journey binding all of it
together, including the honest refusal paths (permission, retired tool) and
the recovery path (a dead worker's expired lease reaped and re-run), and
tenant data protection end-to-end.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

# the R-163 harness: fake PostgREST + app client + durable queue helpers.
# Imported, not duplicated — the class-level state is reset per test by the
# fixtures below (same reset the R-163 ``db`` fixture performs).
import test_r163_durable_queue as DQ  # noqa: E402


# Same fake, but HTTP/1.1 keep-alive: aiohttp reuses connections and the
# stdlib HTTP/1.0 default races it into broken pipes / ClientConnectionError
# under the app's real concurrency (dispatcher + reaper + events). The
# Content-Length is already exact, so 1.1 is safe.
class _PostgREST11(DQ._PostgREST):
    protocol_version = "HTTP/1.1"


@pytest.fixture
def db(monkeypatch):
    from http.server import ThreadingHTTPServer
    import threading
    DQ._PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    DQ._PostgREST.gets = []
    DQ._PostgREST.mode[0] = "ok"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PostgREST11)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    # R-190 harness note: the storage singleton is process-global and caches
    # an aiohttp session bound to the FIRST loop that used it — with one
    # TestClient app per test, that loop is closed by the next test, and a
    # session bound to it fails every call with "Event loop is closed"
    # (flaky, order-dependent). A fresh singleton per test pins the session
    # to THIS test's app loop; nothing real is faked by this.
    ss._storage = None
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "r190-test-not-a-real-key")
    # NOTE: like the R-163 suite, the fake server is NOT shut down at teardown —
    # the app's dispatcher/reaper loops keep polling between tests, and a dead
    # port here would trip the storage circuit breaker for the NEXT test.
    yield DQ._PostgREST.rows


@pytest.fixture
def tenants(tenant_auth, monkeypatch):
    """test_tenant (A, user), other_tenant (B, user) — like the R-163 pair."""
    import hashlib
    import secrets
    import server
    from agent_civilization.core.tenancy import Principal
    key_b = "r190-" + secrets.token_urlsafe(16)
    creds = dict(server.CREDENTIALS)
    creds[hashlib.sha256(key_b.encode()).hexdigest()] = Principal("other_tenant", "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    tenant_auth.headers_b = {"X-API-Key": key_b}
    return tenant_auth


def _stats():
    from agent_civilization.core import tool_runtime
    return tool_runtime.stats()


def _registry():
    from agent_civilization.core import tool_runtime
    return tool_runtime.runtime_registry()


def _submit(c, headers, specialization, prompt, **extra):
    return c.post("/api/tasks", json={"specialization": specialization,
                                     "prompt": prompt, **extra}, headers=headers)


def _wait_row(rows, task_id, status):
    return DQ._wait_for(lambda: any(
        x["task_id"] == task_id and x["status"] == status for x in rows["tasks"]))


# ------------------------------------------------- 1. the happy journey

def test_r190_journey_runs_a_registered_tool_through_the_gate(tenants, db, monkeypatch):
    """A tenant task walks the whole journey and its tool really runs THROUGH
    the registry gate (impact measured on the gate's own counters — not a mock
    on the agent path)."""
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    reg = _registry()
    rec = reg.get("agos.internal.analyst")
    skey = f"agos.internal.analyst@{rec.version}"
    before = _stats()["executed"].get(skey, 0)

    with DQ._client() as c:
        r = _submit(c, tenants.headers, "analyst", "R-190: summarise one two three")
        assert r.status_code == 202, r.text
        tid = r.json()["task_id"]
        assert _wait_row(db, tid, "completed"), db["tasks"]

        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["attempts"] == 1 and row["claim_token"]
        result = row["result"]
        assert isinstance(result, dict) and result.get("ok") is True, result

        # the tool ran through the GATE: the registry's own execution counter
        # moved for this task's tool (the impact measurement — R-190).
        after = _stats()["executed"].get(skey, 0)
        assert after == before + 1, (before, after)

        # the registry record that served this task is the real durable one
        # (the seeded analyst record, in an executable lifecycle state) — not
        # an ad-hoc handler outside the registry (the gate's own binding).
        from agent_civilization.core.tool_runtime import EXECUTABLE_LIFECYCLES
        assert rec.lifecycle_state in EXECUTABLE_LIFECYCLES
        assert rec.implementation_ref == "agent_civilization.core.agents.real_dispatcher:_analyst"

        # the completion event lands (row state and event are separate writes —
        # wait for the event, bounded), scoped to the tenant, with ok=True.
        assert DQ._wait_for(lambda: any(
            e.get("event_type") == "task_completed"
            and (e.get("event_data") or {}).get("ok") is True
            for e in db["events"] if e.get("tenant_id") == "test_tenant")), db["events"]


# --------------------------------------- 2. permission refusal is honest

def test_r190_journey_permission_refusal_fails_the_task_honestly(tenants, db, monkeypatch):
    """A tool that declares the approval policy (modifier 1.1.0) and a scope
    the request does not grant is REFUSED at the gate: the task ends ``failed``
    with the fixed refusal reason — never a fake success — and the refusal is
    counted on the gate's own counters."""
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    reg = _registry()
    rec = reg.get("agos.internal.modifier")
    skey = f"agos.internal.modifier@{rec.version}"
    before_exec = _stats()["executed"].get(skey, 0)

    with DQ._client() as c:
        r = _submit(c, tenants.headers, "modifier", "R-190: modify this code",
                    data={"prompt": "R-190: modify this code"})
        assert r.status_code == 202, r.text
        tid = r.json()["task_id"]
        # no approval + no scopes in the request → refused → terminal failed
        assert _wait_row(db, tid, "failed"), db["tasks"]

        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        result = row["result"]
        assert isinstance(result, dict) and result.get("ok") is False, result
        reason = str(result.get("error") or result)
        assert "refused" in reason, reason          # the fixed honest refusal
        # the gate refused — the tool never executed (no fake success)
        assert _stats()["executed"].get(skey, 0) == before_exec
        # ... and the refusal is visible on the gate's own counters
        assert _stats()["refused"].get(skey, 0) >= 1


# ------------------------------- 3. red control: retired tool, no run

def test_r190_journey_a_retired_tool_is_refused_not_faked(tenants, db, monkeypatch):
    """The negative control that binds this suite to the REAL registry: a task
    whose tool record was retired (disabled — the honest way a host retires a
    tool) is refused by the gate — the journey must fail the task honestly,
    never fall back to an ad-hoc handler outside the registry."""
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    reg = _registry()
    rec = reg.get("agos.internal.analyst")
    reg.update_status("agos.internal.analyst", rec.version, actor="r190-test",
                      lifecycle_state="disabled")

    with DQ._client() as c:
        r = _submit(c, tenants.headers, "analyst", "R-190: summarise one two three")
        assert r.status_code == 202, r.text
        tid = r.json()["task_id"]
        assert _wait_row(db, tid, "failed"), db["tasks"]
        result = next(x for x in db["tasks"]
                      if x["task_id"] == tid)["result"]
        assert result.get("ok") is False
        reason = str(result.get("error") or result)
        assert "not executable" in reason and "disabled" in reason, reason
        # the retired record was never executed through the gate
        assert _stats()["executed"].get("agos.internal.analyst", 0) == 0


# -------------------------------------------- 4. recovery after a crash

def test_r190_journey_recovers_a_task_from_a_dead_worker(tenants, db, monkeypatch):
    """Recovery, end-to-end: a task whose worker died mid-run (an expired
    lease — the R-163 fencing scenario) is reaped, requeued and RE-RUN by the
    dispatcher, and completes on the retry. The journey must recover without
    operator action and without losing or duplicating the task."""
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    # a row a dead worker left behind: claimed, one attempt spent, lease long
    # expired — exactly what the reaper exists for.
    db["tasks"].append(DQ._row("r190-dead1", "test_tenant", status="running",
                              attempts=1, claim_token="r190-dead-token",
                              lease_expires_at="2020-01-01T00:00:00+00:00"))

    with DQ._client() as c:
        # the reaper + dispatcher loops run inside the app's startup: the row
        # is recovered (expired lease → requeued → re-claimed) and the task
        # completes on the retry.
        assert DQ._wait_for(lambda: any(
            x["task_id"] == "r190-dead1" and x["status"] == "completed"
            for x in db["tasks"])), db["tasks"]
        row = next(x for x in db["tasks"] if x["task_id"] == "r190-dead1")
        assert row["attempts"] >= 2 and row["result"].get("ok") is True
        # the tool really ran through the gate for the recovered task
        reg = _registry()
        skey = f"agos.internal.analyst@{reg.get('agos.internal.analyst').version}"
        assert _stats()["executed"].get(skey, 0) >= 1


# ------------------------------------------- 5. tenant data protection

def test_r190_journey_tenant_data_is_fenced(tenants, db, monkeypatch):
    """Tenant protection, end-to-end on the journey: tenant B can neither see,
    nor cancel, nor receive tenant A's task or its result — and A's completed
    result never leaks into B's idempotency replay."""
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    with DQ._client() as c:
        r = _submit(c, tenants.headers, "analyst", "R-190: tenant A secret words",
                    idempotency_key="r190-key-A")
        assert r.status_code == 202, r.text
        tid = r.json()["task_id"]
        assert _wait_row(db, tid, "completed"), db["tasks"]

        # B cannot see the task (404, not 403 — no existence leak)
        assert c.get(f"/api/tasks/{tid}", headers=tenants.headers_b).status_code == 404
        # B cannot cancel it either (same fencing — POST /api/tasks/{id}/cancel)
        assert c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers_b).status_code == 404
        # B's list never contains A's task
        lst = c.get("/api/tasks", headers=tenants.headers_b).json()
        ids = {t.get("task_id") for t in
               (lst if isinstance(lst, list) else lst.get("tasks", []))}
        assert tid not in ids
        # B replaying A's idempotency key creates B's OWN task (no result leak)
        r2 = _submit(c, tenants.headers_b, "analyst", "R-190: B's own words",
                     idempotency_key="r190-key-A")
        assert r2.status_code == 202 and r2.json()["task_id"] != tid
        new = next(x for x in db["tasks"] if x["task_id"] == r2.json()["task_id"])
        assert new["tenant_id"] == "other_tenant"
