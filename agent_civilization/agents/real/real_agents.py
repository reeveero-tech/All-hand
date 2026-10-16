#!/usr/bin/env python3
"""
Real specialist runtime for AGOS Civilization.

This module creates the in-house civilization: 20 specializations x 50
worker instances = 1000 addressable local agents. Every worker delegates to
`real_dispatcher`, which performs real parsing, computation, IO probes,
statistics, persistence-adjacent operations, or LLM calls when configured.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_civilization.core import experience as _experience
from agent_civilization.core.agents.real_dispatcher import (
    coverage as _coverage,
    dispatch as _dispatch,
)
from agent_civilization.core.safety import UnsafeURLError

SPECIALIZATIONS: Dict[str, str] = {
    "analyst": "Analyze numeric/textual evidence and extract measurable patterns.",
    "api_integrator": "Call and inspect HTTP APIs with live response metadata.",
    "architect": "Derive a component graph (requested + implied parts, dependencies, build order) from the request.",
    "builder": "Scaffold project files, verify each (compile/json/toml), and return them.",
    "code_generator": "Generate compile-verified Python from a structured spec (or an LLM when configured); validate supplied code.",
    "communicator": "Rewrite, translate, and measure communication tone.",
    "data_processor": "Clean, aggregate, and summarize structured data.",
    "db_manager": "Validate and execute SQL safely against an isolated SQLite engine.",
    "designer": "Build a palette from the requested colour/mood, checked against WCAG contrast.",
    "educator": "Take the first sentences of a text as key points, with reading time.",
    "modifier": "Rename identifiers or remove unused imports on request; return AST-verified code and diff.",
    "monitor": "Capture live runtime health metrics from the current process.",
    "network_client": "Resolve DNS and probe HTTP endpoints in real time.",
    "researcher": "Perform live web-backed research and source gathering.",
    "reviewer": "Review code/text with tokenizer and static metrics.",
    "self_developer": "Inspect AGOS' own real capability coverage.",
    "strategist": "Order objectives as given into a weighted roadmap.",
    "surgeon": "Diagnose code defects with AST-based inspection.",
    "test_runner": "Run small Python test/code snippets in a bounded subprocess.",
    "validator": "Validate JSON and Python object structure.",
}

SPECIALIZATION_ICONS: Dict[str, str] = {
    "analyst": "chart-line", "api_integrator": "plug", "architect": "network",
    "builder": "hammer", "code_generator": "code", "communicator": "messages",
    "data_processor": "database-zap", "db_manager": "database", "designer": "palette",
    "educator": "graduation-cap", "modifier": "edit", "monitor": "activity",
    "network_client": "wifi", "researcher": "search", "reviewer": "shield-check",
    "self_developer": "sparkles", "strategist": "map", "surgeon": "wrench",
    "test_runner": "flask", "validator": "check-circle",
}

SPECIALIZATION_TOOLS: Dict[str, str] = {
    "analyst": "statistics", "api_integrator": "http-get", "architect": "component-graph",
    "builder": "scaffold+verify", "code_generator": "spec-codegen+ast", "communicator": "sentiment",
    "data_processor": "aggregation", "db_manager": "sqlite", "designer": "hsl-wcag-palette",
    "educator": "keypoint-extraction", "modifier": "ast-rename+unused-imports", "monitor": "resource",
    "network_client": "dns-http", "researcher": "duckduckgo", "reviewer": "tokenize",
    "self_developer": "coverage-introspection", "strategist": "ordered-roadmap",
    "surgeon": "ast-diagnostics", "test_runner": "python-subprocess", "validator": "json-parse",
}


@dataclass
class RealSpecialistAgent:
    """A real executable worker; compatible with server/orchestrator snapshots."""

    name: str
    specialization: str
    agent_id: int
    status: str = "idle"
    stats: Dict[str, float] = field(default_factory=lambda: {
        "tasks_completed": 0,
        "tasks_failed": 0,
        "total_time_s": 0.0,
    })

    async def execute(self, work: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        self.status = "working"
        try:
            work = work or {}

            async def call():
                r = await _dispatch(self.specialization, work)
                return r if r is not None else {"ok": False, "error": f"no handler for {self.specialization}"}

            # R-103: memory + learning around the real handler (D-046)
            result, exp = await _experience.run_with_experience(self.specialization, self.name, work, call)
            ok = bool(result.get("ok"))
            if ok:
                self.stats["tasks_completed"] += 1
            else:
                self.stats["tasks_failed"] += 1
            elapsed = round(time.time() - started, 3)
            self.stats["total_time_s"] += elapsed
            out = {"ok": ok, "agent": self.name, "elapsed_s": elapsed, "result": result}
            if exp is not None:
                out["experience"] = exp
            return out
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.time() - started, 3)
            self.stats["tasks_failed"] += 1
            self.stats["total_time_s"] += elapsed
            # R-149 (Security QA): an unexpected exception's raw text is not
            # trusted — it can carry internal details into the tenant-visible
            # result — so only its TYPE is reported. UnsafeURLError keeps its
            # fixed refusal message (SEC-01/SEC-05, proven by their tests).
            error = str(exc) if isinstance(exc, UnsafeURLError) else type(exc).__name__
            return {
                "ok": False,
                "agent": self.name,
                "elapsed_s": elapsed,
                "result": {"ok": False, "error": error},
            }
        finally:
            self.status = "idle"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.agent_id,
            "name": self.name,
            "specialization": self.specialization,
            "status": self.status,
            "stats": {
                "tasks_completed": int(self.stats["tasks_completed"]),
                "tasks_failed": int(self.stats["tasks_failed"]),
                "total_time_s": round(float(self.stats["total_time_s"]), 3),
            },
        }


async def run_real(specialization: str, prompt: str, data: Optional[Any] = None) -> Dict[str, Any]:
    result = await _dispatch(specialization, {"prompt": prompt, "data": data})
    if result is None:
        return {"ok": False, "specialization": specialization, "error": f"no real implementation for '{specialization}'"}
    return {"specialization": specialization, **result}


def create_civilization(agents_per_spec: int = 50, impl: Optional[str] = None) -> Dict[str, list]:
    """Create 1000 real in-house workers by default; no manual user routing needed.

    ``impl`` (or env ``AGOS_AGENT_IMPL``): ``real`` (default) → RealSpecialistAgent;
    ``specialists`` → agents backed by the `specialists/` package classes (R-032, D-037).
    Unknown values fail loudly at startup rather than silently falling back.
    """
    import os
    impl = (impl or os.getenv("AGOS_AGENT_IMPL", "real")).strip().lower()
    from agent_civilization.agents.real.specialist_runtime import (
        VALID_IMPLS, create_specialist_civilization)
    if impl not in VALID_IMPLS:
        raise ValueError(f"AGOS_AGENT_IMPL must be one of {VALID_IMPLS}, got {impl!r}")
    if impl == "specialists":
        return create_specialist_civilization(SPECIALIZATIONS, agents_per_spec)
    civilization: Dict[str, List[RealSpecialistAgent]] = {}
    global_id = 0
    for spec in SPECIALIZATIONS:
        pool: List[RealSpecialistAgent] = []
        for _ in range(agents_per_spec):
            pool.append(RealSpecialistAgent(
                name=f"{spec}_{global_id:04d}",
                specialization=spec,
                agent_id=global_id,
            ))
            global_id += 1
        civilization[spec] = pool
    return civilization


def real_coverage() -> Dict[str, Any]:
    covered = _coverage()
    return {
        **covered,
        "total_specializations": len(SPECIALIZATIONS),
        "coverage_percent": round(100 * covered["count"] / max(len(SPECIALIZATIONS), 1), 2),
        "default_local_agents": len(SPECIALIZATIONS) * 50,
    }


__all__ = [
    "SPECIALIZATIONS",
    "SPECIALIZATION_ICONS",
    "SPECIALIZATION_TOOLS",
    "RealSpecialistAgent",
    "create_civilization",
    "run_real",
    "real_coverage",
]
