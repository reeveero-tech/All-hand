"""R-173: tool import adapters (MCP / OpenAPI) — batch 1 (D-118).

What this module IS: allowlisted import of EXTERNAL tool metadata into the
R-171 registry as honest, non-executable records — the review pipeline's
front door.

What it deliberately is NOT (declared limits):
- Batch 1 declared "no live fetch, no execution binding" — batch 2
  (``core/mcp_client.py``, D-119) closed both for MCP: manifests are
  fetched live through an SSRF-checked client, and PROMOTED imported MCP
  tools execute through the runtime gate. This module remains the pure
  metadata contract: it never opens a socket itself.
- It does NOT make anything callable by itself: imported records land in
  lifecycle ``discovered`` + test_status ``not_tested`` — the R-171/R-172
  execution gate refuses non-executable lifecycles, so a sensitive
  endpoint can never become an automatically available function.
  Promotion to ``registered`` is an explicit lifecycle move with an actor
  (human review).
- It does NOT bind a Python implementation: ``implementation_ref`` is an
  honest unresolvable placeholder (``import_<adapter>:<name>``) — the
  batch-2 execution binding is STRUCTURAL (record content protected by
  the digest: the ``mcp.`` id prefix, ``source.uri`` and the server
  origin inside ``network_allowlist``), never a shared executor
  reference (the registry's alias rule forbids one ref for many tools).
- OpenAPI imports stay records-only (owner directive, D-119): no
  execution adapter exists for them.
- Batch 3 (D-120) added the CLI and SDK/connector adapters (records-only
  by declared limit — executing arbitrary commands or SDK bindings is a
  security decision requiring an explicit owner directive), the
  ``authScope``-as-demand mapping (per-adapter auth contracts), drift
  coverage for the new shapes, and the governed certification-flow entry
  point lives in ``tool_registry.record_test_outcome`` (test-status
  transitions with proof requirements — the 10K spec's
  smoke/contract/security rule).

Contracts (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-173):
- Separate adapters with version pinning: this module pins the spec/protocol
  revisions it understands (verified against the latest stable revisions on
  2026-11-02 — MCP ``2026-07-28`` is the current stable revision; OpenAPI
  ``3.2.1`` published 2026-09-10 is the latest published spec). A manifest
  naming an unknown revision is refused with an explicit reason, not guessed.
- Allowlisted import: only explicitly allowlisted names (MCP) / operationIds
  (OpenAPI) become records; everything else is refused and counted.
- External metadata is UNTRUSTED: the description text from the source is
  carried as descriptive text only — capabilities stay neutral, security
  fields get fail-closed defaults, and nothing about the external claim is
  treated as reviewed.
- No provider credential leakage: auth material in the source (OpenAPI
  ``securitySchemes`` values, inline api keys, MCP server headers) is NEVER
  mapped into any record field. Records may reference auth ABSTRACTLY via
  ``auth_scope`` strings only.
- Schema drift: re-importing a tool whose payload changed is detected via an
  import fingerprint and reported — the registry's versions are immutable,
  so a drift is never silently overwritten; it demands a new version.
- Timeouts: every imported record gets a conservative ``timeout_ms``
  (default 30s, capped by the record schema) — set before any execution
  can ever happen.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .public_text import redact
from .tool_registry import (DuplicateTool, RegistryError, ToolNotFound,
                            ToolRegistry)

# --------------------------------------------------------------------------
# Version pinning (the roadmap demands: verify the latest stable spec at
# implementation time and record the decision — D-118).
# --------------------------------------------------------------------------

#: Known stable MCP protocol revisions (verified 2026-11-02: the current
#: stable revision is 2026-07-28; earlier stable revisions remain in the
#: wild and their tools/list tool-definition shape is unchanged).
MCP_PROTOCOL_REVISIONS: Tuple[str, ...] = (
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
LATEST_MCP_REVISION = MCP_PROTOCOL_REVISIONS[-1]

#: OpenAPI spec majors whose paths/operations subset this adapter maps
#: (verified 2026-11-02: latest published spec is 3.2.1, 2026-09-10; the
#: mapped subset — paths, methods, operationId, servers, securitySchemes —
#: is stable across 3.0/3.1/3.2).
OPENAPI_SUPPORTED_MAJORS: Tuple[str, ...] = ("3.0", "3.1", "3.2")
LATEST_OPENAPI_VERSION = "3.2.1"

#: This module's own contract version (bump on adapter behavior change).
#: 1.1.0 (batch 3, D-120): CLI and SDK/connector adapters + authScope-as-
#: demand mapping + drift coverage for the new shapes.
IMPORT_ADAPTER_VERSION = "1.1.0"

#: The version an imported, unreviewed record gets. Pre-release semantics:
#: the external source declared no version for the tool; the first reviewed
#: promotion or drift re-import is where a real version line begins.
IMPORTED_RECORD_VERSION = "0.1.0"

#: Conservative defaults for unreviewed external tools — fail-closed by
#: design (an unreviewed tool is treated as: not read-only, not
#: idempotent, approval-demanding, medium risk, no auth, no network).
DEFAULT_TIMEOUT_MS = 30_000
MAX_IMPORT_ITEMS = 5_000


@dataclass(frozen=True)
class ImportOutcome:
    """What happened to one candidate tool during an import run."""

    tool_id: str
    action: str
    reason: str = ""


@dataclass
class ImportReport:
    """The audit-friendly result of an import run (counts + outcomes)."""

    adapter: str
    protocol_revision: str
    outcomes: List[ImportOutcome] = field(default_factory=list)

    def add(self, tool_id: str, action: str, reason: str = "") -> None:
        self.outcomes.append(ImportOutcome(tool_id, action, reason))

    @property
    def imported(self) -> List[ImportOutcome]:
        return [o for o in self.outcomes if o.action == "imported"]

    @property
    def refused(self) -> List[ImportOutcome]:
        return [o for o in self.outcomes if o.action != "imported"]

    def count(self, action: str) -> int:
        return sum(1 for o in self.outcomes if o.action == action)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for o in self.outcomes:
            counts[o.action] = counts.get(o.action, 0) + 1
        return counts


# --------------------------------------------------------------------------
# Shared record-building helpers
# --------------------------------------------------------------------------

def origin(uri: str) -> str:
    """The normalized origin of a URL (scheme://host[:port], defaults
    folded). Used for the import binding: a live-imported record's
    ``network_allowlist`` carries the server origin, and the execution
    adapter (``mcp_client``) refuses any destination not covered by it.
    Raises ``ValueError`` for a URL without scheme/host (the import path
    reports it as a refusal — never a guess)."""
    from urllib.parse import urlsplit
    parts = urlsplit(uri)
    if not parts.scheme or not parts.hostname:
        raise ValueError(f"not an absolute http(s) URL: {uri!r}")
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"                      # bare IPv6 literal
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"bad port in URL: {uri!r}") from None
    default = 443 if parts.scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}"


def _sanitize_tool_id(prefix: str, name: str) -> Optional[str]:
    """Build a registry-legal tool_id from an external name (or None)."""
    cleaned = "".join(c if (c.isalnum() or c in "_.-") else "_" for c in name)
    if not cleaned or len(cleaned) > 128:
        return None
    return f"{prefix}.{cleaned}"


def _import_fingerprint(payload: Any) -> str:
    """A stable digest of the normalized imported payload — drift detection."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _base_record(*, tool_id: str, name: str, source: Dict[str, Any],
                 description: str, input_schema: Dict[str, Any],
                 capabilities: List[str], timeout_ms: int,
                 side_effects: List[str], read_only: bool,
                 requires_approval: bool, risk_class: str,
                 implementation_ref: str, network_allowlist: List[str],
                 approval_scopes: List[str]) -> Dict[str, Any]:
    """The fail-closed record skeleton every adapter fills in.

    External descriptions are capped and de-minimum'd (the record schema
    demands 1..8000 chars) but are NEVER extended with anything the source
    didn't say — untrusted text stays untrusted text. One hard rule on top
    (R-173 contract: no provider credential leakage): every text field the
    source controls passes through ``public_text.redact`` at the boundary —
    a source that embeds its own key in a description gets the key redacted,
    not stored.
    """
    desc = (description or "").strip()
    if not desc:
        desc = "imported from an external manifest; description untrusted until reviewed"
    desc = str(redact(desc))
    if not desc or desc == "<redacted>":
        desc = "imported from an external manifest; description redacted at the boundary"
    return {
        "tool_id": tool_id,
        "name": name,
        "version": IMPORTED_RECORD_VERSION,
        "source": source,
        "description": desc[:8000],
        "capabilities": capabilities,
        "input_schema": input_schema,
        # The record schema demands a structural "type"; the least-claiming
        # valid declaration is "object" (documented placeholder — the
        # external source declared no output schema, and nothing here claims
        # it was reviewed).
        "output_schema": {"type": "object"},
        "auth_scope": [],  # least privilege: no auth granted at import
        "tenant_scope": "request_tenant",
        "risk_class": risk_class,
        "side_effects": side_effects,
        "read_only": read_only,
        "idempotent": False,  # conservative: assume not idempotent
        "requires_approval": requires_approval,
        "approval_scopes": approval_scopes,
        "timeout_ms": min(int(timeout_ms), 3_600_000),
        "network_allowlist": network_allowlist,
        "health_status": "unknown",
        "test_status": "not_tested",
        "lifecycle_state": "discovered",  # NOT executable (the gate refuses)
        "implementation_ref": implementation_ref,  # honest unresolvable placeholder
    }


def _register(registry: ToolRegistry, record: Dict[str, Any], *, actor: str,
              report: ImportReport) -> bool:
    """Register one record; the OUTCOME reported is the truth, not the intent."""
    try:
        registry.register(record, actor=actor)
        return True
    except DuplicateTool:
        # Versions are immutable — a re-import is a drift question, not an
        # overwrite. The drift check below is what reports changed payloads;
        # a byte-identical re-import is reported as already-present.
        report.add(record["tool_id"], "refused_duplicate",
                   f"{record['tool_id']}@{record['version']} already registered "
                   "(versions are immutable; a changed payload is drift)")
        return False
    except RegistryError as e:
        report.add(record["tool_id"], "refused_invalid", str(e)[:200])
        return False


# --------------------------------------------------------------------------
# MCP adapter
# --------------------------------------------------------------------------

def import_mcp_manifest(manifest: Dict[str, Any], *, registry: ToolRegistry,
                        allowlist: Sequence[str], actor: str,
                        server_uri: Optional[str] = None,
                        timeout_ms: int = DEFAULT_TIMEOUT_MS
                        ) -> ImportReport:
    """Import allowlisted tools from an MCP tools/list-shaped manifest.

    The manifest shape (stable across the known stable revisions): a dict
    with ``tools`` — a list of ``{name, description, inputSchema}``. The
    protocol revision, when the manifest carries one (``protocolVersion``),
    must be a known stable revision; unknown revisions are refused with an
    explicit reason rather than guessed at.

    Batch 2 (D-119) — the binding of intent: when ``server_uri`` names the
    server the manifest came from (a LIVE import via ``mcp_client``), the
    imported records carry that server's ORIGIN in their
    ``network_allowlist`` (and the full URL in ``source.uri``). This is
    what later makes the tool executable after an explicit lifecycle
    promotion — the execution adapter refuses any destination not covered
    by the record's own allowlist. An invalid ``server_uri`` refuses the
    import (fail-closed, reported) — never a guessed binding.
    """
    revision = str(manifest.get("protocolVersion") or LATEST_MCP_REVISION)
    report = ImportReport(adapter="mcp", protocol_revision=revision)
    if revision not in MCP_PROTOCOL_REVISIONS:
        report.add("", "refused_unsupported_protocol",
                   f"unknown MCP revision {revision!r} "
                   f"(known stable: {', '.join(MCP_PROTOCOL_REVISIONS)})")
        return report

    tools = manifest.get("tools")
    if not isinstance(tools, list):
        report.add("", "refused_invalid", "manifest has no 'tools' list")
        return report
    if len(tools) > MAX_IMPORT_ITEMS:
        report.add("", "refused_invalid",
                   f"manifest too large ({len(tools)} > {MAX_IMPORT_ITEMS})")
        return report

    # batch 2 (D-119): a named server binds the records to its origin (an
    # invalid URL refuses the import — never a guessed binding)
    network_allowlist: List[str] = []
    if server_uri is not None:
        try:
            network_allowlist = [origin(server_uri)]
        except ValueError as e:
            report.add("", "refused_invalid", f"server_uri invalid: {e}")
            return report

    allowed = set(allowlist)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        tool_id = _sanitize_tool_id("mcp", name) if name else None
        if tool_id is None:
            report.add(str(name or "?")[:64], "refused_invalid",
                       "tool name missing or not representable")
            continue
        if name not in allowed:
            report.add(tool_id, "refused_not_in_allowlist",
                       f"{name!r} is not in the import allowlist")
            continue
        input_schema = tool.get("inputSchema")
        if not isinstance(input_schema, dict):
            report.add(tool_id, "refused_invalid",
                       "tool has no inputSchema object")
            continue
        record = _base_record(
            tool_id=tool_id, name=name,
            source={"kind": "mcp", "uri": server_uri, "revision": revision},
            description=str(tool.get("description") or ""),
            input_schema=input_schema,
            capabilities=["import:mcp"],  # neutral — the claim is unreviewed
            timeout_ms=timeout_ms,
            side_effects=[],  # unknown → conservative defaults below
            read_only=False,
            requires_approval=True,  # fail-closed until reviewed
            risk_class="medium",
            implementation_ref=f"import_mcp:{name}",
            # batch 2 (D-119): the named server's origin when the import
            # is live, else no network intent declared
            network_allowlist=network_allowlist,
            approval_scopes=[],
        )
        if _register(registry, record, actor=actor, report=report):
            report.add(tool_id, "imported")
    return report


# --------------------------------------------------------------------------
# OpenAPI adapter
# --------------------------------------------------------------------------

_READ_ONLY_METHODS = {"get", "head", "options"}
_DESTRUCTIVE_METHODS = {"delete", "put", "patch", "post"}


def _openapi_input_schema(operation: Dict[str, Any]) -> Dict[str, Any]:
    """The operation's request schema, or the documented placeholder.

    The record schema demands a structural "type". An OpenAPI operation
    carries its schema inside requestBody.content.<media>.schema — when the
    operation has no body (or an opaque one) the least-claiming valid
    declaration is {"type": "object"} (a structural placeholder, not a
    reviewed claim).
    """
    body = operation.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content")
        if isinstance(content, dict):
            for media in ("application/json", "application/x-www-form-urlencoded",
                          "*/*"):
                entry = content.get(media)
                if isinstance(entry, dict) and isinstance(entry.get("schema"), dict):
                    schema = dict(entry["schema"])
                    if not isinstance(schema.get("type"), (str, list)):
                        schema["type"] = "object"
                    return schema
    return {"type": "object"}


def import_openapi_spec(spec: Dict[str, Any], *, registry: ToolRegistry,
                        allowlist: Sequence[str], actor: str,
                        timeout_ms: int = DEFAULT_TIMEOUT_MS
                        ) -> ImportReport:
    """Import allowlisted operations from an OpenAPI 3.x spec.

    Only ``operationId``s in the allowlist become records. HTTP semantics
    drive the honest defaults: read-only methods stay read-only; any
    mutating method is side-effectful and approval-demanding; destructive
    (DELETE) operations are high-risk. The spec's ``servers`` become the
    record's network allowlist (an allowlist of INTENT — the R-172 gate
    still enforces the sockets policy). ``securitySchemes`` values are
    deliberately never mapped into records — auth stays abstract.
    """
    version = str(spec.get("openapi") or "")
    report = ImportReport(adapter="openapi", protocol_revision=version)
    if not any(version.startswith(m + ".") for m in OPENAPI_SUPPORTED_MAJORS):
        report.add("", "refused_unsupported_protocol",
                   f"unsupported OpenAPI version {version!r} "
                   f"(supported majors: {', '.join(OPENAPI_SUPPORTED_MAJORS)})")
        return report

    paths = spec.get("paths")
    if not isinstance(paths, dict):
        report.add("", "refused_invalid", "spec has no 'paths' object")
        return report
    if len(paths) > MAX_IMPORT_ITEMS:
        report.add("", "refused_invalid",
                   f"spec too large ({len(paths)} paths > {MAX_IMPORT_ITEMS})")
        return report

    # The servers list — network allowlist of intent (origins only, no keys).
    network_allowlist: List[str] = []
    servers = spec.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict) and isinstance(server.get("url"), str):
                network_allowlist.append(server["url"])

    allowed = set(allowlist)
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in _READ_ONLY_METHODS | _DESTRUCTIVE_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            op_id = str(operation.get("operationId") or "")
            tool_id = _sanitize_tool_id("openapi", op_id) if op_id else None
            if tool_id is None:
                continue  # operations without an operationId are not namable
            if op_id not in allowed:
                report.add(tool_id, "refused_not_in_allowlist",
                           f"operationId {op_id!r} is not in the import allowlist")
                continue
            input_schema = _openapi_input_schema(operation)
            record = _base_record(
                tool_id=tool_id, name=op_id,
                source={"kind": "openapi", "revision": version,
                        "uri": f"paths/{path}/{method}"},
                description=str(operation.get("summary")
                                or operation.get("description") or ""),
                input_schema=input_schema,
                capabilities=["import:openapi"],
                timeout_ms=timeout_ms,
                side_effects=[] if method in _READ_ONLY_METHODS else ["writes"],
                read_only=method in _READ_ONLY_METHODS,
                requires_approval=method not in _READ_ONLY_METHODS,
                risk_class="high" if method == "delete" else "medium",
                implementation_ref=f"import_openapi:{op_id}",
                network_allowlist=network_allowlist,
                approval_scopes=[],
            )
            if _register(registry, record, actor=actor, report=report):
                report.add(tool_id, "imported")
    return report


# --------------------------------------------------------------------------
# CLI adapter (batch 3, D-120)
# --------------------------------------------------------------------------

#: OUR manifest contract (unlike MCP/OpenAPI this is not an external
#: protocol — the pin is this module's own contract version; a manifest
#: naming an unknown version is refused with an explicit reason).
CLI_MANIFEST_VERSION = "1.0.0"


def _manifest_tools(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The manifest's ``tools`` entries that are objects (others ignored)."""
    tools = manifest.get("tools")
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, dict)]


def _validated_auth_scope(tool: Dict[str, Any]) -> Optional[List[str]]:
    """The manifest's ``authScope`` — a DEMAND (scopes the tool requires),
    never a credential. Missing/invalid ⇒ None (the caller refuses the
    tool); an empty list is a valid "demands nothing" declaration."""
    raw = tool.get("authScope")
    if raw is None:
        return []
    if (not isinstance(raw, list)
            or not all(isinstance(s, str) and s.strip() for s in raw)):
        return None
    return list(raw)


def import_cli_manifest(manifest: Dict[str, Any], *, registry: ToolRegistry,
                         allowlist: Sequence[str], actor: str,
                         timeout_ms: int = DEFAULT_TIMEOUT_MS
                         ) -> ImportReport:
    """Import allowlisted CLI tools from OUR pinned CLI manifest contract.

    Shape: ``{"cliManifestVersion": "1.0.0", "tools": [{"name",
    "description", "command", "argsSchema"?, "authScope"?, "readOnly"?}]}``.
    The manifest is operator/CI material, but its TEXT is untrusted boundary
    material exactly like MCP/OpenAPI manifests: descriptions pass through
    ``public_text.redact``; ``authScope`` is a scope DEMAND (mapped to the
    record's ``auth_scope``), never a credential; the command is stored as
    the tool's executable identity (``source.uri`` — argv[0], the tool's
    "address" in the CLI world).

    RECORDS-ONLY (D-120, declared limit): executing arbitrary commands is a
    security decision that requires an explicit owner directive (sandbox,
    argv validation, injection review). Records land ``discovered`` +
    ``not_tested`` with fail-closed defaults; promotion is an explicit,
    actor-audited lifecycle move; and the runtime gate answers an explicit
    records-only refusal for ``cli.`` addresses."""
    version = str(manifest.get("cliManifestVersion") or "")
    report = ImportReport(adapter="cli", protocol_revision=version)
    if version != CLI_MANIFEST_VERSION:
        report.add("", "refused_unsupported_protocol",
                   f"unsupported CLI manifest version {version!r} "
                   f"(this adapter's pinned contract: {CLI_MANIFEST_VERSION})")
        return report

    tools = manifest.get("tools")
    if not isinstance(tools, list):
        report.add("", "refused_invalid", "manifest has no 'tools' list")
        return report
    if len(tools) > MAX_IMPORT_ITEMS:
        report.add("", "refused_invalid",
                   f"manifest too large ({len(tools)} > {MAX_IMPORT_ITEMS})")
        return report

    allowed = set(allowlist)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        tool_id = _sanitize_tool_id("cli", name) if name else None
        if tool_id is None:
            report.add(str(name or "?")[:64], "refused_invalid",
                       "tool name missing or not representable")
            continue
        if name not in allowed:
            report.add(tool_id, "refused_not_in_allowlist",
                       f"{name!r} is not in the import allowlist")
            continue
        command = tool.get("command")
        if not (isinstance(command, str) and command.strip()):
            report.add(tool_id, "refused_invalid",
                      "tool has no command (the executable identity)")
            continue
        auth_scope = _validated_auth_scope(tool)
        if auth_scope is None:
            report.add(tool_id, "refused_invalid",
                       "authScope must be a list of non-empty scope strings")
            continue
        input_schema = tool.get("argsSchema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object"}   # documented placeholder
        record = _base_record(
            tool_id=tool_id, name=name,
            source={"kind": "cli", "uri": command.strip(),
                    "revision": CLI_MANIFEST_VERSION},
            description=str(tool.get("description") or ""),
            input_schema=input_schema,
            capabilities=["import:cli"],
            timeout_ms=timeout_ms,
            side_effects=[],
            read_only=bool(tool.get("readOnly")),  # default: assume writes
            requires_approval=True,                 # fail-closed regardless
            risk_class="medium",
            implementation_ref=f"import_cli:{name}",
            network_allowlist=[],
            approval_scopes=[],
        )
        record["auth_scope"] = auth_scope         # a DEMAND, not a credential
        if _register(registry, record, actor=actor, report=report):
            report.add(tool_id, "imported")
    return report


# --------------------------------------------------------------------------
# SDK/connector adapter (batch 3, D-120)
# --------------------------------------------------------------------------

#: OUR manifest contract (same pinning rationale as the CLI adapter).
SDK_MANIFEST_VERSION = "1.0.0"

#: the binding shape this adapter accepts: module:qualname (the same shape
#: the registry's implementation_ref uses — validated as a SHAPE only; the
#: binding is untrusted manifest text, never resolved at import time).
_SDK_BINDING_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


def import_sdk_manifest(manifest: Dict[str, Any], *, registry: ToolRegistry,
                        allowlist: Sequence[str], actor: str,
                        timeout_ms: int = DEFAULT_TIMEOUT_MS
                        ) -> ImportReport:
    """Import allowlisted SDK/connector bindings from OUR pinned manifest
    contract.

    Shape: ``{"sdkManifestVersion": "1.0.0", "tools": [{"name",
    "description", "binding", "inputSchema"?, "authScope"?, "kind"?}]}``.
    ``binding`` (``module:qualname`` — where the tool lives in the SDK
    world) is stored as the record's address (``source.uri``), validated as
    a SHAPE only and never resolved at import. ``kind`` selects ``sdk``
    (default) or ``connector`` — both are the same adapter, and the tool_id
    prefix follows the kind (``sdk.`` / ``connector.``). ``authScope`` is a
    scope DEMAND; descriptions are redacted at the boundary.

    RECORDS-ONLY (D-120, declared limit): binding arbitrary SDK code is a
    security decision that requires an explicit owner directive. The
    runtime gate answers an explicit records-only refusal for both
    prefixes."""
    version = str(manifest.get("sdkManifestVersion") or "")
    report = ImportReport(adapter="sdk", protocol_revision=version)
    if version != SDK_MANIFEST_VERSION:
        report.add("", "refused_unsupported_protocol",
                   f"unsupported SDK manifest version {version!r} "
                   f"(this adapter's pinned contract: {SDK_MANIFEST_VERSION})")
        return report

    tools = manifest.get("tools")
    if not isinstance(tools, list):
        report.add("", "refused_invalid", "manifest has no 'tools' list")
        return report
    if len(tools) > MAX_IMPORT_ITEMS:
        report.add("", "refused_invalid",
                   f"manifest too large ({len(tools)} > {MAX_IMPORT_ITEMS})")
        return report

    allowed = set(allowlist)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        kind = str(tool.get("kind") or "sdk")
        if kind not in ("sdk", "connector"):
            report.add(str(name or "?")[:64], "refused_invalid",
                       f"unknown sdk kind {kind!r} (sdk/connector)")
            continue
        tool_id = _sanitize_tool_id(kind, name) if name else None
        if tool_id is None:
            report.add(str(name or "?")[:64], "refused_invalid",
                       "tool name missing or not representable")
            continue
        if name not in allowed:
            report.add(tool_id, "refused_not_in_allowlist",
                       f"{name!r} is not in the import allowlist")
            continue
        binding = tool.get("binding")
        if not (isinstance(binding, str) and _SDK_BINDING_RE.match(binding)):
            report.add(tool_id, "refused_invalid",
                       "binding must be a module:qualname reference")
            continue
        auth_scope = _validated_auth_scope(tool)
        if auth_scope is None:
            report.add(tool_id, "refused_invalid",
                       "authScope must be a list of non-empty scope strings")
            continue
        input_schema = tool.get("inputSchema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object"}   # documented placeholder
        record = _base_record(
            tool_id=tool_id, name=name,
            source={"kind": kind, "uri": binding,
                    "revision": SDK_MANIFEST_VERSION},
            description=str(tool.get("description") or ""),
            input_schema=input_schema,
            capabilities=[f"import:{kind}"],
            timeout_ms=timeout_ms,
            side_effects=[],
            read_only=False,                    # unknown → assume writes
            requires_approval=True,             # fail-closed regardless
            risk_class="medium",
            implementation_ref=f"import_{kind}:{name}",
            network_allowlist=[],
            approval_scopes=[],
        )
        record["auth_scope"] = auth_scope         # a DEMAND, not a credential
        if _register(registry, record, actor=actor, report=report):
            report.add(tool_id, "imported")
    return report


# --------------------------------------------------------------------------
# Drift detection (pure — no registry writes)
# --------------------------------------------------------------------------

def drift_check(registry: ToolRegistry, manifest: Dict[str, Any],
                allowlist: Sequence[str],
                server_uri: Optional[str] = None) -> List[Dict[str, str]]:
    """Report imported tools whose CURRENT manifest payload differs from
    the stored record's import fingerprint.

    Pure comparison: it never writes. A drift means the operator must
    register a NEW version of the tool (versions are immutable) — a silent
    overwrite is impossible by construction. The comparison re-derives the
    record the CURRENT manifest would produce and diffs it against the
    stored one field by field (source excluded — it carries the revision);
    any difference in a mapped field is drift. The record itself deliberately
    stores no fingerprint (descriptions are untrusted text — no trust is
    placed in anything the source could rewrite).

    Batch 2 (D-119): pass the SAME ``server_uri`` the live import used —
    the derived ``network_allowlist`` depends on it, and comparing a
    live-imported record against a derivation without it would report a
    spurious drift on that field.
    """
    drifts: List[Dict[str, str]] = []
    candidates = _candidate_records(manifest, allowlist,
                                     server_uri=server_uri)
    for tool_id, record in candidates.items():
        try:
            stored = registry.get(tool_id, IMPORTED_RECORD_VERSION)
        except ToolNotFound:
            continue  # not imported yet — nothing to drift against
        for field_name, new_value in record.items():
            if field_name == "source":
                continue  # source carries the revision — checked separately
            stored_value = getattr(stored, field_name, None)
            if stored_value != new_value:
                drifts.append({
                    "tool_id": tool_id,
                    "field": field_name,
                    "reason": f"imported payload changed "
                              f"({stored_value!r} -> {new_value!r})",
                })
                break
    return drifts


def _candidate_records(manifest: Dict[str, Any],
                       allowlist: Sequence[str],
                       server_uri: Optional[str] = None
                       ) -> Dict[str, Dict[str, Any]]:
    """Re-derive the records a manifest WOULD produce (no registration) —
    for every adapter shape: MCP (``protocolVersion``), CLI
    (``cliManifestVersion``) and SDK/connector (``sdkManifestVersion``).
    A manifest mixing the shape keys derives nothing (a refusal to guess,
    not a claim of no drift)."""
    out: Dict[str, Dict[str, Any]] = {}
    allowed = set(allowlist)
    shape_keys = [k for k in ("protocolVersion", "cliManifestVersion",
                             "sdkManifestVersion") if k in manifest]
    if len(shape_keys) != 1:
        return out
    shape = shape_keys[0]
    if shape == "cliManifestVersion":
        for tool in _manifest_tools(manifest):
            name = str(tool.get("name") or "")
            command = tool.get("command")
            if (name not in allowed
                    or not (isinstance(command, str) and command.strip())
                    or _validated_auth_scope(tool) is None):
                continue
            tool_id = _sanitize_tool_id("cli", name)
            if tool_id is None:
                continue
            input_schema = tool.get("argsSchema")
            record = _base_record(
                tool_id=tool_id, name=name,
                source={"kind": "cli", "uri": command.strip(),
                        "revision": CLI_MANIFEST_VERSION},
                description=str(tool.get("description") or ""),
                input_schema=input_schema if isinstance(input_schema, dict)
                else {"type": "object"},
                capabilities=["import:cli"], timeout_ms=DEFAULT_TIMEOUT_MS,
                side_effects=[], read_only=bool(tool.get("readOnly")),
                requires_approval=True, risk_class="medium",
                implementation_ref=f"import_cli:{name}",
                network_allowlist=[], approval_scopes=[])
            record["auth_scope"] = _validated_auth_scope(tool) or []
            out[tool_id] = record
        return out
    if shape == "sdkManifestVersion":
        for tool in _manifest_tools(manifest):
            name = str(tool.get("name") or "")
            kind = str(tool.get("kind") or "sdk")
            binding = tool.get("binding")
            if (name not in allowed or kind not in ("sdk", "connector")
                    or not (isinstance(binding, str)
                            and _SDK_BINDING_RE.match(binding))
                    or _validated_auth_scope(tool) is None):
                continue
            tool_id = _sanitize_tool_id(kind, name)
            if tool_id is None:
                continue
            input_schema = tool.get("inputSchema")
            record = _base_record(
                tool_id=tool_id, name=name,
                source={"kind": kind, "uri": binding,
                        "revision": SDK_MANIFEST_VERSION},
                description=str(tool.get("description") or ""),
                input_schema=input_schema if isinstance(input_schema, dict)
                else {"type": "object"},
                capabilities=[f"import:{kind}"],
                timeout_ms=DEFAULT_TIMEOUT_MS, side_effects=[],
                read_only=False, requires_approval=True, risk_class="medium",
                implementation_ref=f"import_{kind}:{name}",
                network_allowlist=[], approval_scopes=[])
            record["auth_scope"] = _validated_auth_scope(tool) or []
            out[tool_id] = record
        return out
    # the MCP shape (batch 1/2 derivation, unchanged)
    network_allowlist: List[str] = []
    if server_uri is not None:
        try:
            network_allowlist = [origin(server_uri)]
        except ValueError:
            return out                # not derivable from this input — no
            # candidates, so drift_check reports nothing (a refusal to
            # guess, not a claim of no drift)
    if "tools" in manifest and isinstance(manifest.get("tools"), list):
        for tool in manifest["tools"]:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "")
            if name not in set(allowlist):
                continue
            tool_id = _sanitize_tool_id("mcp", name)
            if tool_id is None:
                continue
            input_schema = tool.get("inputSchema")
            if not isinstance(input_schema, dict):
                continue
            out[tool_id] = _base_record(
                tool_id=tool_id, name=name,
                source={"kind": "mcp", "uri": None, "revision":
                        str(manifest.get("protocolVersion")
                            or LATEST_MCP_REVISION)},
                description=str(tool.get("description") or ""),
                input_schema=input_schema,
                capabilities=["import:mcp"],
                timeout_ms=DEFAULT_TIMEOUT_MS,
                side_effects=[], read_only=False, requires_approval=True,
                risk_class="medium", implementation_ref=f"import_mcp:{name}",
                network_allowlist=network_allowlist, approval_scopes=[],
            )
    return out
