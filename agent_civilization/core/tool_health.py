"""R-171 batch 4 (D-114) — a REAL health check for registered tools.

Before this batch ``health_status`` was only as current as whoever set it by
hand (the batch-2 gate reads it, but nothing measured it — the declared limit
was "no health checker yet"). This module closes that gap for the internal
tools, conservatively and honestly:

* every registered tool gets a STATIC availability check — the handler exists
  in the frozen dispatcher, it is the object the record's
  ``implementation_ref`` names, and its lifecycle is executable (the same
  facts the dispatch gate checks, so a tool the gate would refuse can never
  be reported healthy);
* a tool is EXECUTION-probed only when it is safe to do so without new
  dependencies or side effects: ``read_only`` and risk class ``low`` and no
  declared side effects (no model-provider calls, no network, no sandboxed
  execution). The probe runs the REAL handler through the REAL gate
  (``tool_runtime.dispatch``) with a deterministic smoke input and requires an
  ``ok``-shaped answer — that is an actual execution, not a metadata claim;
* tools with declared side effects (network, model, exec, local metrics) are
  NOT executed by the health checker: their health is the static result only.

Health semantics (written to ``health_status``, audited via ``update_status``
with actor ``health:checker``):

* ``healthy``  — the execution probe ran and passed (probe=execution);
* ``degraded`` — static availability only: the tool is callable, but its
  health was not proven by execution (side effects make a probe unsafe);
* ``unhealthy``— the static check failed or the execution probe failed;
* ``unknown``  — not yet checked. ``disabled`` lifecycle tools are SKIPPED
  (not probed, not overwritten): disabling is an operator decision, and the
  gate already refuses them.

Declared limits: the probe input is one deterministic smoke input, so
``healthy`` means "ran and answered ok on the probe", not "correct for every
input" (correctness evidence is R-175 certification); the checker is invoked
explicitly (CLI/admin API), it is not a background daemon; multi-host stores
are a deployment decision (see OPERATIONS).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent_civilization.core import tool_runtime
from agent_civilization.core.tool_registry import ToolRegistry
from agent_civilization.core.tool_registry_store import RegistryStoreError

HEALTH_ACTOR = "health:checker"
# Deterministic smoke inputs per specialization. The craft-based handlers
# HONESTLY refuse vague input (an R-170 anti-hollow-success protection:
# "no code provided", "no objectives listed", "no component named"), so a
# single generic probe would misreport them as broken. Each input below is
# the minimum the tool's contract accepts; every one of them is executed for
# real by the tests. A tool without a specific input falls back to the generic
# one.
GENERIC_PROBE_WORK: Dict[str, Any] = {"prompt": "registry health probe",
                                     "goal": "registry health probe"}
_CODE = "def add(a, b):\n    return a + b"
PROBE_WORK: Dict[str, Dict[str, Any]] = {
    "analyst": {"prompt": "health probe", "goal": "health probe"},
    "builder": {"prompt": "health probe", "goal": "health probe"},
    "data_processor": {"data": [1, 2, 3]},
    "validator": {"data": {"a": 1}},
    "surgeon": {"data": {"code": _CODE}},
    "reviewer": {"data": {"code": _CODE}},
    "architect": {"prompt": "design an api gateway with a postgres database",
                  "goal": "design an api gateway with a postgres database"},
    "strategist": {"data": ["objective one", "objective two"]},
    "designer": {"prompt": "design a dark blue palette with high contrast",
                 "goal": "design a dark blue palette with high contrast"},
}


def probe_work_for(spec: str) -> Dict[str, Any]:
    return dict(PROBE_WORK.get(spec, GENERIC_PROBE_WORK))

def _spec_of(rec) -> str:
    """The specialization name of a record (inverse of ``tool_id_for``)."""
    return rec.tool_id[len(tool_runtime.TOOL_PREFIX):] \
        if rec.tool_id.startswith(tool_runtime.TOOL_PREFIX) else rec.name


def is_probeable(rec) -> bool:
    """Whether this record may be EXECUTION-probed by the health checker:
    read-only, low risk, and NO declared side effects of any kind (model
    provider, network, sandboxed execution, local metrics, in-memory stores) —
    any effect makes an execution probe unsafe, so the tool is static-only."""
    return bool(rec.read_only) and rec.risk_class == "low" and not rec.side_effects


def _static_check(rec) -> (bool, str):
    """The gate's own facts: would the dispatch gate let this tool run?"""
    return tool_runtime.check(_spec_of(rec))


async def check_tool(rec) -> Dict[str, Any]:
    """One tool: static availability, plus a real execution when safe.

    Returns ``{"probe": "execution"|"static"|"skipped", "status": ...,
    "detail": ...}``. Does NOT write to the registry (see ``run_health_check``).
    """
    if rec.lifecycle_state == "disabled":
        return {"probe": "skipped", "status": "skipped_disabled",
                "detail": "lifecycle_state=disabled — an operator decision; not probed"}
    ok, reason = _static_check(rec)
    if not ok:
        return {"probe": "static", "status": "unhealthy", "detail": reason}
    if not is_probeable(rec):
        return {"probe": "static", "status": "degraded",
                "detail": "available (static check only): declared side effects "
                          "make an execution probe unsafe"}
    # Real execution through the real gate — the same path production uses.
    spec = _spec_of(rec)
    try:
        result = await tool_runtime.dispatch(spec, probe_work_for(spec))
    except Exception as e:  # noqa: BLE001 — a probe crash is a health fact
        return {"probe": "execution", "status": "unhealthy",
                "detail": f"probe raised: {type(e).__name__}: {e}"}
    if not isinstance(result, dict) or "ok" not in result:
        return {"probe": "execution", "status": "unhealthy",
                "detail": f"probe returned a non-result: {result!r}"[:300]}
    if result.get("ok") is not True:
        return {"probe": "execution", "status": "unhealthy",
                "detail": f"probe failed: {result.get('error', 'no error field')}"[:300]}
    return {"probe": "execution", "status": "healthy",
            "detail": "probe executed through the dispatch gate and answered ok"}


_STATUS_TO_HEALTH = {"healthy": "healthy", "degraded": "degraded",
                     "unhealthy": "unhealthy"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def run_health_check(reg: Optional[ToolRegistry] = None,
                           actor: str = HEALTH_ACTOR) -> Dict[str, Any]:
    """Probe every registered tool and make ``health_status`` reflect reality.

    Only changed health values are written (each write is audited); the
    returned report always carries every tool's result and probe type. Raises
    ``RegistryStoreError`` when the store is unavailable (fail-closed).
    """
    reg = reg or tool_runtime.runtime_registry()
    report: Dict[str, Any] = {"actor": actor, "checked_at": _now(),
                              "tools": {}, "summary": {}}
    for rec in reg.search(include_disabled=True, limit=10_000):
        entry = await check_tool(rec)
        report["tools"][f"{rec.tool_id}@{rec.version}"] = entry
        if entry["status"] in _STATUS_TO_HEALTH:
            health = _STATUS_TO_HEALTH[entry["status"]]
            if rec.health_status != health:
                reg.update_status(rec.tool_id, rec.version, actor=actor,
                                  health_status=health, last_verified_at=_now())
            else:
                report["tools"][f"{rec.tool_id}@{rec.version}"]["unchanged"] = True
    counts: Dict[str, int] = {}
    for entry in report["tools"].values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    report["summary"] = counts
    return report


def _cli(argv: Optional[List[str]] = None) -> int:
    """``python -m agent_civilization.core.tool_health`` — operator entry point."""
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="Run the R-171 tool health check")
    ap.add_argument("--json", action="store_true", help="print the raw report")
    args = ap.parse_args(argv)
    try:
        report = asyncio.run(run_health_check())
    except RegistryStoreError as e:
        print(f"tool registry store unavailable: {getattr(e, 'reason', e)}")
        return 3
    if args.json:
        print(_json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for key, entry in sorted(report["tools"].items()):
            print(f"{key}: {entry['status']} ({entry['probe']}) — {entry['detail']}")
        print("summary:", report["summary"])
    return 0


if __name__ == "__main__":  # pragma: no cover — operator convenience
    raise SystemExit(_cli())
