"""R-173 batch 1 (D-118): tool import adapters — MCP / OpenAPI.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-173):
- separate adapters with version pinning (unknown protocol revisions are
  refused explicitly, never guessed);
- allowlisted import (nothing outside the allowlist becomes a record);
- external metadata is untrusted (records land in lifecycle ``discovered``,
  non-executable at the real R-171/R-172 gate — a sensitive endpoint can
  never become an automatically available function);
- no provider credential leakage (auth material from the source never lands
  in any record field);
- schema drift is detected and reported, never silently overwritten;
- timeouts: every imported record carries a conservative timeout_ms.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import tool_import as ti
from agent_civilization.core import tool_runtime

PROBE = {"prompt": "import gate probe", "goal": "import gate probe"}


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


def _assert_absent(tool_id):
    """The registry always holds the 22 seeded internal tools — the honest
    absence check for an imported tool is a lookup refusal, not len==0."""
    import pytest as _pytest
    with _pytest.raises(Exception):
        _registry().get(tool_id)


MCP_MANIFEST = {
    "protocolVersion": "2026-07-28",
    "tools": [
        {"name": "weather_lookup",
         "description": "Look up current weather for a city",
         "inputSchema": {"type": "object",
                          "properties": {"city": {"type": "string"}}}},
        {"name": "dangerous_admin",
         "description": "Delete all user data",
         "inputSchema": {"type": "object"}},
    ],
}

OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/weather": {
            "get": {"operationId": "get_weather",
                    "summary": "Read current weather"},
            "post": {"operationId": "report_weather",
                     "summary": "Submit a weather report"},
        },
        "/users/{id}": {
            "delete": {"operationId": "delete_user",
                       "summary": "Delete a user account"},
        },
    },
}


# -- MCP adapter contract ---------------------------------------------------

def test_mcp_allowlisted_tool_imported_as_a_discovered_record(db):
    report = ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                                    allowlist=["weather_lookup"],
                                    actor="test:r173")
    assert report.count("imported") == 1
    rec = _registry().get("mcp.weather_lookup")
    assert rec.lifecycle_state == "discovered"       # NOT executable
    assert rec.test_status == "not_tested"           # nothing is claimed
    assert rec.version == ti.IMPORTED_RECORD_VERSION
    assert rec.source.kind == "mcp"
    assert rec.source.revision == "2026-07-28"        # pinned revision
    assert rec.capabilities == ["import:mcp"]        # neutral, unreviewed
    assert rec.implementation_ref == "import_mcp:weather_lookup"  # unresolvable


def test_mcp_tool_outside_the_allowlist_is_refused_and_counted(db):
    report = ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                                    allowlist=["weather_lookup"],
                                    actor="test:r173")
    assert report.count("refused_not_in_allowlist") == 1
    assert report.count("imported") == 1
    with pytest.raises(Exception):
        _registry().get("mcp.dangerous_admin")


def test_mcp_unknown_protocol_revision_is_refused_explicitly(db):
    manifest = dict(MCP_MANIFEST, protocolVersion="1999-01-01")
    report = ti.import_mcp_manifest(manifest, registry=_registry(),
                                     allowlist=["weather_lookup"],
                                     actor="test:r173")
    assert report.count("refused_unsupported_protocol") == 1
    assert "1999-01-01" in report.refused[0].reason
    _assert_absent("mcp.weather_lookup")             # nothing registered


def test_mcp_tool_without_an_input_schema_is_refused(db):
    manifest = {"protocolVersion": "2026-07-28",
                "tools": [{"name": "no_schema", "description": "x"}]}
    report = ti.import_mcp_manifest(manifest, registry=_registry(),
                                    allowlist=["no_schema"], actor="test:r173")
    assert report.count("refused_invalid") == 1
    _assert_absent("mcp.no_schema")


def test_an_imported_tool_is_not_executable_at_the_real_gate(db):
    """THE sensitive-endpoint guarantee: import alone can never make an
    external tool callable. Two independent walls hold (batch-2 form,
    D-119): (a) the tool is now ADDRESSABLE through the gate (the
    execution binding exists) — but the gate answers an explicit REFUSAL,
    never a run, never a silent None (a record-carrying address must not
    be indistinguishable from an unknown specialization);
    (b) the record itself is non-executable by lifecycle ("discovered" is
    not in the gate's EXECUTABLE_LIFECYCLES) — the gate's own rule."""
    ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                           allowlist=["weather_lookup"], actor="test:r173")
    result = run(tool_runtime.dispatch("mcp.weather_lookup", dict(PROBE)))
    assert result is not None                       # addressed — not ignored
    assert result["ok"] is False                     # refused, never run
    assert "discovered" in result["error"]
    rec = _registry().get("mcp.weather_lookup")
    assert rec.lifecycle_state == "discovered"
    assert rec.lifecycle_state not in tool_runtime.EXECUTABLE_LIFECYCLES


# -- OpenAPI adapter contract ----------------------------------------------

def test_openapi_read_only_get_maps_to_a_read_only_record(db):
    report = ti.import_openapi_spec(OPENAPI_SPEC, registry=_registry(),
                                    allowlist=["get_weather"], actor="test:r173")
    assert report.count("imported") == 1
    rec = _registry().get("openapi.get_weather")
    assert rec.read_only is True
    assert rec.side_effects == []
    assert rec.requires_approval is False


def test_openapi_mutating_and_destructive_operations_get_fail_closed(db):
    report = ti.import_openapi_spec(
        OPENAPI_SPEC, registry=_registry(),
        allowlist=["report_weather", "delete_user"], actor="test:r173")
    assert report.count("imported") == 2
    post = _registry().get("openapi.report_weather")
    assert post.read_only is False
    assert post.requires_approval is True             # fail-closed until review
    assert post.side_effects == ["writes"]
    assert post.risk_class == "medium"
    delete = _registry().get("openapi.delete_user")
    assert delete.risk_class == "high"                 # destructive = high
    assert delete.requires_approval is True


def test_openapi_servers_become_the_network_allowlist_of_intent(db):
    ti.import_openapi_spec(OPENAPI_SPEC, registry=_registry(),
                          allowlist=["get_weather"], actor="test:r173")
    rec = _registry().get("openapi.get_weather")
    assert rec.network_allowlist == ["https://api.example.com"]


def test_openapi_unsupported_version_is_refused(db):
    spec = dict(OPENAPI_SPEC, openapi="2.0")
    report = ti.import_openapi_spec(spec, registry=_registry(),
                                    allowlist=["get_weather"], actor="test:r173")
    assert report.count("refused_unsupported_protocol") == 1
    _assert_absent("openapi.get_weather")


def test_openapi_operation_without_an_operation_id_is_not_imported(db):
    spec = {"openapi": "3.1.0",
            "paths": {"/anon": {"get": {"summary": "no id"}}}}
    report = ti.import_openapi_spec(spec, registry=_registry(),
                                    allowlist=["*"], actor="test:r173")
    assert report.count("imported") == 0               # not namable, skipped


# -- no provider credential leakage ----------------------------------------

def test_no_credential_from_the_source_ever_lands_in_a_record(db):
    secret = "sk-PROVIDER-SECRET-9f2a"
    manifest = {
        "protocolVersion": "2026-07-28",
        "tools": [{"name": "weather_lookup", "description": "Look up weather",
                   "inputSchema": {"type": "object"},
                   "headers": {"Authorization": f"Bearer {secret}"}}],
    }
    spec = {
        "openapi": "3.1.0",
        "components": {"securitySchemes": {
            "apiKey": {"type": "apiKey", "name": "X-Api-Key",
                       "in": "header", "value": secret}}},
        "paths": {"/weather": {"get": {"operationId": "get_weather",
                                       "summary": f"uses key {secret}"}}},
    }
    ti.import_mcp_manifest(manifest, registry=_registry(),
                           allowlist=["weather_lookup"], actor="test:r173")
    ti.import_openapi_spec(spec, registry=_registry(),
                           allowlist=["get_weather"], actor="test:r173")
    for tool_id in ("mcp.weather_lookup", "openapi.get_weather"):
        dumped = _registry().get(tool_id).model_dump()
        assert secret not in str(dumped)               # nowhere — not even text
    rec = _registry().get("openapi.get_weather")
    assert rec.auth_scope == []                       # auth stays abstract


# -- schema drift ------------------------------------------------------------

def test_reimport_with_a_changed_payload_reports_drift_not_overwrite(db):
    ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                           allowlist=["weather_lookup"], actor="test:r173")
    original = _registry().get("mcp.weather_lookup").model_dump()
    changed = {"protocolVersion": "2026-07-28",
               "tools": [{"name": "weather_lookup",
                          "description": "Look up FORECAST weather now",
                          "inputSchema": {"type": "object"}}]}
    drifts = ti.drift_check(_registry(), changed, ["weather_lookup"])
    assert drifts and drifts[0]["tool_id"] == "mcp.weather_lookup"
    assert drifts[0]["field"] == "description"
    # the registry still holds the ORIGINAL immutable record
    assert _registry().get("mcp.weather_lookup").model_dump() == original
    # and re-importing is refused (duplicate), never an overwrite
    report = ti.import_mcp_manifest(changed, registry=_registry(),
                                    allowlist=["weather_lookup"],
                                    actor="test:r173")
    assert report.count("refused_duplicate") == 1


# -- timeouts ----------------------------------------------------------------

def test_imported_records_get_a_conservative_timeout(db):
    ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                          allowlist=["weather_lookup"], actor="test:r173")
    assert _registry().get("mcp.weather_lookup").timeout_ms == 30_000
    ti.import_openapi_spec(OPENAPI_SPEC, registry=_registry(),
                           allowlist=["get_weather"], actor="test:r173",
                           timeout_ms=5_000)
    assert _registry().get("openapi.get_weather").timeout_ms == 5_000


# -- promotion is an explicit, audited, human decision -----------------------

def test_promotion_to_executable_requires_an_explicit_lifecycle_move(db):
    ti.import_mcp_manifest(MCP_MANIFEST, registry=_registry(),
                           allowlist=["weather_lookup"], actor="test:r173")
    reg = _registry()
    # before promotion: addressed but refused (batch-2 form — the refusal is
    # explicit, never a silent None and never a run)
    result = run(tool_runtime.dispatch("mcp.weather_lookup", dict(PROBE)))
    assert result["ok"] is False and "discovered" in result["error"]
    reg.update_status("mcp.weather_lookup", ti.IMPORTED_RECORD_VERSION,
                      actor="reviewer:alice", lifecycle_state="registered")
    rec = reg.get("mcp.weather_lookup")
    assert rec.lifecycle_state == "registered"        # executable lifecycle…
    assert rec.requires_approval is True               # …but still fail-closed
    assert rec.test_status == "not_tested"            # review ≠ certification
    # the audit trail carries both the import and the promotion with actors
    log = [(e["op"], e["actor"]) for e in reg.audit()]
    assert ("register", "test:r173") in log
    assert ("update", "reviewer:alice") in log


# -- version pinning (the roadmap's recorded decision) -------------------------

def test_the_adapter_pins_the_verified_stable_revisions():
    # verified 2026-11-02: MCP 2026-07-28 is the current stable revision;
    # OpenAPI 3.2.1 (2026-09-10) is the latest published spec.
    assert ti.LATEST_MCP_REVISION == "2026-07-28"
    assert "2025-11-25" in ti.MCP_PROTOCOL_REVISIONS     # earlier stable kept
    assert ti.LATEST_OPENAPI_VERSION == "3.2.1"
    assert set(ti.OPENAPI_SUPPORTED_MAJORS) == {"3.0", "3.1", "3.2"}
    assert ti.IMPORT_ADAPTER_VERSION.count(".") == 2   # semver
