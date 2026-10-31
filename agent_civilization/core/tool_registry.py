"""R-171 batch 1 — the unified tool registry (contract + in-process store).

Scope (what this module IS):

* ``ToolRecord`` — the canonical record contract, adopted from the draft
  ``docs/governance/roadmaps/revival-scale/specs/tool_registry_contract.schema.json``
  with these DECLARED changes (D-108): ``version`` must be semantic
  ``MAJOR.MINOR.PATCH`` (versioning needs an order); ``implementation_ref``
  is required (``module:attribute``) so an entry points at something real;
  ``input_schema``/``output_schema`` must at least declare ``type``.
* ``ToolRegistry`` — create / read / search / status update / remove, with
  uniqueness of ``(tool_id, version)``, immutable versions, monotonic
  versions per ``tool_id``, alias refusal (one ``implementation_ref`` = one
  ``tool_id``), a lifecycle graph, and an append-only audit trail whose
  entries carry the SHA-256 of the canonical record (auditable results).

What it is NOT (honest limits): this class is an in-process store (no
persistence, no multi-process sharing) — the durable host-shared variant is
``tool_registry_store.PersistentToolRegistry`` (R-171 batch 3, D-113), which
inherits every rule below unchanged; no execution, sandboxing or
permission enforcement (R-172); no importers (R-173); no routing (R-174);
``input_schema`` is checked structurally, not as a full JSON-Schema
meta-validation (no ``jsonschema`` dependency). A valid record proves
metadata shape only — never that the tool works or is safe.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import threading
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
IMPL_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")

RiskClass = Literal["low", "medium", "high", "critical"]
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
Lifecycle = Literal["discovered", "registered", "validated", "certified",
                    "disabled", "deprecated-compatible", "failed"]
Health = Literal["unknown", "healthy", "degraded", "unhealthy", "disabled"]
TestStatus = Literal["not_tested", "contract_pass", "integration_pass",
                     "certified", "failed", "blocked"]

# Allowed lifecycle moves. ``certified`` is reachable only from ``validated``
# and requires test_status ``certified`` (checked in update_status) — a
# registry write alone can never certify a tool.
LIFECYCLE_MOVES: Dict[str, Tuple[str, ...]] = {
    "discovered": ("registered", "failed", "disabled"),
    "registered": ("validated", "failed", "disabled"),
    "validated": ("certified", "failed", "disabled", "registered"),
    "certified": ("deprecated-compatible", "disabled", "failed"),
    "deprecated-compatible": ("disabled",),
    "failed": ("registered", "disabled"),
    "disabled": ("registered",),
}
INITIAL_STATES = ("discovered", "registered")
MUTABLE_FIELDS = ("health_status", "test_status", "lifecycle_state", "last_verified_at")


class ToolSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["internal", "mcp", "openapi", "sdk", "cli", "connector", "external"]
    uri: Optional[str] = None
    revision: Optional[str] = None


class ToolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(min_length=3, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=128)
    version: str
    source: ToolSource
    description: str = Field(min_length=1, max_length=8000)
    capabilities: List[str] = Field(min_length=1)
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    auth_scope: List[str]
    tenant_scope: Literal["request_tenant", "system_scoped", "explicit_admin_only", "none"]
    risk_class: RiskClass
    side_effects: List[str] = Field(default_factory=list)
    read_only: bool
    idempotent: bool
    requires_approval: bool
    timeout_ms: int = Field(ge=1, le=3_600_000)
    rate_limit: Optional[Dict[str, Any]] = None
    resource_budget: Optional[Dict[str, Any]] = None
    network_allowlist: List[str] = Field(default_factory=list)
    data_classification: List[str] = Field(default_factory=list)
    health_status: Health
    test_status: TestStatus
    last_verified_at: Optional[str] = None
    license: Optional[str] = None
    docs_uri: Optional[str] = None
    implementation_ref: str
    fallback_policy: Optional[str] = None
    telemetry_name: Optional[str] = None
    lifecycle_state: Lifecycle

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER.match(v):
            raise ValueError("version must be MAJOR.MINOR.PATCH")
        return v

    @field_validator("implementation_ref")
    @classmethod
    def _impl(cls, v: str) -> str:
        if not IMPL_REF.match(v):
            raise ValueError("implementation_ref must be 'module.path:attribute'")
        return v

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, v: List[str]) -> List[str]:
        if any(not c.strip() for c in v):
            raise ValueError("capabilities must be non-empty strings")
        if len(set(v)) != len(v):
            raise ValueError("capabilities must be unique")
        return v

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(v.get("type"), (str, list)):
            raise ValueError("schema must declare 'type' (structural check only)")
        return v

    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                               ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RegistryError(Exception):
    code = "registry_error"


class InvalidRecord(RegistryError):
    code = "invalid_record"


class DuplicateTool(RegistryError):
    code = "duplicate"


class VersionConflict(RegistryError):
    code = "version_conflict"


class AliasConflict(RegistryError):
    code = "alias"


class ToolNotFound(RegistryError):
    code = "not_found"


class TransitionRefused(RegistryError):
    code = "transition_refused"


def _vkey(version: str) -> Tuple[int, int, int]:
    a, b, c = version.split(".")
    return int(a), int(b), int(c)


def resolve_implementation(ref: str) -> Any:
    """Import ``module:attribute`` — proves the reference points at a real
    object. Says nothing about whether calling it works."""
    module, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module), attr)


class ToolRegistry:
    """Thread-safe in-process registry. Every mutation is audited."""

    def __init__(self, clock=time.time):
        self._lock = threading.RLock()
        self._records: Dict[Tuple[str, str], ToolRecord] = {}
        self._removed: Dict[Tuple[str, str], str] = {}    # tombstone -> digest
        self._impl_owner: Dict[str, str] = {}             # implementation_ref -> tool_id
        self._versions: Dict[str, List[str]] = {}         # tool_id -> versions (index)
        self._audit: List[Dict[str, Any]] = []
        self._clock = clock

    # -- audit -------------------------------------------------------------
    def _log(self, op: str, actor: str, tool_id: str, version: Optional[str],
             digest: Optional[str], **extra: Any) -> None:
        self._audit.append({"seq": len(self._audit) + 1, "ts": self._clock(), "op": op,
                            "actor": actor, "tool_id": tool_id, "version": version,
                            "digest": digest, **extra})

    def audit(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._audit]

    # -- create ------------------------------------------------------------
    def register(self, data: Dict[str, Any], *, actor: str) -> ToolRecord:
        if not actor:
            raise InvalidRecord("actor is required (audit)")
        try:
            rec = data if isinstance(data, ToolRecord) else ToolRecord.model_validate(data)
        except ValidationError as e:
            fields = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
            with self._lock:
                self._log("register_refused", actor, str(data.get("tool_id", ""))[:256]
                          if isinstance(data, dict) else "", None, None,
                          reason="invalid_record", fields=fields)
            raise InvalidRecord(f"invalid record: {fields}") from None
        if rec.lifecycle_state not in INITIAL_STATES:
            raise InvalidRecord(f"a new record starts in {INITIAL_STATES}, "
                                f"not {rec.lifecycle_state!r}")
        if rec.test_status == "certified":
            raise InvalidRecord("a new record cannot arrive certified")
        key = (rec.tool_id, rec.version)
        with self._lock:
            if key in self._records:
                self._log("register_refused", actor, *key, rec.digest(), reason="duplicate")
                raise DuplicateTool(f"{key} already registered (versions are immutable)")
            if key in self._removed and self._removed[key] != rec.digest():
                self._log("register_refused", actor, *key, rec.digest(),
                          reason="tombstone_content_changed")
                raise VersionConflict(f"{key} was removed; re-registering it with "
                                      "different content needs a new version")
            versions = self._versions.get(rec.tool_id, [])
            if versions and _vkey(rec.version) <= max(_vkey(v) for v in versions):
                self._log("register_refused", actor, *key, rec.digest(), reason="not_newer")
                raise VersionConflict(f"{rec.version} is not newer than the latest "
                                      f"version of {rec.tool_id}")
            owner = self._impl_owner.get(rec.implementation_ref)
            if owner is not None and owner != rec.tool_id:
                self._log("register_refused", actor, *key, rec.digest(), reason="alias",
                          owner=owner)
                raise AliasConflict(f"{rec.implementation_ref} already belongs to {owner}"
                                    " (an alias is not a new tool)")
            self._records[key] = rec
            self._versions.setdefault(rec.tool_id, []).append(rec.version)
            self._impl_owner[rec.implementation_ref] = rec.tool_id
            self._removed.pop(key, None)
            self._log("register", actor, *key, rec.digest())
            return rec

    # -- read --------------------------------------------------------------
    def get(self, tool_id: str, version: Optional[str] = None) -> ToolRecord:
        with self._lock:
            if version is not None:
                rec = self._records.get((tool_id, version))
                if rec is None:
                    raise ToolNotFound(f"{tool_id}@{version}")
                return rec
            versions = self._versions.get(tool_id)
            if not versions:
                raise ToolNotFound(tool_id)
            return self._records[(tool_id, max(versions, key=_vkey))]

    def versions(self, tool_id: str) -> List[str]:
        with self._lock:
            return sorted(self._versions.get(tool_id, []), key=_vkey)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def search(self, *, capability: Optional[str] = None, text: Optional[str] = None,
               max_risk: Optional[str] = None, read_only: Optional[bool] = None,
               lifecycle: Optional[Tuple[str, ...]] = None, latest_only: bool = True,
               include_disabled: bool = False, limit: int = 50) -> List[ToolRecord]:
        """Deterministic filter search (ordered by tool_id, then version).
        Exact capability match; ``text`` is a case-insensitive substring of
        name/description/capabilities. Not ranking (R-174)."""
        if max_risk is not None and max_risk not in RISK_ORDER:
            raise InvalidRecord(f"unknown risk class {max_risk!r}")
        needle = text.lower() if text else None
        with self._lock:
            recs = list(self._records.values())
        if latest_only:
            latest: Dict[str, ToolRecord] = {}
            for r in recs:
                cur = latest.get(r.tool_id)
                if cur is None or _vkey(r.version) > _vkey(cur.version):
                    latest[r.tool_id] = r
            recs = list(latest.values())
        out = []
        for r in sorted(recs, key=lambda r: (r.tool_id, _vkey(r.version))):
            if not include_disabled and r.lifecycle_state in ("disabled", "failed"):
                continue
            if capability is not None and capability not in r.capabilities:
                continue
            if max_risk is not None and RISK_ORDER[r.risk_class] > RISK_ORDER[max_risk]:
                continue
            if read_only is not None and r.read_only is not read_only:
                continue
            if lifecycle is not None and r.lifecycle_state not in lifecycle:
                continue
            if needle is not None and needle not in " ".join(
                    [r.name, r.description, *r.capabilities]).lower():
                continue
            out.append(r)
            if len(out) >= max(0, limit):
                break
        return out

    # -- update (operational fields only) ----------------------------------
    def update_status(self, tool_id: str, version: str, *, actor: str,
                      **changes: Any) -> ToolRecord:
        if not actor:
            raise InvalidRecord("actor is required (audit)")
        bad = sorted(set(changes) - set(MUTABLE_FIELDS))
        if bad:
            raise InvalidRecord(f"immutable fields {bad}: publish a new version instead")
        with self._lock:
            rec = self.get(tool_id, version)
            new_state = changes.get("lifecycle_state", rec.lifecycle_state)
            if new_state != rec.lifecycle_state and new_state not in LIFECYCLE_MOVES[
                    rec.lifecycle_state]:
                self._log("update_refused", actor, tool_id, version, rec.digest(),
                          reason="lifecycle", frm=rec.lifecycle_state, to=new_state)
                raise TransitionRefused(f"{rec.lifecycle_state} -> {new_state}")
            new_test = changes.get("test_status", rec.test_status)
            if new_state == "certified" and new_test != "certified":
                self._log("update_refused", actor, tool_id, version, rec.digest(),
                          reason="certified_without_certified_test")
                raise TransitionRefused("certified requires test_status 'certified'")
            try:
                updated = ToolRecord.model_validate({**rec.model_dump(), **changes})
            except ValidationError as e:
                fields = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
                raise InvalidRecord(f"invalid change: {fields}") from None
            self._records[(tool_id, version)] = updated
            self._log("update", actor, tool_id, version, updated.digest(),
                      prev_digest=rec.digest(), changed=sorted(changes))
            return updated

    # -- delete ------------------------------------------------------------
    def remove(self, tool_id: str, version: str, *, actor: str) -> None:
        """Removes one version; leaves a tombstone so the same key can only
        come back with the same content (no silent replacement)."""
        if not actor:
            raise InvalidRecord("actor is required (audit)")
        with self._lock:
            rec = self.get(tool_id, version)
            del self._records[(tool_id, version)]
            self._removed[(tool_id, version)] = rec.digest()
            remaining = [v for v in self._versions.get(tool_id, []) if v != version]
            if remaining:
                self._versions[tool_id] = remaining
            else:
                self._versions.pop(tool_id, None)
                self._impl_owner.pop(rec.implementation_ref, None)
            self._log("remove", actor, tool_id, version, rec.digest())
