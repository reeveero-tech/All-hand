"""R-174 — tool selection quality: retrieval, ranking, verified run, safe fallback.

Scope (what this module IS):

* ``TaskSpec`` — the caller's declaration of a task's tool needs: capability,
  risk ceiling, read-only demand, granted scopes, timeout budget, side-effect
  allowance, tenant-scope allowance and a prior-result quality signal. The
  MODEL never decides permissions — the spec comes from the task contract.
* ``select_tools()`` — candidate retrieval by exact capability from the real
  registry, constraint filtering where every violation is RECORDED with a
  reason (never silently dropped), and deterministic multi-criteria ranking:
  health, lifecycle, test status, risk class, read-only (privacy),
  prior-result quality, fewest scopes — with a lexicographic tool_id
  tiebreak. No randomness anywhere; the same registry state always yields
  the same order.
* ``run_selected()`` — the chosen candidate runs through the REAL dispatch
  gate (every R-171/R-172 wall applies: lifecycle, health, approval policy,
  scopes, network policy, timeout, output caps, redaction), the result is
  verified against the task's verification plan, and fallback to the next
  candidate happens ONLY when safe: the failed candidate never ran (gate
  refusal) or is read-only/idempotent AND the alternative is
  read-only/idempotent. A failed tool with side effects is NEVER silently
  retried past — the failure is surfaced.

Honest limits (what it is NOT):

* prior-result quality is OPERATOR-SUPPLIED (a callable over the record);
  the automatic telemetry feed (batch 4, D-124) lives one layer up in
  ``task_contract`` (``quality_source: "telemetry"`` builds the callable from
  the runtime's operational counters); ``TaskSpec`` itself stays agnostic;
* selections are not persisted and not counted beyond best-effort telemetry;
* the executed-counter delta is the "did it run" signal — under concurrent
  selections a refusal may be mis-attributed as a run, which only ever
  BLOCKS a fallback (the safe direction), never enables one;
* no model involvement, no auto-retry storm beyond top_k candidates, and no
  binding into the agent execution path yet (that is a later batch).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_civilization.core import tool_runtime
from agent_civilization.core.tool_registry import RISK_ORDER, ToolRecord

TOOL_PREFIX = tool_runtime.TOOL_PREFIX

# Deterministic preference orders (lower rank = better). Health dominates
# everything: a degraded tool never outranks a healthy one regardless of
# quality; lifecycle and test status follow (the governed certification
# pipeline is the source of both); then risk, read-only (privacy), quality,
# fewest scopes, and the tool_id tiebreak.
HEALTH_RANK: Dict[str, int] = {"healthy": 0, "unknown": 1, "degraded": 2,
                               "unhealthy": 3, "disabled": 4}
LIFECYCLE_RANK: Dict[str, int] = {"certified": 0, "validated": 1,
                                 "registered": 2, "deprecated-compatible": 3,
                                 "discovered": 4, "failed": 5, "disabled": 6}
TEST_RANK: Dict[str, int] = {"certified": 0, "integration_pass": 1,
                             "contract_pass": 2, "not_tested": 3, "blocked": 4,
                             "failed": 5}
RISK_RANK: Dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

EXECUTABLE_LIFECYCLE: Tuple[str, ...] = ("registered", "validated", "certified")
UNHEALTHY: Tuple[str, ...] = ("unhealthy", "disabled")


@dataclass(frozen=True)
class TaskSpec:
    """The caller's tool needs for one task (the model never decides these)."""

    capability: str
    top_k: int = 3
    # Conservative defaults: a medium risk ceiling and no side effects unless
    # the task explicitly allows them (privacy/safety first — callers raise
    # the ceiling deliberately when the task genuinely needs it).
    max_risk: str = "medium"
    read_only: Optional[bool] = None
    granted_scopes: Tuple[str, ...] = ()
    timeout_budget_ms: Optional[int] = None
    allow_side_effects: bool = False
    allowed_tenant_scopes: Optional[Tuple[str, ...]] = None
    # Prior-result quality (operator-supplied, e.g. from telemetry exports):
    # higher is better; a tiebreaker within equal health/lifecycle/test/risk.
    quality: Optional[Callable[[ToolRecord], float]] = None

    def __post_init__(self) -> None:
        if not self.capability or not self.capability.strip():
            raise ValueError("capability must be a non-empty string")
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1")
        if self.max_risk not in RISK_ORDER:
            raise ValueError(f"unknown risk class {self.max_risk!r}")
        if self.timeout_budget_ms is not None and self.timeout_budget_ms < 1:
            raise ValueError("timeout_budget_ms must be >= 1")


@dataclass(frozen=True)
class Selection:
    """The outcome of one selection: the ranked candidates and every exclusion
    with its reason (auditability — a prohibited tool is never silently
    dropped, it is recorded as excluded)."""

    task: TaskSpec
    ranked: List[ToolRecord] = field(default_factory=list)
    excluded: List[Tuple[str, str]] = field(default_factory=list)


def _violation(rec: ToolRecord, task: TaskSpec) -> Optional[str]:
    """Why this candidate is not selectable for this task (None = eligible)."""
    if rec.lifecycle_state not in EXECUTABLE_LIFECYCLE:
        return f"lifecycle_state={rec.lifecycle_state} (not executable)"
    if rec.health_status in UNHEALTHY:
        return f"health_status={rec.health_status}"
    if RISK_ORDER[rec.risk_class] > RISK_ORDER[task.max_risk]:
        return f"risk_class={rec.risk_class} above task ceiling {task.max_risk}"
    if task.read_only is not None and rec.read_only is not task.read_only:
        return f"read_only={rec.read_only} (task demands {task.read_only})"
    if (task.allowed_tenant_scopes is not None
            and rec.tenant_scope not in task.allowed_tenant_scopes):
        return f"tenant_scope={rec.tenant_scope} not allowed by task"
    if rec.side_effects and not task.allow_side_effects:
        return ("side effects not allowed by task: "
                + ",".join(rec.side_effects))
    if (task.timeout_budget_ms is not None
            and rec.timeout_ms > task.timeout_budget_ms):
        return (f"timeout_ms={rec.timeout_ms} above task budget "
                f"{task.timeout_budget_ms}")
    missing = [s for s in rec.auth_scope if s not in task.granted_scopes]
    if missing:
        return "scopes not granted: " + ",".join(missing)
    return None


def _rank_key(rec: ToolRecord, task: TaskSpec) -> Tuple[Any, ...]:
    quality = 0.0
    if task.quality is not None:
        try:
            quality = float(task.quality(rec))
        except Exception:            # noqa: BLE001 — a bad quality signal must
            quality = 0.0            # never corrupt the ranking, only flatten
    return (HEALTH_RANK[rec.health_status],
            LIFECYCLE_RANK[rec.lifecycle_state],
            TEST_RANK[rec.test_status],
            RISK_RANK[rec.risk_class],
            0 if rec.read_only else 1,
            -quality,
            len(rec.auth_scope),
            rec.tool_id)


def _telemetry(op: str, *, outcome: str, tool: str) -> None:
    try:
        from agent_civilization.core import telemetry
        telemetry.event("tool", op, outcome=outcome, tool=tool)
    except Exception:                # noqa: BLE001 — telemetry never blocks
        pass


def select_tools(registry: Any, task: TaskSpec) -> Selection:
    """Retrieve, filter (recording every exclusion) and rank candidates.

    Retrieval is intentionally BROAD (capability + latest version only) so
    that every constraint violation is visible in ``excluded`` — the registry
    search's own filters are not used for risk/lifecycle, this module owns
    the decision and its audit trail.

    R-174 batch 2 (D-122): retrieval is COMPLETE (``limit=None``) — the old
    ``limit=1000`` made every tool beyond the first 1000 tool_ids of a
    capability INVISIBLE to selection (red proof: a 1500-tool capability
    whose only eligible champion sorted last yielded an empty selection). A
    10K-tool catalog (R-175) must be fully visible to selection.
    """
    candidates = registry.search(capability=task.capability,
                                 include_disabled=True, limit=None)
    excluded: List[Tuple[str, str]] = []
    eligible: List[ToolRecord] = []
    for rec in candidates:
        reason = _violation(rec, task)
        if reason is None:
            eligible.append(rec)
        else:
            excluded.append((rec.tool_id, reason))
    ranked = sorted(eligible, key=lambda r: _rank_key(r, task))[:task.top_k]
    _telemetry("select", outcome="ok" if ranked else "fail",
               tool=ranked[0].tool_id if ranked else task.capability)
    return Selection(task=task, ranked=ranked, excluded=excluded)


# -- verified run with safe fallback -----------------------------------------

def _specialization_for(rec: ToolRecord) -> str:
    """The dispatch key for a record: internal tools are dispatched by their
    specialization; imported tools (mcp.*) are addressable by tool_id."""
    if rec.tool_id.startswith(TOOL_PREFIX):
        return rec.tool_id[len(TOOL_PREFIX):]
    return rec.tool_id


def _executed_snapshot() -> Dict[str, int]:
    try:
        return dict(tool_runtime.stats()["executed"])
    except Exception:                # noqa: BLE001 — stats must never break a run
        return {}


def _ran(before: Dict[str, int], rec: ToolRecord) -> bool:
    key = f"{rec.tool_id}@{rec.version}"
    return _executed_snapshot().get(key, 0) > before.get(key, 0)


def _verify_result(result: Dict[str, Any],
                   verify: Optional[Callable[[Dict[str, Any]], Any]]) -> bool:
    if verify is not None:
        try:
            return bool(verify(result))
        except Exception:            # noqa: BLE001 — a raising verify plan is
            return False             # a failed verification, not a crash
    return bool(result.get("ok"))


def _fallback_safe(failed: ToolRecord, alternative: ToolRecord, ran: bool) -> bool:
    """Fallback is allowed only when safe. If the failed candidate NEVER ran
    (a gate refusal), trying the next candidate is safe. If it DID run, the
    failed tool must be read-only/idempotent (no partial side effects to
    worry about) AND the alternative must be too — a failed tool with side
    effects is never silently retried past."""
    if not ran:
        return True
    if failed.read_only or failed.idempotent:
        return alternative.read_only or alternative.idempotent
    return False


async def run_selected(selection: Selection, work: Dict[str, Any],
                       *, verify: Optional[Callable[[Dict[str, Any]], Any]] = None
                       ) -> Dict[str, Any]:
    """Run the ranked candidates in order through the REAL gate, verify each
    result against the task's verification plan, and fall back to the next
    candidate only when safe (see ``_fallback_safe``).

    Returns ``{"ok": True, "tool", "result", "verified": True, "fallbacks"}``
    on the first verified success, or ``{"ok": False, "error", "fallbacks",
    "exhausted": True}`` when no candidate could deliver a verified result.
    """
    fallbacks: List[Dict[str, str]] = []
    last_error = "no candidate ran"
    for i, rec in enumerate(selection.ranked):
        before = _executed_snapshot()
        result = await tool_runtime.dispatch(_specialization_for(rec), dict(work))
        ran = _ran(before, rec)
        if result.get("ok"):
            if _verify_result(result, verify):
                _telemetry("select", outcome="ok", tool=rec.tool_id)
                return {"ok": True, "tool": rec.tool_id, "result": result,
                        "verified": True, "fallbacks": fallbacks}
            failure = "verification failed"
        else:
            failure = ("gate refusal (tool never ran)" if not ran
                       else "execution failure")
        last_error = str(result.get("error") or failure)
        nxt = selection.ranked[i + 1] if i + 1 < len(selection.ranked) else None
        if nxt is None:
            break
        if not _fallback_safe(rec, nxt, ran):
            break                    # surface the failure — never a silent
        fallbacks.append({"from": rec.tool_id, "to": nxt.tool_id,   # retry past
                          "reason": failure})
        _telemetry("fallback", outcome="ok", tool=nxt.tool_id)
    return {"ok": False, "error": last_error, "fallbacks": fallbacks,
            "exhausted": True, "verified": False}
