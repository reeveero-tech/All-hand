"""R-171 batch 2 (D-112) — the registry governs the real execution path.

``dispatch(spec, work)`` is THE way a specialist handler runs: every caller
(``real_agents``, the ``base_agent`` wrapper, the specialist templates) uses
it instead of ``real_dispatcher.dispatch`` (an AST test forbids any other
import). Before calling the frozen dispatcher it resolves the tool
``agos.internal.<spec>`` in the process-wide registry and REFUSES, without
running the handler (fail-closed), when:

1. the handler exists but has no registry record (unregistered = not runnable);
2. the record's ``lifecycle_state`` is not executable (``discovered``,
   ``failed``, ``disabled``) or its ``health_status`` is ``disabled``;
3. the handler about to run is not the object the record's
   ``implementation_ref`` names (a swapped handler cannot borrow a record);
4. for ``self_developer``: any tool its self-test reaches is not executable
   (its probes call the handlers directly inside the frozen dispatcher, so a
   disabled tool would otherwise still run there).

A refusal is ``{"ok": False, "error": "tool <id>@<version> ..."}`` — the same
shape as a handler failure. An unknown specialization (no handler, no record)
keeps the frozen ``None`` contract. An executable tool returns exactly what
``real_dispatcher.dispatch`` returns (its telemetry span and exception
handling stay in the frozen code).

Operational control: ``AGOS_DISABLED_TOOLS`` (comma-separated tool ids or
specialization names) disables tools when the registry is seeded (first use
in the process; audited with actor ``env:AGOS_DISABLED_TOOLS``). Unknown
names are reported in ``stats()["unknown_disabled"]`` and logged.

Limits (declared): the registry is per process (no persistence, no sharing
between workers; a change through ``runtime_registry()`` affects this process
only, and the env is read at seeding — a restart applies a new value); a
check is not a sandbox or a permission system (R-172); ``health_status`` is
only as current as whoever sets it (no health checker yet).
"""
from __future__ import annotations

import logging
import os
import threading
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.tool_registry import ToolNotFound, ToolRegistry, resolve_implementation

logger = logging.getLogger("agos.tool_runtime")

TOOL_PREFIX = "agos.internal."
EXECUTABLE_LIFECYCLES = ("registered", "validated", "certified", "deprecated-compatible")
ENV_DISABLED = "AGOS_DISABLED_TOOLS"
ENV_ACTOR = f"env:{ENV_DISABLED}"

_lock = threading.Lock()
_registry: Optional[ToolRegistry] = None
_executed: Counter = Counter()
_refused: Counter = Counter()
_unknown_disabled: List[str] = []


def tool_id_for(spec: str) -> str:
    return f"{TOOL_PREFIX}{spec}"


def _seed() -> ToolRegistry:
    from agent_civilization.core.internal_tools import seeded_registry
    reg = seeded_registry()
    raw = os.environ.get(ENV_DISABLED, "")
    unknown: List[str] = []
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        tid = item if item.startswith(TOOL_PREFIX) else tool_id_for(item)
        try:
            rec = reg.get(tid)
        except ToolNotFound:
            unknown.append(item)
            continue
        if rec.lifecycle_state != "disabled":
            reg.update_status(tid, rec.version, actor=ENV_ACTOR, lifecycle_state="disabled")
    if unknown:
        logger.warning("%s: unknown tool(s) ignored: %s", ENV_DISABLED, ", ".join(unknown))
    _unknown_disabled[:] = unknown
    return reg


def runtime_registry() -> ToolRegistry:
    """The process-wide registry the execution path consults (seeded lazily)."""
    global _registry
    with _lock:
        if _registry is None:
            _registry = _seed()
        return _registry


def reset_runtime_registry() -> None:
    """Forget the registry and the counters (tests; re-reads the env)."""
    global _registry
    with _lock:
        _registry = None
        _executed.clear()
        _refused.clear()
        _unknown_disabled.clear()


def _own_check(reg: ToolRegistry, spec: str) -> Tuple[bool, str, str]:
    """(ok, reason, key) for one tool, without the reach rule."""
    tid = tool_id_for(spec)
    try:
        rec = reg.get(tid)
    except ToolNotFound:
        return False, f"tool {tid} is not registered (unregistered tools do not run)", tid
    key = f"{tid}@{rec.version}"
    if rec.lifecycle_state not in EXECUTABLE_LIFECYCLES:
        return False, f"tool {key} is not executable: lifecycle_state={rec.lifecycle_state}", key
    if rec.health_status == "disabled":
        return False, f"tool {key} is not executable: health_status=disabled", key
    handler = real_dispatcher._HANDLERS.get(spec)
    try:
        expected = resolve_implementation(rec.implementation_ref)
    except Exception:  # noqa: BLE001 — an unresolvable reference is a refusal
        expected = None
    if handler is None or handler is not expected:
        return False, (f"tool {key} refused: the handler to run is not the registered "
                       f"implementation ({rec.implementation_ref})"), key
    return True, "", key


def _reached(spec: str) -> List[str]:
    if spec == "self_developer":
        return sorted(s for s in getattr(real_dispatcher, "_SELF_PROBES", {}) if s != spec)
    return []


def check(spec: str) -> Tuple[bool, str]:
    """Whether ``spec``'s tool may run now, and why not."""
    reg = runtime_registry()
    ok, reason, _ = _own_check(reg, spec)
    if not ok:
        return ok, reason
    for other in _reached(spec):
        ok2, reason2, _ = _own_check(reg, other)
        if not ok2:
            return False, f"tool {tool_id_for(spec)} refused: it reaches a non-executable tool — {reason2}"
    return True, ""


async def dispatch(specialization: str, work: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Gate, then run the frozen dispatcher (see the module docstring)."""
    if specialization not in real_dispatcher._HANDLERS:
        try:
            runtime_registry().get(tool_id_for(specialization))
        except ToolNotFound:
            return None                     # unknown specialization: frozen contract
    reg = runtime_registry()
    ok, reason = check(specialization)
    _, _, key = _own_check(reg, specialization)
    if not ok:
        with _lock:
            _refused[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "refused", outcome="fail", tool=specialization)
        except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
            pass
        logger.info("tool refused: %s", reason)
        return {"ok": False, "error": reason}
    with _lock:
        _executed[key] += 1
    return await real_dispatcher.dispatch(specialization, work)


def stats() -> Dict[str, Any]:
    with _lock:
        return {"executed": dict(_executed), "refused": dict(_refused),
                "unknown_disabled": list(_unknown_disabled)}


def coverage() -> Dict[str, Any]:
    """The frozen dispatcher's coverage (re-exported for callers of this gate)."""
    return real_dispatcher.coverage()


__all__ = ["EXECUTABLE_LIFECYCLES", "ENV_DISABLED", "check", "coverage", "dispatch",
           "reset_runtime_registry", "runtime_registry", "stats", "tool_id_for"]
