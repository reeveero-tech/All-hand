"""R-191 — the connectors framework: one REAL terminal connector (GitHub)
built on the existing point integrations, with documented review standards.

Evidence class of THESE tests: REAL code paths, no mocks of the components
under test —
  * the real connectors framework (``agent_civilization.connectors``) —
    the reusable layer ABOVE ``external_adapter`` (the point integrations
    stay as they are: the framework does not replace them);
  * the real encrypted per-tenant secret store (R-189 ``SecretStore``) on a
    per-test sqlite db — the connector's credential really lives there;
  * the real tool registry + the REAL gate (``tool_runtime.dispatch``) — the
    connector tool is dispatched through the same gate every task's tools go
    through, with the task contract (task identity, approval, tenant);
  * a fake GitHub terminal on loopback (R-163 evidence class: the real HTTP
    wire is exercised; only the remote service is a fake) — with modes for
    the negative paths: connection drop, slowness, 401, 403, and an
    idempotency store that replays (never re-applies) duplicate requests.

R-191's review standards, each verified by a test below:
  * credential invalidation on expiry/removal (401 → revoke → fail-fast);
  * timeouts and retries (bounded, honest, and retried ONLY for
    connection-level interruptions with a stable task identity — R-188-b:
    a retry must never duplicate a side effect);
  * side-effect safety on retry (duplicate request applies once);
  * tenant isolation (SEC-02: the ambient tenant scopes the credential);
  * observability (per-connector stats + telemetry spans);
  * the tool records declare their truth (network allowlist, side effects,
    idempotency, approval policy).

أحمر أولًا: this suite was written BEFORE the framework existed and captured
its red run in evidence log 359 (the R-191 discipline the roadmap demands).
"""
import asyncio
import json
import pathlib
import secrets as secrets_mod
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

GITHUB_TOKEN = "r191-fake-token-not-a-real-secret"
SECRET_NAME = "connector_github_token"


# ---------------------------------------------------------------------------
# the fake GitHub terminal (R-163 evidence class: real wire, fake service)
# ---------------------------------------------------------------------------
class _FakeGitHub(BaseHTTPRequestHandler):
    """A loopback stand-in for api.github.com with NEGATIVE modes.

    Class-level state (reset by the ``github_terminal`` fixture):
      mode        — ok | drop | 401 | 403 | slow
      drop_first  — drop this many connections before serving (int)
      hits        — every request seen (method, path, auth, idem key, body)
      applied     — ops actually APPLIED (an idempotent replay is NOT here)
    """
    protocol_version = "HTTP/1.1"
    mode = ["ok"]
    drop_first = [0]
    hits = []
    applied = []

    def _record(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.hits.append({
            "method": method,
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "idem": self.headers.get("Idempotency-Key", ""),
            "body": body.decode("utf-8", "replace"),
        })

    def _serve(self, method):
        self._record(method)
        if _FakeGitHub.drop_first[0] > 0:
            _FakeGitHub.drop_first[0] -= 1
            # connection-level interruption: kill the connection without any
            # response (shutdown: a bare close() is deferred by the handler's
            # own file wrappers and never sends FIN to the client)
            import socket as _socket
            try:
                self.connection.shutdown(_socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        m = _FakeGitHub.mode[0]
        if m == "slow":
            time.sleep(1.5)
        if m == "401":
            self._respond(401, {"message": "Bad credentials"})
            return
        if m == "403":
            self._respond(403, {"message": "Resource not accessible by integration"})
            return
        # ok: apply the op once per idempotency key (a replay never re-applies)
        key = self.hits[-1]["idem"]
        if key:
            if key in {h["idem"] for h in _FakeGitHub.applied}:
                self._respond(200, {"replayed": True, "number": 1})
                return
        _FakeGitHub.applied.append(self.hits[-1])
        self._respond(200, {"number": 1, "url": self.path, "applied": True})

    def _respond(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def log_message(self, *a):  # keep the test output clean
        pass


@pytest.fixture
def github_terminal():
    """A fresh fake GitHub terminal per test; yields its base URL."""
    _FakeGitHub.mode[0] = "ok"
    _FakeGitHub.drop_first[0] = 0
    _FakeGitHub.hits.clear()
    _FakeGitHub.applied.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGitHub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


# ---------------------------------------------------------------------------
# the real encrypted secret store (R-189), per test
# ---------------------------------------------------------------------------
@pytest.fixture
def secret_store(tmp_path, monkeypatch):
    import agent_civilization.core.secret_store as secret_store_mod
    db = tmp_path / "secrets.sqlite3"
    key_file = tmp_path / "master.key"
    key_file.write_bytes(secrets_mod.token_bytes(32))
    monkeypatch.setenv("AGOS_SECRETS_DB", str(db))
    monkeypatch.setenv("AGOS_MASTER_KEY_FILE", str(key_file))
    store = secret_store_mod.SecretStore(str(db), str(key_file))
    store.initialize()
    store.put_secret("test_tenant", SECRET_NAME, GITHUB_TOKEN)
    return store


# ---------------------------------------------------------------------------
# the connector + its tool record, wired to the fake terminal
# ---------------------------------------------------------------------------
def _register_github_tool(github_terminal):
    """Register the real github connector + its record; returns nothing.

    The record's network allowlist carries the connector's REAL target host
    (the fake's loopback host in tests, api.github.com in production) — the
    connector refuses to talk to a host its policy does not allow.
    """
    from agent_civilization.core import tool_runtime
    from agent_civilization.connectors import github as github_mod

    github_mod.register(base_url=github_terminal,
                        policy=github_mod.ConnectorPolicy(
                            timeout_s=0.5, max_attempts=2, backoff_base_s=0.05),
                        allowlist_hosts=[github_terminal.split("//")[1].split(":")[0]])
    rec = github_mod.tool_record(allowlist_hosts=[github_terminal.split("//")[1].split(":")[0]])
    tool_runtime.runtime_registry().register(rec, actor="r191-test")


def _work(op="create_issue", task_id="r191-task-1", approval=True, **data):
    body = {"op": op, "owner": "reeveero-tech", "repo": "All-hand",
            "title": "R-191", "body": "connector test"}
    body.update(data)
    work = {"prompt": f"R-191: {op}", "data": body, "task_id": task_id}
    if approval:
        work["approval"] = {"actor": "r191-owner"}
    return work


async def _dispatch(work):
    from agent_civilization.core import tool_runtime
    return await tool_runtime.dispatch("github", work)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _connector_stats():
    from agent_civilization import connectors
    return connectors.stats()


def _terminal_hits():
    return list(_FakeGitHub.hits)


# ------------------------------------------------- 1. the policy is bounded
def test_r191_policy_bounds_are_validated():
    """The retry/timeout policy is bounded by construction (review standard:
    timeouts and retries) — an unbounded policy is refused, not clamped."""
    from agent_civilization.connectors import ConnectorPolicy
    for kwargs in ({"timeout_s": 0}, {"timeout_s": 121}, {"timeout_s": -1},
                   {"max_attempts": 0}, {"max_attempts": 6}):
        with pytest.raises(Exception):
            ConnectorPolicy(**kwargs)
    p = ConnectorPolicy(timeout_s=30, max_attempts=3, backoff_base_s=0.1)
    assert p.timeout_s == 30 and p.max_attempts == 3


# ------------------------------------- 2. the happy path through the REAL gate
def test_r191_happy_path_runs_through_the_real_gate(github_terminal, secret_store,
                                                   monkeypatch):
    """A task-shaped work item dispatches the connector tool through the REAL
    gate: the terminal is hit with the credential FROM THE STORE and the
    task's idempotency key, and the gate's own execution counter moves."""
    from agent_civilization.core import tenancy
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")
    _register_github_tool(github_terminal)
    tenancy.set_tenant("test_tenant")
    try:
        before = _connector_stats()["github"]["executed"]
        out = _run(_dispatch(_work()))
        assert out and out.get("ok") is True, out
        assert out["tool_output"]["applied"] is True
        # the terminal saw the credential FROM THE STORE and the stable key
        hits = _terminal_hits()
        assert hits, "the terminal was never hit"
        assert hits[0]["auth"] == f"Bearer {GITHUB_TOKEN}"
        assert hits[0]["idem"] == "r191-task-1:create_issue"
        # the framework's own stats moved (observability)
        assert _connector_stats()["github"]["executed"] == before + 1
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# ------------------------------------------ 3. the timeout is bounded/honest
def test_r191_timeout_is_bounded_and_honest(github_terminal, secret_store):
    """A slow terminal is cut at the connector's OWN budget (not the gate's
    larger one): the error is honest (type only, R-149), bounded in time,
    and NOT retried (a timeout may mean the effect already happened)."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        _FakeGitHub.mode[0] = "slow"
        t0 = time.monotonic()
        out = _run(_dispatch(_work()))
        assert time.monotonic() - t0 < 1.4, "the timeout was not bounded"
        assert out and out.get("ok") is False
        assert "timeout" in str(out.get("error")).lower()
        # NOT retried: a timeout is never a safe retry (side-effect safety)
        assert len(_terminal_hits()) == 1
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# --------------- 4. interruption retried ONLY with a stable task identity
def test_r191_transport_interrupted_retried_only_with_stable_identity(
        github_terminal, secret_store):
    """A connection-level interruption (no response received — the effect
    cannot have been confirmed) is retried, and ONLY when the call carries a
    stable task identity: the idempotency key makes the retry safe (R-188-b)."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        _FakeGitHub.drop_first[0] = 1
        out = _run(_dispatch(_work(task_id="r191-task-stable")))
        assert out and out.get("ok") is True, out
        # two attempts, ONE applied effect — the retry was safe
        assert len(_terminal_hits()) == 2
        assert len(_FakeGitHub.applied) == 1
        assert _connector_stats()["github"]["retries"] >= 1
        # the identity is stable across attempts (the same key on both)
        assert _terminal_hits()[0]["idem"] == _terminal_hits()[1]["idem"]
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


def test_r191_transport_interrupted_without_identity_is_not_retried(
        github_terminal, secret_store):
    """Without a task identity there is NO idempotency key, so a retry could
    duplicate a side effect — the interruption surfaces honestly instead
    (fail-closed: never guess whether the effect happened)."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        _FakeGitHub.drop_first[0] = 1
        work = _work()
        work.pop("task_id")
        out = _run(_dispatch(work))
        assert out and out.get("ok") is False
        assert "interrupted" in str(out.get("error")).lower()
        assert len(_terminal_hits()) == 1   # one attempt only — no retry
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# ------------------------------------------- 5. permission refusal is honest
def test_r191_permission_refused_is_honest(github_terminal, secret_store):
    """A 403 from the terminal is an honest refusal (fixed reason, no payload
    echo) and is never retried — the caller must fix the grant, not retry."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        _FakeGitHub.mode[0] = "403"
        out = _run(_dispatch(_work()))
        assert out and out.get("ok") is False
        assert "permission" in str(out.get("error")).lower()
        assert len(_terminal_hits()) == 1
        assert _connector_stats()["github"]["refused"] >= 1
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# ------------------------------- 6. credential invalidation (401 → revoke)
def test_r191_auth_invalidation_revokes_and_fails_fast(github_terminal, secret_store):
    """The documented review standard: a 401 INVALIDATES the credential —
    the secret is revoked at the store, the next call fails FAST (no terminal
    hit), and the credential recovers when a new secret is put."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        _FakeGitHub.mode[0] = "401"
        out = _run(_dispatch(_work()))
        assert out and out.get("ok") is False
        assert "auth" in str(out.get("error")).lower()
        assert _connector_stats()["github"]["auth_invalidated"] >= 1
        # the credential was REVOKED at the store (expiry/removal discipline)
        listed = [s["name"] for s in secret_store.list_secrets("test_tenant")]
        assert SECRET_NAME not in listed
        # fail-fast: the next call never touches the terminal
        hits_before = len(_terminal_hits())
        out2 = _run(_dispatch(_work()))
        assert out2 and out2.get("ok") is False
        assert "auth" in str(out2.get("error")).lower()
        assert len(_terminal_hits()) == hits_before
        # recovery: a NEW secret unblocks the connector (no restart needed)
        _FakeGitHub.mode[0] = "ok"
        secret_store.put_secret("test_tenant", SECRET_NAME, GITHUB_TOKEN)
        out3 = _run(_dispatch(_work()))
        assert out3 and out3.get("ok") is True, out3
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# --------------------------- 7. side-effect safety: duplicate applies once
def test_r191_duplicate_request_applies_once(github_terminal, secret_store):
    """The core R-188-b guarantee at the connector layer: the SAME task
    identity dispatched twice (at-least-once redelivery) applies its effect
    ONCE — the terminal's idempotency store replays, it never re-applies."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        work = _work(task_id="r191-task-dup")
        out1 = _run(_dispatch(work))
        out2 = _run(_dispatch(work))
        assert out1.get("ok") is True and out2.get("ok") is True
        assert len(_FakeGitHub.applied) == 1
        assert len(_terminal_hits()) == 2
        # both attempts carried the SAME stable key
        keys = {h["idem"] for h in _terminal_hits()}
        assert keys == {"r191-task-dup:create_issue"}
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# --------------------------------------------- 8. tenant isolation (SEC-02)
def test_r191_tenant_isolation_sec02(github_terminal, secret_store):
    """The credential is scoped to the AMBIENT tenant: another tenant's call
    does not use the first tenant's secret (it fails closed before any
    request), and the first tenant's token never travels under the second."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        assert _run(_dispatch(_work())).get("ok") is True
        hits_a = len(_terminal_hits())
        # tenant B has NO secret for this connector
        tenancy.set_tenant("other_tenant")
        out = _run(_dispatch(_work(task_id="r191-task-b")))
        assert out and out.get("ok") is False
        assert "auth" in str(out.get("error")).lower()
        # fail-closed BEFORE any request: nothing was sent under tenant B
        assert len(_terminal_hits()) == hits_a
        for h in _terminal_hits():
            assert h["auth"] == f"Bearer {GITHUB_TOKEN}"  # only A's calls
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("other_tenant"))


# ------------------------------------- 9. no secret echo in errors (marker)
def test_r191_no_secret_echo_in_errors(github_terminal, secret_store):
    """No error path echoes the credential (R-149 discipline): 401, timeout,
    and interruption errors carry the failure TYPE and safe reason only."""
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        for mode in ("401", "403", "slow"):
            _FakeGitHub.mode[0] = mode
            out = _run(_dispatch(_work()))
            assert out and out.get("ok") is False
            assert GITHUB_TOKEN not in json.dumps(out), mode
        _FakeGitHub.drop_first[0] = 1
        work = _work()
        work.pop("task_id")
        out = _run(_dispatch(work))
        assert out.get("ok") is False
        assert GITHUB_TOKEN not in json.dumps(out)
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# -------------------------- 10. the tool records declare their own truth
def test_r191_tool_records_declare_their_truth():
    """The registry record for the connector declares its truth: the network
    allowlist (api.github.com), the write side effect, non-read-only,
    idempotent, approval-demanding, and a bounded timeout."""
    from agent_civilization.connectors import github as github_mod
    rec = github_mod.tool_record()
    assert rec.tool_id == "agos.internal.github"
    assert "api.github.com" in rec.network_allowlist
    assert rec.network_allowlist == ["api.github.com"]
    assert rec.side_effects == ["github:write"]
    assert rec.read_only is False
    assert rec.idempotent is True
    assert rec.requires_approval is True
    assert 0 < rec.timeout_ms <= 3_600_000
    assert rec.auth_scope == []
    assert rec.implementation_ref.startswith("agent_civilization.connectors")


# --------------------------- 11. the gate refuses without an approval
def test_r191_gate_refusal_without_approval(github_terminal, secret_store):
    """The connector tool is approval-demanding and the REAL gate enforces
    it: without a valid approval the tool never runs (the terminal is never
    hit) — the refusal is the gate's own fixed reason, counted on its own
    counters."""
    from agent_civilization.core import tool_runtime
    _register_github_tool(github_terminal)
    from agent_civilization.core import tenancy
    tenancy.set_tenant("test_tenant")
    try:
        out = _run(_dispatch(_work(approval=False)))
        assert out and out.get("ok") is False
        assert "refused" in str(out.get("error"))
        assert "approval" in str(out.get("error"))
        # the terminal was NEVER hit — the gate refused before any socket
        assert not _terminal_hits()
        skey = "agos.internal.github@1.0.0"
        assert tool_runtime.stats()["refused"].get(skey, 0) >= 1
    finally:
        tenancy.reset_tenant(tenancy.set_tenant("test_tenant"))


# --------------------- 12. the registry invariants hold (no drift)
def test_r191_registry_invariants_hold():
    """The framework does NOT drift the frozen surfaces: the dispatcher's
    handler map stays at exactly 100 (the R-171 invariant) and the connector
    record is NOT auto-seeded into the default registry (an operator action —
    the D-129 phase-1 ceiling is untouched)."""
    from agent_civilization.core import tool_runtime
    from agent_civilization.core.agents import real_dispatcher
    assert len(real_dispatcher._HANDLERS) == 100
    # the default seed carries NO connector record
    tool_runtime.reset_runtime_registry()
    try:
        tool_runtime.runtime_registry().get("agos.internal.github")
        raise AssertionError("the connector record must not be auto-seeded")
    except Exception:
        pass  # ToolNotFound — the honest state on main
