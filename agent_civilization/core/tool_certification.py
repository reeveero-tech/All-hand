"""R-175 batch 1 (D-125): the phased certification pipeline (100 -> 1,000 -> 10,000).

Scope (what this module IS — the 10K_TOOLS_SPEC_AR.md phased gates):

* ``PHASE_CEILINGS = (100, 1000, 10000)`` — the spec's milestones declared in
  one place: 100 certified core tools first, then 1,000 (connector packs), then
  10,000. The phase is an OPERATOR declaration (``phase=`` at the call, default
  1); advancing it is an owner decision, never automatic — the system does not
  get to claim 10K readiness without the operator saying so.
* ``certify()`` — the governed walk to ``certified`` through the EXISTING
  front doors (``record_test_outcome`` + ``update_status``; nothing here
  bypasses the registry's rules), with the PHASE GATE enforced FIRST and
  fail-closed: certifying beyond the current phase's ceiling is refused
  BEFORE any mutation, so a refused tool is never left half-walked.
* ``state_counts()`` — the spec's «يعلن النظام كل رقم منفصلًا»: every state
  number declared separately (lifecycle: discovered/registered/validated/
  certified/deprecated-compatible/disabled/failed; test status: not_tested/
  contract_pass/integration_pass/certified/failed/blocked), counting UNIQUE
  tool_ids (a tool's state is its latest version's state) — aliases cannot
  exist by construction (one ``implementation_ref`` = one ``tool_id``), and a
  discovery record is never counted as a certified tool.
* ``certify_batch()`` — SAFE STOP: sequential, each item atomic; on the FIRST
  error (a poisoned record, a schema refusal, an unavailable connector) the
  batch stops and returns a structured report — earlier items stay certified
  (reported honestly), later items are untouched, and nothing escapes as an
  exception. No silent continuation past a failure.

What it is NOT (honest limits): the phase gate is a pre-check in this module
(the registry itself is not phase-aware — a direct ``update_status`` call
bypasses it; the governed path is ``certify()``); no throughput benchmark yet
(the spec's phase-2 «benchmark البحث واختيار الأداة» — a later batch); no
automatic phase advancement; no execution — certification proves the recorded
evidence chain, not that the tool works right now (that is the health
checker's job).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from agent_civilization.core.tool_registry import (
    InvalidRecord, RegistryError, ToolRecord)

#: the spec's phased milestones: 100 -> 1,000 -> 10,000 certified tools.
PHASE_CEILINGS = (100, 1000, 10_000)

#: the governed test-status walk (D-120's graph; 'certified' demands the
#: smoke/contract/security markers in the evidence — enforced by the registry).
_WALK = ("contract_pass", "integration_pass", "certified")

#: lifecycle states a tool may be certified FROM (a discovery record is not
#: an executable tool; failed/disabled tools need an explicit operator
#: recovery first — never a silent re-certification).
_CERTIFIABLE_FROM = ("registered", "validated")


class PhaseCeilingRefused(RegistryError):
    """Certifying one more tool would exceed the declared phase's ceiling."""
    code = "phase_ceiling"


def _latest_versions(registry: Any) -> List[str]:
    """The search API is public and (since R-174 batch 2) untruncated with
    ``limit=None``; versions are monotonic per tool_id, so the max version's
    state IS the tool's state."""
    records = registry.search(include_disabled=True, limit=None)
    latest: Dict[str, ToolRecord] = {}
    for rec in records:
        cur = latest.get(rec.tool_id)
        if cur is None or _vkey(rec.version) >= _vkey(cur.version):
            latest[rec.tool_id] = rec
    return list(latest.values())


def _vkey(version: str) -> tuple:
    return tuple(int(p) for p in version.split("."))


def state_counts(registry: Any) -> Dict[str, Any]:
    """Every state number declared separately, counting UNIQUE tool_ids."""
    lifecycle: Dict[str, int] = {s: 0 for s in (
        "discovered", "registered", "validated", "certified",
        "deprecated-compatible", "disabled", "failed")}
    test: Dict[str, int] = {s: 0 for s in (
        "not_tested", "contract_pass", "integration_pass",
        "certified", "failed", "blocked")}
    tools = _latest_versions(registry)
    for rec in tools:
        lifecycle[rec.lifecycle_state] = lifecycle.get(rec.lifecycle_state, 0) + 1
        test[rec.test_status] = test.get(rec.test_status, 0) + 1
    return {"lifecycle": lifecycle, "test_status": test,
            "unique_tools": len(tools)}


def _certified_now(registry: Any) -> int:
    return state_counts(registry)["lifecycle"]["certified"]


def certify(registry: Any, tool_id: str, version: str, *, actor: str,
            evidence: str, phase: int = 1) -> ToolRecord:
    """The governed walk to ``certified``, behind the PHASE GATE.

    Fail-closed order: idempotency check -> PHASE GATE (before ANY mutation)
    -> lifecycle precondition -> the test-status walk -> the lifecycle move.
    A refusal never leaves the tool half-walked."""
    if not isinstance(phase, int) or not (1 <= phase <= len(PHASE_CEILINGS)):
        raise ValueError(f"phase must be 1..{len(PHASE_CEILINGS)}, got {phase!r}")
    rec = registry.get(tool_id, version)
    # idempotent: an already-certified tool re-certifies as itself and does
    # not consume a new phase slot (batch re-runs are safe).
    if rec.lifecycle_state == "certified" and rec.test_status == "certified":
        return rec
    ceiling = PHASE_CEILINGS[phase - 1]
    now = _certified_now(registry)
    if now + 1 > ceiling:
        raise PhaseCeilingRefused(
            f"phase {phase} ceiling is {ceiling} certified tools "
            f"(now {now}); advancing the phase is an owner decision "
            f"(10K_TOOLS_SPEC_AR.md) — not automatic")
    if rec.lifecycle_state not in _CERTIFIABLE_FROM:
        raise InvalidRecord(
            f"{tool_id}@{version} is {rec.lifecycle_state!r}: only "
            f"{_CERTIFIABLE_FROM} tools certify (a discovery record is not "
            f"an executable tool; failed/disabled need an explicit recovery)")
    if rec.lifecycle_state == "registered":
        rec = registry.update_status(tool_id, version, actor=actor,
                                     lifecycle_state="validated")
    for step in _WALK:
        if rec.test_status != step:
            rec = registry.record_test_outcome(tool_id, version, actor=actor,
                                               to=step, evidence=evidence)
    if rec.lifecycle_state != "certified":
        rec = registry.update_status(tool_id, version, actor=actor,
                                     lifecycle_state="certified")
    return rec


def certify_batch(registry: Any, items: Iterable[Dict[str, Any]], *,
                  actor: str, phase: int = 1) -> Dict[str, Any]:
    """Sequential certification with a SAFE STOP on the first error.

    Each item is ``{"tool_id": ..., "version": ..., "evidence": ...}``. The
    first failure (poisoned record, schema refusal, unavailable connector)
    STOPS the batch: earlier items stay certified (reported), later items are
    untouched, and the error is returned in the report — never raised, never
    silently continued past."""
    report: Dict[str, Any] = {"phase": phase, "certified": [], "stopped_at": None,
                              "error_type": None, "error": None}
    for item in items:
        try:
            certify(registry, item["tool_id"], item["version"], actor=actor,
                    evidence=item["evidence"], phase=phase)
        except Exception as exc:  # noqa: BLE001 — safe stop, never a crash
            report["stopped_at"] = {"tool_id": item.get("tool_id"),
                                     "version": item.get("version")}
            report["error_type"] = type(exc).__name__
            report["error"] = str(exc)
            return report
        report["certified"].append(f"{item['tool_id']}@{item['version']}")
    return report


__all__ = ["PHASE_CEILINGS", "PhaseCeilingRefused", "certify", "certify_batch",
           "state_counts"]
