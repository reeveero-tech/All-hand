"""R-191 — the GitHub terminal connector: the framework's first REAL user.

One connector, four ops against the real GitHub REST API (issues), with the
framework's whole review-standard envelope: the credential lives in the
per-tenant secret store (R-189, name ``connector_github_token``), a 401
revokes it and fails fast, retries happen only for connection-level
interruptions with a stable task identity, and every call carries an
``Idempotency-Key`` of ``{task_id}:{op}`` (R-188-b: a duplicate applies
once).

The tool glue: ``register()`` binds the connector instance and its async
tool handler; ``real_dispatcher.dispatch`` falls back to
``connectors.handler_for("github")`` (the documented FREEZE_DRIFT pattern —
the frozen ``_HANDLERS`` map and its ``==100`` invariant are untouched).
``tool_record()`` builds the registry record an OPERATOR registers (it is
NOT auto-seeded — the D-129 ceiling and the certify runbook stay at 100).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agent_civilization.core.tool_registry import ToolRecord

from . import (Connector, ConnectorError, ConnectorPolicy,
               _tool_handlers, register as _register)

TOOL_SPEC = "github"
TOOL_ID = "agos.internal.github"
TOOL_PREFIX = "agos.internal."
DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_ALLOWLIST = ["api.github.com"]
CREDENTIAL_NAME = "connector_github_token"
SEED_VERSION = "1.0.0"

# op -> (method, path template). Path parameters come from the work item's
# data (owner/repo/number) — the templates are fixed, only values are bound.
_OPS: Dict[str, Any] = {
    "list_issues": ("GET", "/repos/{owner}/{repo}/issues"),
    "get_issue": ("GET", "/repos/{owner}/{repo}/issues/{number}"),
    "create_issue": ("POST", "/repos/{owner}/{repo}/issues"),
    "add_comment": ("POST", "/repos/{owner}/{repo}/issues/{number}/comments"),
}

_OWNER_REPO_OK = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


def _binding(data: Dict[str, Any], key: str) -> str:
    v = data.get(key)
    if not (isinstance(v, str) and v.strip()):
        raise ConnectorError(f"the request data is missing {key!r}")
    return v.strip()


class GitHubConnector(Connector):
    """The GitHub issues terminal (one real connector, four ops)."""
    connector_id = "github"
    kind = "terminal"

    def build_request(self, op: str, data: Dict[str, Any]):
        if op not in _OPS:
            raise ConnectorError(f"unknown github op (expected one of: "
                                 f"{', '.join(sorted(_OPS))})")
        method, template = _OPS[op]
        values = {k: _binding(data, k)
                  for k in ("owner", "repo") + (("number",) if "{number}" in template else ())}
        for k in ("owner", "repo"):
            if not _OWNER_REPO_OK.match(values[k]):
                # a path parameter is a URL segment: refuse anything that
                # could escape it (a contract, not sanitisation)
                raise ConnectorError("owner/repo must be plain path segments")
        if "{number}" in template:
            try:
                values["number"] = str(int(data.get("number")))
            except (TypeError, ValueError):
                raise ConnectorError("number must be an integer") from None
        path = template.format(**values)
        body: Optional[Dict[str, Any]] = None
        if method == "POST":
            body = {}
            for key in ("title", "body"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    body[key] = v
        return method, path, body


_connector: Optional[GitHubConnector] = None


def register(base_url: str = DEFAULT_BASE_URL,
             policy: Optional[ConnectorPolicy] = None,
             allowlist_hosts: Optional[List[str]] = None) -> GitHubConnector:
    """Bind the connector instance + the async tool handler for the spec.

    Tests pass the fake terminal's loopback URL (and its host in the
    allowlist); production uses the defaults (api.github.com)."""
    global _connector
    _connector = GitHubConnector(
        base_url=base_url,
        policy=policy or ConnectorPolicy(timeout_s=30.0),
        credential_name=CREDENTIAL_NAME,
        allowlist_hosts=allowlist_hosts or DEFAULT_ALLOWLIST)
    _register(_connector)
    _tool_handlers[TOOL_SPEC] = _github_tool
    return _connector


def tool_record(allowlist_hosts: Optional[List[str]] = None) -> ToolRecord:
    """The registry record for this connector tool. An OPERATOR registers it
    (``reg.register(tool_record(), actor=...)``) — it is NOT auto-seeded:
    the D-129 phase-1 ceiling and the certify runbook stay at 100 tools."""
    hosts = allowlist_hosts or DEFAULT_ALLOWLIST
    return ToolRecord.model_validate({
        "tool_id": TOOL_ID, "version": SEED_VERSION, "name": TOOL_SPEC,
        "description": "GitHub issues terminal connector (list/get/create "
                       "issues, add comments) — the R-191 connectors "
                       "framework's first real user. Credential from the "
                       "per-tenant secret store; 401 revokes it; retries "
                       "only connection-level interruptions with a stable "
                       "task identity (Idempotency-Key).",
        "source": {"kind": "internal",
                   "uri": "agent_civilization/connectors/github.py"},
        "capabilities": ["github.issues", "issue_tracking"],
        "input_schema": {"type": "object",
                         "required": ["data"],
                         "properties": {"data": {
                             "type": "object", "required": ["op"],
                             "properties": {
                                 "op": {"enum": sorted(_OPS)},
                                 "owner": {"type": "string"},
                                 "repo": {"type": "string"},
                                 "number": {"type": "integer"},
                                 "title": {"type": "string"},
                                 "body": {"type": "string"}}}}},
        "output_schema": {"type": "object",
                          "properties": {"ok": {"type": "boolean"}}},
        "auth_scope": [], "tenant_scope": "request_tenant",
        "risk_class": "high",
        "side_effects": ["github:write"], "read_only": False,
        "idempotent": True, "requires_approval": True,
        "timeout_ms": 10_000,
        "network_allowlist": list(hosts), "health_status": "unknown",
        "test_status": "not_tested",
        "implementation_ref": "agent_civilization.connectors.github:_github_tool",
        "lifecycle_state": "registered",
    })


async def _github_tool(work: Dict[str, Any]) -> Dict[str, Any]:
    """The async tool handler the dispatcher's fallback finds. Maps every
    connector failure to the honest fixed-reason result (never a crash,
    never a fake success)."""
    from agent_civilization.core import tenancy
    if _connector is None:
        return {"ok": False, "error": "github connector is not registered"}
    data = (work or {}).get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error":
                "github tool refused: data (a dict with op, owner, repo) is required"}
    op = data.get("op")
    if not isinstance(op, str):
        return {"ok": False, "error": "github tool refused: data.op is required"}
    tenant = None
    try:
        tenant = tenancy.current_tenant()
    except Exception:  # noqa: BLE001 — no ambient tenant is a refusal below
        tenant = None
    if not tenant:
        return {"ok": False, "error":
                "github tool refused: no ambient tenant (SEC-02: connector "
                "calls are tenant-scoped)"}
    try:
        out = await _connector.call(op, data, tenant=tenant,
                                    task_id=work.get("task_id"))
        return {"ok": True, "tool_output": out}
    except ConnectorError as e:
        return {"ok": False, "error": str(e)}
