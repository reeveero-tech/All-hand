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

Durable and shared (R-171 batch 3, D-113): the registry is a
``PersistentToolRegistry`` on the host store (``AGOS_TOOL_REGISTRY_DB``), so
a change made by any worker — or by the operator CLI
``scripts/tool_registry_admin.py`` — reaches every worker's next check
without a restart, and survives restarts with its audit trail. If the store
is unavailable or fails its integrity check, every check REFUSES
(fail-closed) with ``tool registry store unavailable``.

Operational control: ``AGOS_DISABLED_TOOLS`` (comma-separated tool ids or
specialization names) disables tools when a process seeds (audited with actor
``env:AGOS_DISABLED_TOOLS``; written once, not per worker). The disable is
DURABLE: removing a name from the env does not re-enable it — use
``tool_registry_admin.py enable``. Unknown names are reported in
``stats()["unknown_disabled"]`` and logged.

Limits (declared): host-local store (multi-host is a deployment decision); a
check is not a sandbox (execution isolation is a later R-172 batch). R-172
batch 1 (D-115) added GATE-level enforcement of the record's own
``requires_approval`` and ``timeout_ms``. R-172 batch 2 (D-116) enforces the
rest of the declared metadata at this gate: ``auth_scope`` (least privilege —
the work item must grant every scope the record demands), the per-tool
network policy (a tool whose record declares no network side effect and no
allowlist cannot use sockets while it runs — see ``_network_allowed``), and
output caps (a result larger than the record's
``resource_budget['max_output_bytes']`` — or the operator default
``AGOS_TOOL_MAX_OUTPUT_BYTES`` — is refused and withheld, counted in
``stats()['output_capped']``). The network block is per-TASK (an audit hook
gated by a ``contextvars`` flag), so concurrent tasks are untouched; threads
spawned by a handler get a fresh context and are NOT covered (declared
residual).

R-172 batch 3 (D-117) closes those declared residuals where the record asks
for it, and sanitizes the failure channel:

* **Execution isolation** — a record whose ``resource_budget['isolation']``
  is ``"process"`` runs in a SPAWNED WORKER (fresh interpreter, ``spawn``
  context), not in this process. The worker enforces the tool's own
  declarations with real teeth: the network policy is installed
  PROCESS-WIDE in the worker (so threads the handler spawns ARE covered —
  the batch-2 residual), the address space is capped
  (``resource_budget['max_memory_bytes']``, default 512MB, via
  ``RLIMIT_AS``), the registered ``timeout_ms`` kills the worker, and a
  crashing handler (``os._exit``, segfault, OOM-kill) cannot take the host
  down (counted in ``stats()['isolated_crashed']``). Runs are counted in
  ``stats()['isolated']``. Fail-closed: a worker that cannot start or that
  dies before answering returns an honest error dict, never a hang.
* **Approval policy** — ``requires_approval`` is no longer just "someone
  typed a name": the record can demand ``approval_ttl_s`` (the work item's
  ``approval['granted_at']`` must be ISO-8601 and fresh — an old or missing
  timestamp is refused), ``approval_scopes`` (the APPROVER must hold them in
  ``approval['scopes']`` — distinct from ``work['scopes']`` which is what
  the TOOL may touch) and honors tool binding (``approval['tool']``, when
  present, must name THIS tool — an approval granted for another tool
  cannot be replayed here). Malformed values are refused, not ignored.
* **Sanitized failure channel** — every ``error`` text that crosses this
  gate (handler results and isolated-worker failures alike) passes through
  ``public_text.redact``: credentials inside a failure message (bearer
  tokens, JWTs, key formats, URL userinfo) are replaced by ``<redacted>``
  before the result moves on. Non-secret diagnostics are preserved.

``health_status`` is set by the R-171 batch-4 health checker
(``core/tool_health``), on demand — not continuously.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import multiprocessing
import os
import resource
import sys
import threading
import importlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.tool_registry import ToolNotFound, ToolRegistry, resolve_implementation
from agent_civilization.core.tool_registry_store import (
    PersistentToolRegistry,
    RegistryStoreError,
    default_db_path,
)

logger = logging.getLogger("agos.tool_runtime")

TOOL_PREFIX = "agos.internal."
EXECUTABLE_LIFECYCLES = ("registered", "validated", "certified", "deprecated-compatible")
ENV_DISABLED = "AGOS_DISABLED_TOOLS"
ENV_ACTOR = f"env:{ENV_DISABLED}"

_lock = threading.Lock()
_registry: Optional[ToolRegistry] = None
_executed: Counter = Counter()
_refused: Counter = Counter()
_timed_out: Counter = Counter()
_net_blocked: Counter = Counter()
_output_capped: Counter = Counter()
_isolated: Counter = Counter()
_isolated_crashed: Counter = Counter()
_unknown_disabled: List[str] = []
_seed_drift: List[str] = []
STORE_UNAVAILABLE = "tool registry store unavailable"


def tool_id_for(spec: str) -> str:
    return f"{TOOL_PREFIX}{spec}"


class _HandlerRaisedTimeout(Exception):
    """A ``TimeoutError`` raised BY the handler itself (an internal dependency
    timing out — the R-149 deterministic case), NOT the gate's own budget
    expiring. Since Python 3.11 ``asyncio.TimeoutError`` IS the builtin
    ``TimeoutError``, the gate cannot tell them apart at the ``except`` site —
    so the guarded call re-raises the handler's timeout as THIS distinct type,
    and the gate re-raises the original untouched (the task layer sanitizes it
    to the type only; the gate must never mislabel a handler failure as its
    own budget, nor leak the handler's text)."""


def _valid_approval(rec: Any, work: Any) -> Tuple[bool, str]:
    """R-172 batch 1 (D-115) established the minimal honest approval contract
    — ``work['approval'] = {'actor': <non-empty str>}``. Batch 3 (D-117)
    makes the RECORD decide the policy: ``approval_ttl_s`` demands a fresh
    ISO-8601 ``approval['granted_at']``; ``approval_scopes`` demands the
    APPROVER hold them in ``approval['scopes']`` (the approver's authority —
    distinct from ``work['scopes']``, what the TOOL may touch); and an
    approval that names a ``tool`` binds to that tool (no replay across
    tools). Malformed values are refused, not ignored (fail-closed).

    Returns ``(ok, reason)``; the reason is for the refusal message (tool id
    and policy facts only — never the work payload)."""
    if not isinstance(work, dict):
        return False, "no approval in the work item"
    ap = work.get("approval")
    if not isinstance(ap, dict):
        return False, "no approval in the work item"
    actor = ap.get("actor")
    if not (isinstance(actor, str) and actor.strip()):
        return False, "approval.actor (who approved) is required"
    bound = ap.get("tool")
    if bound is not None and bound != getattr(rec, "tool_id", None):
        return False, f"approval is bound to tool {bound}, not this tool"
    ttl = getattr(rec, "approval_ttl_s", None)
    if ttl is not None:
        granted = ap.get("granted_at")
        if not isinstance(granted, str) or not granted.strip():
            return False, (f"approval_ttl_s={ttl} is set — "
                           "approval.granted_at (ISO-8601) is required")
        try:
            ts = datetime.fromisoformat(granted.replace("Z", "+00:00"))
        except ValueError:
            return False, "approval.granted_at is not a valid ISO-8601 timestamp"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age < 0 or age > ttl:
            return False, (f"approval is stale — older than the registered "
                           f"approval_ttl_s={ttl}s")
    need = list(getattr(rec, "approval_scopes", None) or ())
    if need:
        have = ap.get("scopes")
        if not (isinstance(have, list)
                and all(isinstance(s, str) for s in have)):
            return False, ("approval_scopes set — approval.scopes (list of "
                           "strings held by the approver) is required")
        missing = [s for s in need if s not in have]
        if missing:
            return False, (f"approver lacks required approval scopes: "
                           f"{', '.join(missing)}")
    return True, ""


# -- R-172 batch 2 (D-116): the rest of the declared metadata -----------------

# side effects that legitimately use the network: the model-provider call
# and direct external HTTP — the contract's own constants (internal_tools.
# MODEL / internal_tools.NET; a test pins the equality).
_NETWORK_SIDE_EFFECTS = frozenset({"model_provider_call", "external_http_read"})

# audit events blocked while a no-network tool runs. connect/bind raise in the
# CALLING thread (the tool's own task context); getaddrinfo covers DNS.
_BLOCKED_NET_EVENTS = frozenset(
    {"socket.connect", "socket.getaddrinfo", "socket.bind"})

_net_forbidden: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agos_net_forbidden", default="")
_hook_lock = threading.Lock()
_hook_installed = False


def _install_network_audit_hook() -> None:
    """Install (once per process) the audit hook that makes the per-task
    network policy REAL: socket operations raise while the current task's
    context marks a no-network tool. The hook is process-wide but the
    decision is per-context, so concurrent tasks (other tools, the server's
    own calls) are untouched; the exception surfaces inside the frozen
    dispatcher's handler call and becomes its honest error dict."""
    global _hook_installed
    if _hook_installed:
        return
    with _hook_lock:
        if _hook_installed:
            return

        def _hook(event: str, args: object) -> None:
            if event in _BLOCKED_NET_EVENTS:
                who = _net_forbidden.get()
                if who:
                    raise RuntimeError(
                        f"network use blocked for tool {who}: its record "
                        "declares no network side effect")

        sys.addaudithook(_hook)
        _hook_installed = True


def _network_allowed(rec: Any) -> bool:
    """A tool may use the network when its record SAYS it does: a declared
    network side effect (model provider / external HTTP) or a non-empty
    allowlist. Everything else — pure local transforms, the code sandbox —
    is blocked while it runs (fail-closed against a swapped handler)."""
    effects = set(getattr(rec, "side_effects", None) or ())
    return bool(getattr(rec, "network_allowlist", None)) or bool(
        effects & _NETWORK_SIDE_EFFECTS)


def _default_max_output_bytes() -> int:
    try:
        return max(1, int(os.getenv("AGOS_TOOL_MAX_OUTPUT_BYTES", "1000000")))
    except ValueError:
        return 1_000_000


def _output_cap(rec: Any) -> int:
    """The record's own ``resource_budget['max_output_bytes']`` when it
    declares one, else the operator default — a cap ALWAYS applies."""
    budget = getattr(rec, "resource_budget", None)
    if (isinstance(budget, dict) and isinstance(budget.get("max_output_bytes"), int)
            and budget["max_output_bytes"] > 0):
        return budget["max_output_bytes"]
    return _default_max_output_bytes()


def _result_size(result: Any) -> int:
    try:
        return len(json.dumps(result, default=str))
    except (TypeError, ValueError):
        return len(repr(result))


def _granted_scopes(work: Any) -> set:
    scopes = work.get("scopes") if isinstance(work, dict) else None
    # the documented contract is a LIST of scope strings (the work item is
    # JSON-shaped); anything else grants nothing (fail-closed).
    if not isinstance(scopes, list):
        return set()
    return {s for s in scopes if isinstance(s, str)}


def _missing_scopes(rec: Any, work: Any) -> List[str]:
    required = {s for s in (getattr(rec, "auth_scope", None) or [])
                if isinstance(s, str)}
    return sorted(required - _granted_scopes(work))


# -- R-172 batch 3 (D-117): process isolation, approval policy, redaction ---

# default address-space cap for an isolated worker whose record does not
# declare one (resource_budget['max_memory_bytes']).
_ISOLATION_DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024


def _wants_isolation(rec: Any) -> bool:
    """The record opts into execution isolation via
    ``resource_budget['isolation'] == 'process'`` — a per-tool declaration,
    never a global default."""
    budget = getattr(rec, "resource_budget", None)
    return isinstance(budget, dict) and budget.get("isolation") == "process"


def _isolation_memory(rec: Any) -> int:
    budget = getattr(rec, "resource_budget", None)
    if (isinstance(budget, dict)
            and isinstance(budget.get("max_memory_bytes"), int)
            and budget["max_memory_bytes"] > 0):
        return budget["max_memory_bytes"]
    return _ISOLATION_DEFAULT_MEMORY_BYTES


def _sanitize_result(result: Any) -> Any:
    """R-172 batch 3: the failure channel is sanitized AT THE GATE — any
    ``error`` text crossing back from a handler (or an isolated worker) is
    passed through ``public_text.redact``: credentials inside a failure
    message are replaced by ``<redacted>``; non-secret diagnostics are
    preserved. Applied to the error TEXT only — result payloads are the
    tool's own contract (sanitizing them needs semantic knowledge the gate
    does not have; declared limit)."""
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        try:
            from agent_civilization.core import public_text
            result["error"] = public_text.redact(result["error"])
        except Exception:  # noqa: BLE001 — never break the result contract
            pass
    return result


def _isolated_child(handler_ref: str, work: Dict[str, Any],
                    net_block: bool, mem_bytes: int, conn: Any) -> None:
    """The spawned worker's entry point (must be module-level: ``spawn``
    pickles the target by reference). Runs in a FRESH interpreter:

    * the network policy, when the record declares no network, is installed
      PROCESS-WIDE here (``sys.addaudithook`` fires in every thread of this
      process — closing the batch-2 residual where a handler-spawned thread
      escaped the parent's per-task ContextVar gate);
    * the address space is capped (``RLIMIT_AS``) BEFORE the handler runs;
    * the handler is resolved by its dotted reference (``module:qualname``)
      so the worker runs exactly the handler the gate resolved — the same
      object identity the record's ``implementation_ref`` names;
    * the result (or the failure text) goes back over the pipe; a worker
      that dies before answering is the parent's crash-containment case."""
    if net_block:
        def _hook(event: str, args: object) -> None:
            if event in _BLOCKED_NET_EVENTS:
                raise RuntimeError(
                    "network use blocked in the isolated worker: the tool's "
                    "record declares no network side effect")
        sys.addaudithook(_hook)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except (ValueError, OSError):
        pass                      # no cap on this platform: run uncapped
    module, _, qual = handler_ref.partition(":")
    try:
        fn = getattr(importlib.import_module(module), qual)
    except Exception as e:  # noqa: BLE001 — surfaced honestly to the parent
        conn.send(("error", f"handler resolution failed: {type(e).__name__}: {e}"))
        return
    try:
        res = asyncio.run(fn(work))
        conn.send(("ok", res))
    except Exception as e:  # noqa: BLE001 — the worker's honest failure text
        conn.send(("error", f"{type(e).__name__}: {e}"))


async def _run_isolated(key: str, specialization: str, rec: Any,
                        work: Dict[str, Any]) -> Any:
    """Run a record-declared ``isolation: 'process'`` tool in a spawned
    worker. The parent stays responsive (``to_thread``), owns the timeout
    (kill + join on the registered ``timeout_ms``), and contains crashes: a
    dead worker becomes an honest error dict here, never a hang and never
    a dead host."""
    fn = real_dispatcher._HANDLERS.get(specialization)
    if fn is None:
        return {"ok": False,
                "error": f"tool {key} refused: no handler to isolate"}
    handler_ref = f"{getattr(fn, '__module__', '')}:{getattr(fn, '__qualname__', '')}"
    with _lock:
        _isolated[key] += 1
    net_block = not _network_allowed(rec)
    mem_bytes = _isolation_memory(rec)
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe()
    try:
        proc = ctx.Process(target=_isolated_child,
                           args=(handler_ref, work, net_block, mem_bytes,
                                 child_conn))
        proc.start()
        # the parent must drop its own copy of the child's pipe end —
        # otherwise the child's death can never surface as EOFError on
        # recv (the pipe would still have a writer) and a crash would be
        # misreported as the timeout.
        try:
            child_conn.close()
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001 — spawn/pickle failure: fail closed
        try:
            parent_conn.close()
            child_conn.close()
        except OSError:
            pass
        with _lock:
            _isolated_crashed[key] += 1
        return {"ok": False, "error": (
            f"tool {key} isolated worker could not start: "
            f"{type(e).__name__}: {e}")}
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(parent_conn.recv), timeout=rec.timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        proc.kill()
        proc.join(5)
        with _lock:
            _timed_out[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "timeout", outcome="fail",
                            tool=specialization)
        except Exception:  # noqa: BLE001
            pass
        logger.warning("tool timed out: %s exceeded timeout_ms=%d (isolated)",
                       key, rec.timeout_ms)
        return {"ok": False, "error": (
            f"tool {key} exceeded its registered timeout_ms={rec.timeout_ms} "
            "(the isolated worker was killed)")}
    except EOFError:
        with _lock:
            _isolated_crashed[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "isolated_crash", outcome="fail",
                            tool=specialization)
        except Exception:  # noqa: BLE001
            pass
        logger.warning("isolated worker crashed for tool %s (exit=%s)",
                       key, proc.exitcode)
        proc.join(5)
        return {"ok": False, "error": (
            f"tool {key} isolated worker crashed before answering "
            f"(exit={proc.exitcode}) — the host is unaffected")}
    finally:
        proc.join(5)
        try:
            parent_conn.close()
        except OSError:
            pass
    kind, payload_result = payload
    if kind == "error":
        with _lock:
            _isolated_crashed[key] += 0        # failed run, host contained
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "isolated_run", outcome="fail",
                            tool=specialization)
        except Exception:  # noqa: BLE001
            pass
        return _sanitize_result({"ok": False, "error": payload_result})
    try:
        from agent_civilization.core import telemetry
        telemetry.event("tool", "isolated_run", outcome="ok",
                        tool=specialization)
    except Exception:  # noqa: BLE001
        pass
    return payload_result


def _seed() -> ToolRegistry:
    from agent_civilization.core import internal_tools
    reg = PersistentToolRegistry(default_db_path())
    try:
        drift = reg.seed(internal_tools.internal_tool_records(), actor="seed:internal_tools")
    except BaseException:
        reg.close()
        raise
    if drift:
        logger.warning("tool registry: stored record differs from the code seed (stored wins): %s",
                       ", ".join(drift))
    _seed_drift[:] = drift
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
        if rec.lifecycle_state != "disabled":   # once, not once per worker
            reg.update_status(tid, rec.version, actor=ENV_ACTOR, lifecycle_state="disabled")
    if unknown:
        logger.warning("%s: unknown tool(s) ignored: %s", ENV_DISABLED, ", ".join(unknown))
    _unknown_disabled[:] = unknown
    return reg


def runtime_registry() -> ToolRegistry:
    """The process-wide handle on the SHARED registry (seeded lazily). Raises
    ``RegistryStoreError`` when the store is unavailable (not cached: the
    next call retries)."""
    global _registry
    with _lock:
        if _registry is None:
            _registry = _seed()
        return _registry


def reset_runtime_registry() -> None:
    """Forget this process's handle and counters (tests; re-reads the env).
    The shared store itself is NOT touched."""
    global _registry
    with _lock:
        if isinstance(_registry, PersistentToolRegistry):
            _registry.close()
        _registry = None
        _seed_drift.clear()
        _executed.clear()
        _refused.clear()
        _timed_out.clear()
        _net_blocked.clear()
        _output_capped.clear()
        _isolated.clear()
        _isolated_crashed.clear()
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


def _evaluate(spec: str) -> Tuple[bool, str, str]:
    try:
        reg = runtime_registry()
        ok, reason, key = _own_check(reg, spec)
        if not ok:
            return ok, reason, key
        for other in _reached(spec):
            ok2, reason2, _ = _own_check(reg, other)
            if not ok2:
                return False, (f"tool {tool_id_for(spec)} refused: it reaches a non-executable "
                               f"tool — {reason2}"), key
        return True, "", key
    except RegistryStoreError as e:     # fail-closed: never run on stale/empty state
        logger.warning("tool registry store error: %s", getattr(e, "reason", "unknown"))
        return False, f"tool {tool_id_for(spec)} refused: {STORE_UNAVAILABLE}", tool_id_for(spec)


def check(spec: str) -> Tuple[bool, str]:
    """Whether ``spec``'s tool may run now, and why not."""
    ok, reason, _ = _evaluate(spec)
    return ok, reason


async def dispatch(specialization: str, work: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Gate, then run the frozen dispatcher (see the module docstring).

    R-172 batch 1 (D-115): the record's own SECURITY metadata is ENFORCED
    here, not just stored — an approval-demanding tool (``requires_approval``)
    does not run without a valid approval in the work item, and NO tool may
    outlive its registered ``timeout_ms`` (fail-closed on both)."""
    if specialization not in real_dispatcher._HANDLERS:
        try:
            runtime_registry().get(tool_id_for(specialization))
        except (ToolNotFound, RegistryStoreError):
            return None                     # unknown specialization: frozen contract
    ok, reason, key = _evaluate(specialization)
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
    # re-fetch the record (the gate already proved it exists and is executable)
    try:
        rec = runtime_registry().get(tool_id_for(specialization))
    except (ToolNotFound, RegistryStoreError):
        with _lock:
            _refused[key] += 1
        return {"ok": False, "error": f"tool {key} refused: {STORE_UNAVAILABLE}"}
    if rec.requires_approval:
        ok_ap, ap_reason = _valid_approval(rec, work)
        if not ok_ap:
            with _lock:
                _refused[key] += 1
            try:
                from agent_civilization.core import telemetry
                telemetry.event("tool", "refused", outcome="fail",
                                tool=specialization)
            except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
                pass
            logger.info("tool refused: %s approval policy: %s", key, ap_reason)
            return {"ok": False, "error": (
                f"tool {key} refused: requires_approval is set — {ap_reason} "
                "(work['approval'] = {'actor': <who approved>, "
                "'granted_at': <ISO-8601 when>, 'scopes': [<approver scopes>], "
                "'tool': <tool id binding>} as the record's policy demands)")}
    if rec.requires_approval:
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "approved_run", outcome="ok", tool=specialization)
        except Exception:  # noqa: BLE001 — telemetry never blocks the run
            pass
    # R-172 batch 2: least privilege — the work item must GRANT every scope
    # the record demands (fail-closed, like the approval check above).
    missing = _missing_scopes(rec, work)
    if missing:
        with _lock:
            _refused[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "refused", outcome="fail", tool=specialization)
        except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
            pass
        logger.info("tool refused: %s missing required auth scopes: %s",
                    key, ", ".join(missing))
        return {"ok": False, "error": (
            f"tool {key} refused: missing required auth scopes: "
            f"{', '.join(missing)} — grant them in work['scopes']")}
    with _lock:
        _executed[key] += 1

    # R-172 batch 2: the per-tool network policy — a tool whose record
    # declares no network side effect and no allowlist cannot use sockets
    # while it runs (an audit hook gated per task context, so concurrent
    # tasks are untouched).
    net_ok = _network_allowed(rec)
    isolated = _wants_isolation(rec)
    if not net_ok and not isolated:
        _install_network_audit_hook()
    token = _net_forbidden.set(key) if (not net_ok and not isolated) else None
    try:
        async def _guarded() -> Any:
            # a TimeoutError raised BY the handler (an internal dependency
            # timing out) is re-raised as a distinct type so the gate's
            # wait_for below cannot catch it as its own budget — the
            # original propagates unchanged to the task layer (R-149: type
            # only, no text leak).
            try:
                if isolated:
                    # R-172 batch 3: the record opted into a spawned worker —
                    # the worker owns its own network policy (process-wide,
                    # thread-covering) and memory cap, so the parent's
                    # per-task hook is not armed for it.
                    return await _run_isolated(key, specialization, rec, work)
                return await real_dispatcher.dispatch(specialization, work)
            except TimeoutError as e:
                raise _HandlerRaisedTimeout() from e

        try:
            if isolated:
                # the isolation path OWNS its timeout: _run_isolated kills
                # and joins the worker on the registered timeout_ms itself
                # (a wait_for HERE would race it, cancel the runner, and
                # leave the worker's recv thread unjoinable at loop
                # shutdown).
                result = await _guarded()
            else:
                result = await asyncio.wait_for(_guarded(),
                                                timeout=rec.timeout_ms / 1000.0)
        except _HandlerRaisedTimeout as e:
            raise e.__cause__      # the handler's own timeout, untouched
        except asyncio.TimeoutError:
            with _lock:
                _timed_out[key] += 1
            try:
                from agent_civilization.core import telemetry
                telemetry.event("tool", "timeout", outcome="fail", tool=specialization)
            except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
                pass
            logger.warning("tool timed out: %s exceeded timeout_ms=%d", key,
                           rec.timeout_ms)
            return {"ok": False, "error": (
                f"tool {key} exceeded its registered timeout_ms={rec.timeout_ms}")}
        except RuntimeError as e:
            # the network policy block surfaces here: the audit hook raises
            # inside a socket op of the running tool's task context. The
            # frozen dispatcher shapes handler exceptions into error dicts,
            # so this path is for blocks raised around it — count it, log
            # the tool id (never the work payload), and fail closed.
            if "network use blocked" in str(e):
                with _lock:
                    _net_blocked[key] += 1
                try:
                    from agent_civilization.core import telemetry
                    telemetry.event("tool", "net_blocked", outcome="fail",
                                     tool=specialization)
                except Exception:  # noqa: BLE001
                    pass
                logger.warning("network use blocked for tool %s "
                              "(no network declared)", key)
                return {"ok": False, "error": (
                    f"tool {key} refused: {e}")}
            raise
    finally:
        if token is not None:
            _net_forbidden.reset(token)
    # R-172 batch 3: the failure channel is sanitized at the gate — any
    # credential inside an error text is redacted before the result moves on
    # (non-secret diagnostics are preserved; payloads are the tool's own
    # contract — declared limit).
    result = _sanitize_result(result)
    # the network block normally surfaces INSIDE the frozen dispatcher (it
    # shapes handler exceptions into error dicts) — count it from the result
    # text; the raw-exception path above is the same decision, kept for
    # blocks that escape the frozen layer.
    if (isinstance(result, dict) and result.get("ok") is False
            and isinstance(result.get("error"), str)
            and "network use blocked" in result["error"]):
        with _lock:
            _net_blocked[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "net_blocked", outcome="fail",
                            tool=specialization)
        except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
            pass
        logger.warning("network use blocked for tool %s (no network declared)",
                       key)
    # R-172 batch 2: output caps — a result larger than the registered (or
    # default) cap is refused and WITHHELD (the payload never flows back
    # through the gate; note the handler already ran — the cap bounds what
    # moves, it cannot un-run side effects).
    cap = _output_cap(rec)
    if _result_size(result) > cap:
        with _lock:
            _output_capped[key] += 1
        try:
            from agent_civilization.core import telemetry
            telemetry.event("tool", "output_capped", outcome="fail",
                            tool=specialization)
        except Exception:  # noqa: BLE001 — telemetry never blocks the refusal
            pass
        logger.warning("tool result capped: %s exceeded max_output_bytes=%d",
                       key, cap)
        return {"ok": False, "error": (
            f"tool {key} exceeded its output cap max_output_bytes={cap} "
            "(the result is withheld)")}
    return result


def stats() -> Dict[str, Any]:
    with _lock:
        return {"executed": dict(_executed), "refused": dict(_refused),
                "timed_out": dict(_timed_out),
                "net_blocked": dict(_net_blocked),
                "output_capped": dict(_output_capped),
                "isolated": dict(_isolated),
                "isolated_crashed": dict(_isolated_crashed),
                "unknown_disabled": list(_unknown_disabled), "seed_drift": list(_seed_drift),
                "store": "sqlite-host-shared"}


def coverage() -> Dict[str, Any]:
    """The frozen dispatcher's coverage (re-exported for callers of this gate)."""
    return real_dispatcher.coverage()


__all__ = ["EXECUTABLE_LIFECYCLES", "ENV_DISABLED", "check", "coverage", "dispatch",
           "reset_runtime_registry", "runtime_registry", "stats", "tool_id_for"]
