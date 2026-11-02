"""R-173 batch 2 (D-119): live MCP client — SSRF-checked fetch + execution binding.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-173; owner
directive for this batch: MCP client first, OpenAPI stays records-only):

- fetching a manifest live is SSRF-checked: non-HTTP schemes, userinfo,
  missing hosts, private/loopback/link-local/reserved destinations are
  refused by default (an explicit operator allowance is required for a
  local server), and a hostname that RESOLVES to a private address is
  refused just like a literal one (DNS-based SSRF);
- DNS rebinding: the connection is validated (all resolved addresses
  public), and the peer actually connected to is re-checked after the
  socket is established — a name that re-resolves mid-flight is refused;
- execution binding: a PROMOTED imported MCP tool (explicit lifecycle
  move, actor-audited) runs through the same real gate as internal tools
  — lifecycle, health, approval policy, scopes, network policy, timeout
  and output caps all apply, and the run is a real HTTP round trip to the
  allowlisted server origin;
- OpenAPI imports are records-only in this batch: dispatching them is an
  explicit refusal, never a silent None and never an execution;
- a record whose network allowlist does not cover its own server origin
  is refused before any connection is attempted.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_civilization.core import mcp_client
from agent_civilization.core import tool_import as ti
from agent_civilization.core import tool_runtime

PROBE = {"prompt": "mcp execution probe", "goal": "mcp execution probe"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "tools.sqlite3")
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", path)
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield path
    tool_runtime.reset_runtime_registry()


def _registry():
    return tool_runtime.runtime_registry()


SERVER_TOOLS = [{"name": "echo_tool", "description": "Echo the arguments back",
                 "inputSchema": {"type": "object",
                                 "properties": {"text": {"type": "string"}}}}]


class _McpHandler(BaseHTTPRequestHandler):
    """A minimal streamable-HTTP MCP server (JSON responses, session id)."""

    server_version = "mcp-test/1.0"

    def log_message(self, *a):  # silence — test output stays readable
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            msg = json.loads(body)
        except ValueError:
            self._reply(400, {"ok": False, "error": "bad json"})
            return
        if self.path != "/mcp":
            self._reply(404, {"ok": False, "error": "unknown path"})
            return
        method = msg.get("method")
        if method == "initialize":
            self._reply(200, {
                "jsonrpc": "2.0", "id": msg.get("id"),
                "result": {"protocolVersion": "2026-07-28",
                           "capabilities": {},
                           "serverInfo": {"name": "mcp-test", "version": "1.0"}}})
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("mcp-session-id", "test-session-1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/list":
            self._reply(200, {
                "jsonrpc": "2.0", "id": msg.get("id"),
                "result": {"tools": self.server.tools}})
        elif method == "tools/call":
            result = self.server.on_call(msg.get("params") or {})
            self._reply(200, {"jsonrpc": "2.0", "id": msg.get("id"),
                              "result": result})
        else:
            self._reply(400, {"jsonrpc": "2.0", "id": msg.get("id"),
                              "error": {"message": f"no such method {method}"}})

    def _reply(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("mcp-session-id", "test-session-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _LocalMcpServer:
    """A real HTTP MCP server on 127.0.0.1 (ephemeral port)."""

    def __init__(self, *, tools=None, call_result=None, delay_s=0.0):
        self.tools = tools if tools is not None else SERVER_TOOLS
        self._call_result = call_result
        self._delay_s = delay_s
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
        self._httpd.daemon_threads = True
        self._httpd.tools = self.tools
        self._httpd.on_call = self._on_call
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()

    def _on_call(self, params):
        if self._delay_s:
            import time
            time.sleep(self._delay_s)
        if self._call_result is not None:
            return self._call_result
        return {"content": [{"type": "text",
                             "text": json.dumps({"echo": params.get("arguments")})}],
                "isError": False}

    @property
    def url(self):
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def server():
    srv = _LocalMcpServer()
    yield srv
    srv.close()


# -- SSRF / DNS-rebinding checks (pure, no server needed) --------------------

def test_non_http_schemes_are_refused():
    for uri in ("file:///etc/passwd", "ftp://example.com/mcp",
                "gopher://example.com/x"):
        with pytest.raises(mcp_client.McpClientError) as ei:
            mcp_client.check_url(uri)
        assert "scheme" in str(ei.value)


def test_userinfo_and_missing_host_are_refused():
    for uri in ("https://user:secret@example.com/mcp", "https:///mcp"):
        with pytest.raises(mcp_client.McpClientError):
            mcp_client.check_url(uri)


def test_private_destinations_are_refused_by_default():
    for uri in ("http://127.0.0.1:9/mcp",          # loopback literal
                "http://169.254.169.254/latest",   # cloud metadata
                "http://10.0.0.1/mcp",              # private range
                "http://[::1]/mcp",                 # IPv6 loopback
                "http://localhost/mcp"):            # resolves to loopback
        with pytest.raises(mcp_client.McpClientError) as ei:
            mcp_client.check_url(uri)
        assert "non-public" in str(ei.value)


def test_private_destinations_pass_only_with_an_explicit_allowance():
    # the operator decision (a local test/dev server) is explicit, never
    # inferred from anything the external source controls
    ips = mcp_client.check_url("http://127.0.0.1:9/mcp", allow_private=True)
    assert ips == ["127.0.0.1"]


def test_a_hostname_resolving_to_a_private_ip_is_refused():
    # DNS-based SSRF: the name looks public, the address is not
    def fake_resolver(host, port=None):
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    with pytest.raises(mcp_client.McpClientError):
        mcp_client.check_url("http://rebind.example.com/mcp",
                             resolver=fake_resolver)


def test_the_connected_peer_is_revalidated_after_the_socket_opens():
    # DNS rebinding: the name validated to one address, the socket landed
    # on another — refused, connection torn down
    with pytest.raises(mcp_client.McpClientError) as ei:
        mcp_client._assert_peer("10.0.0.9", ["93.184.216.34"])
    assert "rebinding" in str(ei.value)
    mcp_client._assert_peer("93.184.216.34", ["93.184.216.34"])  # ok


# -- live fetch + import ------------------------------------------------------

def test_live_fetch_imports_records_with_the_server_origin_allowlisted(db, server):
    report = mcp_client.import_from_uri(server.url, registry=_registry(),
                                       allowlist=["echo_tool"], actor="test:r173",
                                       allow_private=True)
    assert report.count("imported") == 1
    rec = _registry().get("mcp.echo_tool")
    assert rec.lifecycle_state == "discovered"      # still not executable
    assert rec.network_allowlist == [mcp_client.origin(server.url)]
    assert rec.source.uri == server.url


def test_live_fetch_refuses_a_local_server_without_the_explicit_allowance(db, server):
    with pytest.raises(mcp_client.McpClientError) as ei:
        mcp_client.import_from_uri(server.url, registry=_registry(),
                                   allowlist=["echo_tool"], actor="test:r173")
    assert "non-public" in str(ei.value)


def test_import_with_an_invalid_server_uri_is_refused(db):
    report = ti.import_mcp_manifest(
        {"protocolVersion": "2026-07-28", "tools": SERVER_TOOLS},
        registry=_registry(), allowlist=["echo_tool"], actor="test:r173",
        server_uri="::not a url::")
    assert report.count("imported") == 0
    assert report.count("refused_invalid") == 1


def test_drift_check_derives_the_live_import_allowlist(db, server):
    mcp_client.import_from_uri(server.url, registry=_registry(),
                               allowlist=["echo_tool"], actor="test:r173",
                               allow_private=True)
    manifest = {"protocolVersion": "2026-07-28", "tools": SERVER_TOOLS}
    drifts = ti.drift_check(_registry(), manifest, ["echo_tool"],
                            server_uri=server.url)
    assert drifts == []                              # same source, no drift


# -- execution binding ---------------------------------------------------------

def test_a_promoted_imported_tool_executes_through_the_real_gate(db, server):
    mcp_client.import_from_uri(server.url, registry=_registry(),
                              allowlist=["echo_tool"], actor="test:r173",
                              allow_private=True)
    _registry().update_status("mcp.echo_tool", ti.IMPORTED_RECORD_VERSION,
                              actor="reviewer:alice", lifecycle_state="registered")
    result = run(tool_runtime.dispatch(
        "mcp.echo_tool", {"arguments": {"text": "hello"},
                          "approval": {"actor": "approver:bob"}}))
    assert result["ok"] is True                      # a real HTTP round trip
    echoed = json.loads(result["result"]["content"][0]["text"])["echo"]
    assert echoed == {"text": "hello"}
    key = f"mcp.echo_tool@{ti.IMPORTED_RECORD_VERSION}"
    assert tool_runtime.stats()["executed"][key] == 1


def test_a_discovered_imported_tool_is_refused_not_silently_ignored(db, server):
    """The sensitive-endpoint guarantee, batch-2 form: the imported tool is
    ADDRESSABLE now (the binding exists) but the gate refuses it with an
    explicit error while its lifecycle is not executable — never a silent
    None, and never a run."""
    mcp_client.import_from_uri(server.url, registry=_registry(),
                              allowlist=["echo_tool"], actor="test:r173",
                              allow_private=True)
    result = run(tool_runtime.dispatch(
        "mcp.echo_tool", {"arguments": {"text": "x"},
                          "approval": {"actor": "approver:bob"}}))
    assert result is not None
    assert result["ok"] is False
    assert "discovered" in result["error"]
    key = f"mcp.echo_tool@{ti.IMPORTED_RECORD_VERSION}"
    assert tool_runtime.stats()["executed"].get(key, 0) == 0


def test_an_imported_tool_still_demands_approval_after_promotion(db, server):
    mcp_client.import_from_uri(server.url, registry=_registry(),
                              allowlist=["echo_tool"], actor="test:r173",
                              allow_private=True)
    _registry().update_status("mcp.echo_tool", ti.IMPORTED_RECORD_VERSION,
                              actor="reviewer:alice", lifecycle_state="registered")
    result = run(tool_runtime.dispatch("mcp.echo_tool",
                                       {"arguments": {"text": "x"}}))
    assert result["ok"] is False
    assert "requires_approval" in result["error"]


def test_openapi_imports_are_records_only(db):
    spec = {"openapi": "3.1.0",
            "paths": {"/w": {"get": {"operationId": "get_weather",
                                      "summary": "Read weather"}}}}
    ti.import_openapi_spec(spec, registry=_registry(), allowlist=["get_weather"],
                           actor="test:r173")
    _registry().update_status("openapi.get_weather", ti.IMPORTED_RECORD_VERSION,
                              actor="reviewer:alice", lifecycle_state="registered")
    result = run(tool_runtime.dispatch("openapi.get_weather", dict(PROBE)))
    assert result["ok"] is False
    assert "records-only" in result["error"]


def test_the_registered_timeout_kills_the_live_call(db):
    slow = _LocalMcpServer(delay_s=3.0)
    try:
        mcp_client.import_from_uri(slow.url, registry=_registry(),
                                  allowlist=["echo_tool"], actor="test:r173",
                                  allow_private=True, timeout_ms=1_000)
        _registry().update_status("mcp.echo_tool", ti.IMPORTED_RECORD_VERSION,
                                  actor="reviewer:alice",
                                  lifecycle_state="registered")
        result = run(tool_runtime.dispatch(
            "mcp.echo_tool", {"arguments": {}, "approval": {"actor": "a:b"}}))
        assert result["ok"] is False
        assert "timeout_ms=1000" in result["error"]
        key = f"mcp.echo_tool@{ti.IMPORTED_RECORD_VERSION}"
        assert tool_runtime.stats()["timed_out"][key] == 1
    finally:
        slow.close()


def test_output_caps_withhold_huge_imported_results(db, server, monkeypatch):
    monkeypatch.setenv("AGOS_TOOL_MAX_OUTPUT_BYTES", "500")
    big = _LocalMcpServer(
        call_result={"content": [{"type": "text", "text": "x" * 10_000}],
                     "isError": False})
    try:
        mcp_client.import_from_uri(big.url, registry=_registry(),
                                   allowlist=["echo_tool"], actor="test:r173",
                                   allow_private=True)
        _registry().update_status("mcp.echo_tool", ti.IMPORTED_RECORD_VERSION,
                                  actor="reviewer:alice",
                                  lifecycle_state="registered")
        result = run(tool_runtime.dispatch(
            "mcp.echo_tool", {"arguments": {}, "approval": {"actor": "a:b"}}))
        assert result["ok"] is False
        assert "output cap" in result["error"]
    finally:
        big.close()


def test_a_record_without_network_allowance_for_its_server_is_refused(db):
    # an operator-crafted row: bound server NOT covered by its allowlist —
    # refused before any connection is attempted (no server needed at all)
    record = ti._base_record(
        tool_id="mcp.rogue", name="rogue",
        source={"kind": "mcp", "uri": "http://127.0.0.1:1/mcp",
                "revision": "2026-07-28"},
        description="rogue row", input_schema={"type": "object"},
        capabilities=["import:mcp"], timeout_ms=1_000, side_effects=[],
        read_only=False, requires_approval=True, risk_class="medium",
        implementation_ref="import_mcp:rogue", network_allowlist=[],
        approval_scopes=[])
    _registry().register(record, actor="test:r173")
    _registry().update_status("mcp.rogue", ti.IMPORTED_RECORD_VERSION,
                              actor="reviewer:alice", lifecycle_state="registered")
    result = run(tool_runtime.dispatch("mcp.rogue",
                                       {"arguments": {}, "approval": {"actor": "a:b"}}))
    assert result["ok"] is False
    assert "network allowlist" in result["error"]


def test_a_server_side_error_maps_to_an_honest_failure(db):
    failing = _LocalMcpServer(
        call_result={"content": [{"type": "text", "text": "boom"}],
                     "isError": True})
    try:
        mcp_client.import_from_uri(failing.url, registry=_registry(),
                                   allowlist=["echo_tool"], actor="test:r173",
                                   allow_private=True)
        _registry().update_status("mcp.echo_tool", ti.IMPORTED_RECORD_VERSION,
                                  actor="reviewer:alice",
                                  lifecycle_state="registered")
        result = run(tool_runtime.dispatch(
            "mcp.echo_tool", {"arguments": {}, "approval": {"actor": "a:b"}}))
        assert result["ok"] is False
        assert "boom" in result["error"]
    finally:
        failing.close()
