"""R-032 (D-037): opt-in runtime backed by the `specialists/` package.

Enabled with ``AGOS_AGENT_IMPL=specialists`` (default ``real`` keeps
``RealSpecialistAgent``). Each runtime agent wraps a real instance of a
specialist class (e.g. ``AnalystAgent0015``) and exposes the same contract
the server/orchestrator rely on: ``name``, ``specialization``, ``agent_id``,
``status``, ``stats`` (tasks_completed/tasks_failed/total_time_s),
``execute()`` and ``snapshot()``.

Execution path: adapter.execute → BaseAgent.execute → wrapped
``_process_task`` → ``real_dispatcher.dispatch``. The specialist instances
are also registered on a shared ``CommunicationHub`` so they can exchange
messages (R-054).
"""
from __future__ import annotations

import importlib
import inspect
import time
from typing import Any, Dict, List

from agent_civilization.agents.real.runtime_contract import failure_result
from agent_civilization.core import experience as _experience
from agent_civilization.core import telemetry as _telemetry
from agent_civilization.core.output_validators import apply_output_verification
from agent_civilization.core.safety import UnsafeURLError  # noqa: F401  (public name kept, R-157)
# R-104c2: the canonical public messaging API lives in agent_civilization.communication
from agent_civilization.communication import CommunicationHub

VALID_IMPLS = ("real", "specialists")


def specialist_classes(spec: str) -> List[type]:
    """The 50 specialist classes of one specialization, in stable order."""
    mod = importlib.import_module(f"agent_civilization.agents.specialists.{spec}")
    return [getattr(mod, n) for n in sorted(mod.__all__)]


class SpecialistBackedAgent:
    """Runtime adapter around a real specialist (BaseAgent subclass) instance."""

    def __init__(self, inner: Any, specialization: str, agent_id: int):
        self.inner = inner
        self.name = inner.name
        self.specialization = specialization
        self.agent_id = agent_id
        self.status = "idle"
        self.stats: Dict[str, float] = {
            "tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0,
        }

    @property
    def impl_class(self) -> str:
        return type(self.inner).__name__

    async def execute(self, work: Dict[str, Any]) -> Dict[str, Any]:
        # R-159: one `agent` span, same fields as the real runtime (impl differs)
        with _telemetry.span("agent", self.specialization, impl="specialists", agent=self.name,
                             specialization=self.specialization) as sp:
            out = await self._execute(work)
            sp.set(outcome="ok" if out.get("ok") else "fail")
            return out

    async def _execute(self, work: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        self.status = "working"
        try:
            work = work or {}
            # R-170 (batch 3): capture verification requirements BEFORE the
            # handler runs — it must not be able to change them mid-flight.
            # R-170 (batch 7): a copy; the per-specialization default policy
            # applies when the work declares none (e.g. /api/tasks).
            from agent_civilization.core.output_validators import requirements_for
            _verification = requirements_for(self.specialization, work)

            async def call():
                out = await self._run_inner(work)
                r = out.get("result")
                if not isinstance(r, dict):
                    r = {"ok": bool(out.get("ok")), "answer": r}
                return r

            # R-103: memory + learning around the real handler (D-046)
            result, exp = await _experience.run_with_experience(self.specialization, self.name, work, call)
            ok = bool(result.get("ok"))
            # R-170 (batch 3): completion gate (opt-in AGOS_OUTPUT_VALIDATORS=1)
            # applied BEFORE adopting success or its side effects (stats below).
            ok, result = apply_output_verification(_verification, ok, result)
            elapsed = round(time.time() - started, 3)
            self.stats["tasks_completed" if ok else "tasks_failed"] += 1
            self.stats["total_time_s"] += elapsed
            resp = {"ok": ok, "agent": self.name, "elapsed_s": elapsed,
                    "result": result, "impl": self.impl_class}
            if exp is not None:
                resp["experience"] = exp
            return resp
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.time() - started, 3)
            self.stats["tasks_failed"] += 1
            _telemetry.annotate(kind="agent", error=exc)
            self.stats["total_time_s"] += elapsed
            # R-149 (Security QA): an unexpected exception's raw text is not
            # trusted — only its TYPE is reported; UnsafeURLError keeps its
            # fixed refusal message (SEC-01/SEC-05, proven by their tests).
            # R-157: the rule lives in runtime_contract (shared with real).
            return {"ok": False, "agent": self.name, "elapsed_s": elapsed,
                    "result": failure_result(exc),
                    "impl": self.impl_class}
        finally:
            self.status = "idle"

    async def _run_inner(self, work: Dict[str, Any]) -> Dict[str, Any]:
        """R-157 (D-069) compatibility adapter around the specialist instance.

        Same bookkeeping and the same return value as the legacy
        ``BaseAgent.execute`` (status, ``performance_metrics``, ok propagation
        of R-032, non-dict results) — but an exception raised by
        ``_process_task`` is re-raised instead of being turned into
        ``"<Type>: <message>"``. That lets it reach the canonical envelope
        above, so both runtimes report the type only (R-149) and a raising
        handler leaves no memory/learning record, exactly like ``real``.
        ``BaseAgent.execute`` itself is unchanged for its other callers
        (hub ``_execute_work``)."""
        inner = self.inner
        pt = getattr(inner, "_process_task", None)
        if not (pt and inspect.iscoroutinefunction(pt)):
            return {"ok": False, "agent": inner.name,
                    "result": {"error": "agent has no _process_task"}}
        t0 = time.monotonic()
        inner.status = "working"
        try:
            result = await pt(work)
        except Exception:
            inner.performance_metrics["tasks_failed"] += 1
            raise
        finally:
            inner.status = "idle"
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        inner.performance_metrics["tasks_completed" if ok else "tasks_failed"] += 1
        return {"ok": ok, "agent": inner.name,
                "elapsed_s": round(time.monotonic() - t0, 3), "result": result}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.agent_id,
            "name": self.name,
            "specialization": self.specialization,
            "status": self.status,
            "impl": self.impl_class,
            "stats": {
                "tasks_completed": int(self.stats["tasks_completed"]),
                "tasks_failed": int(self.stats["tasks_failed"]),
                "total_time_s": round(float(self.stats["total_time_s"]), 3),
            },
        }


def create_specialist_civilization(specializations, agents_per_spec: int) -> Dict[str, list]:
    """Build the civilization from specialist classes (cycling the 50 per spec)."""
    hub = CommunicationHub()
    civ: Dict[str, list] = {}
    gid = 0
    for spec in specializations:
        classes = specialist_classes(spec)
        pool = []
        for i in range(agents_per_spec):
            cls = classes[i % len(classes)]
            name = f"{spec}_{gid:04d}"
            inner = cls(name=name, hub=hub, agent_id=gid)
            # Startup is single-threaded with no running loop: register directly
            # (CommunicationHub.register_agent is async and only guards concurrency).
            hub.agents[name] = inner
            pool.append(SpecialistBackedAgent(inner, spec, gid))
            gid += 1
        civ[spec] = pool
    return civ
