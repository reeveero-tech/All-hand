"""R-174 batch 3 (D-123): bind the tool selector into the agent execution path.

Scope (what this module IS):

* ``CONTRACT_KEY`` — the work-item key that carries a task's tool contract
  (``work["tool_contract"]``). The contract is the CALLER's declaration of
  the task's tool needs; the MODEL never decides permissions (same rule as
  ``TaskSpec`` itself, batch 1).
* ``derive_task_spec(work)`` — derive a ``TaskSpec`` from the contract
  (capability, top_k, risk ceiling, read-only demand, granted scopes,
  timeout budget, side-effect allowance, tenant scopes). Returns ``None``
  when the work carries no contract: the legacy dispatch path runs exactly
  as before (100% backward compatible — no env flag needed, the contract's
  PRESENCE is the opt-in). A MALFORMED contract raises ``ValueError`` — it
  must never silently downgrade a declared task to the legacy path.
* ``run_with_selection(spec, work)`` — select through the real registry and
  run through the real gate (``select_tools`` + ``run_selected``, batches
  1-2), then return the outcome AUGMENTED with the selection audit: the
  excluded candidates with their recorded reasons and the candidate count.
  When the contract declares tools nobody can satisfy, the failure names
  every exclusion — the legacy specialization handler is NEVER run for a
  contracted task (that would execute a tool the caller never allowed).

Honest limits (what it is NOT):

* the contract carries no ``quality`` callable — the operator-supplied
  telemetry feed is a later batch; ranking quality stays 0.0 for all;
* the verification plan stays the batch-1 shape (``verify=None`` — the
  runtime's R-170 output-verification gate still applies AROUND this
  outcome, both runtimes);
* one selection per work item — no multi-step planning.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from agent_civilization.core.tool_selector import TaskSpec, run_selected, select_tools
from agent_civilization.core import tool_runtime

CONTRACT_KEY = "tool_contract"

#: contract fields copied verbatim (validated by TaskSpec itself)
_LIST_FIELDS = ("granted_scopes", "allowed_tenant_scopes")


def derive_task_spec(work: Dict[str, Any]) -> Optional[TaskSpec]:
    """The TaskSpec declared by the work item's tool contract (None = no
    contract, legacy path). A malformed contract raises — never a silent
    fallback to a path the caller did not declare."""
    contract = (work or {}).get(CONTRACT_KEY)
    if contract is None:
        return None
    if not isinstance(contract, dict):
        raise ValueError(f"{CONTRACT_KEY} must be a dict, got {type(contract).__name__}")
    data = dict(contract)
    for f in _LIST_FIELDS:
        if data.get(f) is not None:
            if not isinstance(data[f], (list, tuple)):
                raise ValueError(f"{CONTRACT_KEY}.{f} must be a list, got {type(data[f]).__name__}")
            data[f] = tuple(data[f])
    # explicit contract fields only — anything unknown is a caller error, not
    # silently ignored permissions
    allowed = {"capability", "top_k", "max_risk", "read_only", "granted_scopes",
               "timeout_budget_ms", "allow_side_effects", "allowed_tenant_scopes"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"{CONTRACT_KEY} has unknown fields: {sorted(unknown)}")
    return TaskSpec(**data)


async def run_with_selection(spec: TaskSpec, work: Dict[str, Any]) -> Dict[str, Any]:
    """Select + run one contracted task and return the audited outcome.

    The outcome of ``run_selected`` plus the selection audit (every excluded
    candidate with its recorded reason, and how many candidates existed)."""
    registry = tool_runtime.runtime_registry()
    selection = select_tools(registry, spec)
    outcome = await run_selected(selection, dict(work))
    outcome["selection_excluded"] = [
        {"tool": tool_id, "reason": reason} for tool_id, reason in selection.excluded
    ]
    outcome["selection_candidates"] = len(selection.ranked)
    return outcome


__all__ = ["CONTRACT_KEY", "derive_task_spec", "run_with_selection"]
