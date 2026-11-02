"""R-173 batch 3 (D-120): CLI/SDK import adapters + the governed certification
flow (test-status transitions with proof requirements).

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-173; the 10K
tools spec):

- adapters for external SDK/CLI providers (the roadmap's «مزودون
  خارجيون/SDK/CLI: adapters ذات نسخ pinned»): version-pinned manifest
  contracts of OURS (unlike MCP/OpenAPI these are not external protocols —
  the pin is our own contract version, refused explicitly when unknown),
  allowlisted import, fail-closed defaults, redaction at the boundary;
- per-adapter AUTH handling: a manifest ``authScope`` is a DEMAND (scopes
  the tool requires), never a credential; MCP execution-side scope
  enforcement (the gate refuses a run without the granted scopes);
- CLI/SDK imports are RECORDS-ONLY in this batch (executing arbitrary
  commands or SDK bindings is a security decision requiring an explicit
  owner directive — declared limit, D-120);
- the certification flow (R-175 depends on it): test-status transitions
  are governed — a legal-transition graph, mandatory evidence for every
  transition, and ``certified`` requires all three proof kinds the 10K
  spec demands (smoke/contract/security); the lifecycle's own rule
  (certified lifecycle requires test_status certified) integrates with
  the governed path.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import tool_import as ti
from agent_civilization.core import tool_registry as tr
from agent_civilization.core import tool_runtime

PROBE = {"prompt": "cli/sdk import probe", "goal": "cli/sdk import probe"}


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
    with pytest.raises(Exception):
        _registry().get(tool_id)


CLI_MANIFEST = {
    "cliManifestVersion": "1.0.0",
    "tools": [
        {"name": "grep_files", "description": "Search files for a pattern",
         "command": "rg", "authScope": ["fs:read"], "readOnly": True,
         "argsSchema": {"type": "object",
                        "properties": {"pattern": {"type": "string"}}}},
        {"name": "deploy_app", "description": "Deploy the application",
         "command": "deploy-tool"},
    ],
}

SDK_MANIFEST = {
    "sdkManifestVersion": "1.0.0",
    "tools": [
        {"name": "send_email", "description": "Send an email via the provider SDK",
         "binding": "providersdk:mail_send", "authScope": ["email:send"]},
        {"name": "query_crm", "description": "Query the CRM connector",
         "binding": "crmconnector:query_contacts", "kind": "connector"},
    ],
}


# -- CLI adapter contract ------------------------------------------------------

def test_cli_allowlisted_tool_imported_as_a_discovered_record(db):
    report = ti.import_cli_manifest(CLI_MANIFEST, registry=_registry(),
                                     allowlist=["grep_files"], actor="test:r173")
    assert report.count("imported") == 1
    rec = _registry().get("cli.grep_files")
    assert rec.lifecycle_state == "discovered"       # not executable
    assert rec.source.kind == "cli"
    assert rec.source.uri == "rg"                    # the executable identity
    assert rec.source.revision == "1.0.0"           # our pinned contract
    assert rec.auth_scope == ["fs:read"]            # a DEMAND, not a credential
    assert rec.read_only is True                    # honored from the manifest
    assert rec.requires_approval is True            # fail-closed regardless
    assert rec.timeout_ms == ti.DEFAULT_TIMEOUT_MS
    assert rec.implementation_ref == "import_cli:grep_files"  # placeholder


def test_cli_unknown_manifest_version_is_refused(db):
    manifest = dict(CLI_MANIFEST, cliManifestVersion="0.9.0")
    report = ti.import_cli_manifest(manifest, registry=_registry(),
                                    allowlist=["grep_files"], actor="test:r173")
    assert report.count("refused_unsupported_protocol") == 1
    assert "0.9.0" in report.refused[0].reason
    _assert_absent("cli.grep_files")


def test_cli_tool_outside_the_allowlist_is_refused_and_counted(db):
    report = ti.import_cli_manifest(CLI_MANIFEST, registry=_registry(),
                                     allowlist=["grep_files"], actor="test:r173")
    assert report.count("refused_not_in_allowlist") == 1
    assert report.count("imported") == 1
    _assert_absent("cli.deploy_app")


def test_cli_tool_without_a_command_is_refused(db):
    manifest = {"cliManifestVersion": "1.0.0",
                "tools": [{"name": "no_cmd", "description": "x"}]}
    report = ti.import_cli_manifest(manifest, registry=_registry(),
                                    allowlist=["no_cmd"], actor="test:r173")
    assert report.count("refused_invalid") == 1
    _assert_absent("cli.no_cmd")


def test_cli_auth_scope_is_a_demand_not_a_credential(db):
    # a tool with no authScope demands nothing (least privilege); a secret
    # planted in the description never lands in the record (boundary redact)
    secret = "sk-CLI-SECRET-77c1ab34cd56"
    manifest = {"cliManifestVersion": "1.0.0",
                "tools": [{"name": "grep_files", "command": "rg",
                           "description": f"uses key {secret}"}]}
    ti.import_cli_manifest(manifest, registry=_registry(), allowlist=["grep_files"],
                           actor="test:r173")
    rec = _registry().get("cli.grep_files")
    assert rec.auth_scope == []
    assert secret not in str(rec.model_dump())


# -- SDK/connector adapter contract --------------------------------------------

def test_sdk_allowlisted_tool_imported_with_its_binding_address(db):
    report = ti.import_sdk_manifest(SDK_MANIFEST, registry=_registry(),
                                    allowlist=["send_email"], actor="test:r173")
    assert report.count("imported") == 1
    rec = _registry().get("sdk.send_email")
    assert rec.source.kind == "sdk"
    assert rec.source.uri == "providersdk:mail_send"  # the binding address
    assert rec.source.revision == "1.0.0"
    assert rec.auth_scope == ["email:send"]
    assert rec.implementation_ref == "import_sdk:send_email"


def test_sdk_connector_kind_gets_the_connector_prefix(db):
    report = ti.import_sdk_manifest(SDK_MANIFEST, registry=_registry(),
                                    allowlist=["query_crm"], actor="test:r173")
    assert report.count("imported") == 1
    rec = _registry().get("connector.query_crm")
    assert rec.source.kind == "connector"


def test_sdk_invalid_binding_shape_is_refused(db):
    manifest = {"sdkManifestVersion": "1.0.0",
                "tools": [{"name": "bad", "description": "x",
                           "binding": "not a ref"}]}
    report = ti.import_sdk_manifest(manifest, registry=_registry(),
                                    allowlist=["bad"], actor="test:r173")
    assert report.count("refused_invalid") == 1
    _assert_absent("sdk.bad")


def test_sdk_unknown_manifest_version_is_refused(db):
    manifest = dict(SDK_MANIFEST, sdkManifestVersion="2.0.0")
    report = ti.import_sdk_manifest(manifest, registry=_registry(),
                                    allowlist=["send_email"], actor="test:r173")
    assert report.count("refused_unsupported_protocol") == 1


# -- records-only (no execution binding for CLI/SDK in this batch) --------------

def test_cli_and_sdk_imports_are_records_only(db):
    ti.import_cli_manifest(CLI_MANIFEST, registry=_registry(),
                           allowlist=["grep_files"], actor="test:r173")
    ti.import_sdk_manifest(SDK_MANIFEST, registry=_registry(),
                           allowlist=["send_email"], actor="test:r173")
    for tool_id in ("cli.grep_files", "sdk.send_email"):
        _registry().update_status(tool_id, ti.IMPORTED_RECORD_VERSION,
                                  actor="reviewer:alice",
                                  lifecycle_state="registered")
        result = run(tool_runtime.dispatch(tool_id, dict(PROBE)))
        assert result is not None
        assert result["ok"] is False
        assert "records-only" in result["error"]


# -- per-adapter auth at the EXECUTION side (MCP) --------------------------------

def test_mcp_imported_tool_with_auth_scope_runs_only_with_granted_scopes(db):
    # an operator-crafted MCP row demanding scopes: the gate refuses the run
    # until the work item GRANTS them (least privilege, fail-closed)
    record = ti._base_record(
        tool_id="mcp.scoped", name="scoped",
        source={"kind": "mcp", "uri": "https://public.example/mcp",
                "revision": "2026-07-28"},
        description="scoped row", input_schema={"type": "object"},
        capabilities=["import:mcp"], timeout_ms=30_000, side_effects=[],
        read_only=True, requires_approval=True, risk_class="medium",
        implementation_ref="import_mcp:scoped",
        network_allowlist=["https://public.example"], approval_scopes=[])
    record["auth_scope"] = ["crm:read"]
    _registry().register(record, actor="test:r173")
    _registry().update_status("mcp.scoped", ti.IMPORTED_RECORD_VERSION,
                              actor="reviewer:alice", lifecycle_state="registered")
    work = {"arguments": {}, "approval": {"actor": "a:b"}}
    result = run(tool_runtime.dispatch("mcp.scoped", dict(work)))
    assert result["ok"] is False
    assert "missing required auth scopes" in result["error"]
    assert "crm:read" in result["error"]
    # with the scope granted the gate lets the run through to the executor
    # (which then fails honestly on the unreachable server — a transport
    # failure, NOT a scope refusal: the auth wall was passed)
    result = run(tool_runtime.dispatch("mcp.scoped",
                                       dict(work, scopes=["crm:read"])))
    assert "auth scopes" not in str(result)


# -- the governed certification flow (test-status transitions) --------------------

def test_test_status_transitions_follow_the_governed_graph(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    tid, ver = "cli.grep_files", ti.IMPORTED_RECORD_VERSION
    # the legal path: not_tested -> contract_pass -> integration_pass -> certified
    rec = reg.record_test_outcome(tid, ver, actor="tester:ci",
                                 to="contract_pass", evidence="log 345 contract run")
    assert rec.test_status == "contract_pass"
    rec = reg.record_test_outcome(tid, ver, actor="tester:ci",
                                 to="integration_pass", evidence="log 345 integration")
    rec = reg.record_test_outcome(tid, ver, actor="tester:ci",
                                 to="certified",
                                 evidence="smoke ok; contract ok; security ok")
    assert rec.test_status == "certified"
    # certified is terminal except failure: a regression is a legal move,
    # any other jump out of certified is refused
    with pytest.raises(tr.TransitionRefused):
        reg.record_test_outcome(tid, ver, actor="tester:ci",
                               to="contract_pass", evidence="retest")


def test_a_jump_skipping_the_graph_is_refused(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    with pytest.raises(tr.TransitionRefused):
        reg.record_test_outcome("cli.grep_files", ti.IMPORTED_RECORD_VERSION,
                               actor="tester:ci", to="certified",
                               evidence="smoke; contract; security")


def test_certified_requires_all_three_proof_kinds(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    tid, ver = "cli.grep_files", ti.IMPORTED_RECORD_VERSION
    reg.record_test_outcome(tid, ver, actor="tester:ci",
                           to="contract_pass", evidence="log 345")
    reg.record_test_outcome(tid, ver, actor="tester:ci",
                           to="integration_pass", evidence="log 345")
    # missing the security proof: refused with the missing kind named
    with pytest.raises(tr.TransitionRefused) as ei:
        reg.record_test_outcome(tid, ver, actor="tester:ci",
                               to="certified", evidence="smoke ok; contract ok")
    assert "security" in str(ei.value)


def test_every_transition_demands_evidence(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    with pytest.raises(tr.InvalidRecord):
        reg.record_test_outcome("cli.grep_files", ti.IMPORTED_RECORD_VERSION,
                               actor="tester:ci", to="contract_pass", evidence="  ")


def test_the_certified_lifecycle_integrates_with_the_governed_path(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    tid, ver = "cli.grep_files", ti.IMPORTED_RECORD_VERSION
    # the registry's own rule: certified LIFECYCLE requires test_status certified
    with pytest.raises(tr.TransitionRefused):
        reg.update_status(tid, ver, actor="reviewer:alice",
                          lifecycle_state="certified")
    # through the governed path (contract -> integration -> certified-with-proofs)
    # the promotion to the certified lifecycle succeeds
    reg.record_test_outcome(tid, ver, actor="tester:ci",
                           to="contract_pass", evidence="log 345")
    reg.record_test_outcome(tid, ver, actor="tester:ci",
                           to="integration_pass", evidence="log 345")
    reg.record_test_outcome(tid, ver, actor="tester:ci",
                           to="certified",
                           evidence="smoke pass; contract pass; security pass")
    # discovered -> registered -> validated -> certified (the lifecycle's own
    # graph; the last hop requires the certified test_status the governed
    # path produced)
    reg.update_status(tid, ver, actor="reviewer:alice",
                     lifecycle_state="registered")
    rec = reg.update_status(tid, ver, actor="reviewer:alice",
                            lifecycle_state="validated")
    rec = reg.update_status(tid, ver, actor="reviewer:alice",
                            lifecycle_state="certified")
    assert rec.lifecycle_state == "certified"
    # and the audit trail carries the actors of the governed transitions
    log = [(e["op"], e["actor"]) for e in reg.audit()]
    assert ("update", "tester:ci") in log
    assert ("update", "reviewer:alice") in log


# -- drift detection covers the new adapters -------------------------------------

def test_drift_check_covers_cli_and_sdk_manifests(db):
    reg = _registry()
    ti.import_cli_manifest(CLI_MANIFEST, registry=reg, allowlist=["grep_files"],
                           actor="test:r173")
    ti.import_sdk_manifest(SDK_MANIFEST, registry=reg, allowlist=["send_email"],
                           actor="test:r173")
    # unchanged manifests: no drift
    assert ti.drift_check(reg, CLI_MANIFEST, ["grep_files"]) == []
    assert ti.drift_check(reg, SDK_MANIFEST, ["send_email"]) == []
    # a changed CLI payload is reported, never silently overwritten
    changed = {"cliManifestVersion": "1.0.0",
               "tools": [{"name": "grep_files", "command": "rg",
                          "description": "Search files for MANY patterns"}]}
    drifts = ti.drift_check(reg, changed, ["grep_files"])
    assert drifts and drifts[0]["tool_id"] == "cli.grep_files"
    assert drifts[0]["field"] == "description"
    # re-importing the changed payload is a duplicate, not an overwrite
    report = ti.import_cli_manifest(changed, registry=reg, allowlist=["grep_files"],
                                    actor="test:r173")
    assert report.count("refused_duplicate") == 1
