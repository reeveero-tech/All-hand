"""R-175 batch 2 (D-126): catalog throughput measurement — the phase-2 gate.

The 10K_TOOLS_SPEC_AR.md acceptance requires «throughput للبحث وتحديث الكتالوج
مقاس» — measured, not claimed. This module is the measurement:

* ``make_records(n)`` — n UNIQUE valid tool records (one implementation_ref per
  tool_id; no aliases by construction) for seeding at scale.
* ``measure_throughput(registry, n)`` — one structured report over a registry
  seeded to n tools: bulk-seed rate (the catalog-update path — one
  transaction), full-scan search rate (the retrieval path), ``state_counts``
  scan rate (the declared numbers), and the governed certification walk rate
  (``certify_batch`` — each item walks contract→integration→certified through
  the registry's own front doors, inside the phase-1 ceiling).
* ``BUDGETS`` — the declared budgets, checked by ``within_budget()``. They are
  deliberately GENEROUS (order-of-magnitude over local runs) so CI variance
  never flakes the suite; the point is to detect regressions of that scale,
  not to certify a specific number. The report carries the raw measurements
  — the honest artifact is the measurement itself.

What it is NOT (honest limits): single-writer, no concurrent-load or
multi-host contention measurement yet (a later batch if the spec's phase-3
load profile demands it); the CI test runs at n=1,000 (the spec's phase-2
number) — the 10,000 run is on demand via ``scripts/bench_tool_catalog.py``
(documented, not part of the test suite's time budget); rates are wall-clock
and machine-dependent — the budgets are guards, not SLAs.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from agent_civilization.core.tool_certification import certify_batch

#: the spec's phase numbers, declared once (100 -> 1,000 -> 10,000).
PHASE_NUMBERS = (100, 1_000, 10_000)

#: declared budgets (generous by design — regression guards, not SLAs).
BUDGETS: Dict[str, float] = {
    "seed_ms_per_record": 5.0,       # bulk seed: one transaction, n records
    "search_ms_per_1k": 50.0,        # full-scan retrieval over n records
    "counts_ms_per_1k": 50.0,        # state_counts scan over n records
    "certify_ms_per_item": 250.0,    # the 3-step governed walk per item
}

_EVIDENCE = "smoke pass; contract v1.0 verified; security: allowlist+timeout"


def make_record(tool_id: str, version: str = "1.0.0", **overrides) -> Dict[str, Any]:
    """A valid unique record (shared by the bench, the tests and the script)."""
    base = {"tool_id": tool_id, "version": version,
            "name": f"{tool_id} bench tool",
            "description": f"{tool_id} throughput bench tool",
            "source": {"kind": "internal", "uri": "agos.internal:r175-bench"},
            "capabilities": ["bench"], "input_schema": {"type": "object"},
            "output_schema": {"type": "object"}, "auth_scope": [],
            "tenant_scope": "request_tenant", "risk_class": "low",
            "side_effects": [], "read_only": True, "idempotent": True,
            "requires_approval": False, "timeout_ms": 30_000,
            "network_allowlist": [], "health_status": "healthy",
            "test_status": "not_tested",
            "implementation_ref": "agent_civilization.core.r175bench:"
                                   + tool_id.replace(".", "_"),
            "lifecycle_state": "registered"}
    base.update(overrides)
    return base


def make_records(n: int, prefix: str = "bench", version: str = "1.0.0"
                 ) -> List[Any]:
    """n UNIQUE ToolRecords (the seed API takes models, not dicts) — one
    implementation_ref per tool_id, no aliases by construction."""
    from agent_civilization.core.tool_registry import ToolRecord
    return [ToolRecord.model_validate(make_record(f"{prefix}.{i:05d}", version))
            for i in range(n)]


def to_record(data: Dict[str, Any]) -> Any:
    """The single-record bridge: a dict (register accepts dicts) -> a
    ToolRecord (seed takes models)."""
    from agent_civilization.core.tool_registry import ToolRecord
    return ToolRecord.model_validate(data)


def _rate(ms: float, items: float) -> float:
    return round(items / (ms / 1000.0), 1) if ms > 0 else 0.0


def measure_throughput(registry: Any, n: int = 1_000, *, certify_items: int = 25,
                       search_repeats: int = 3) -> Dict[str, Any]:
    """Seed to n tools, then measure the catalog-update and retrieval paths.

    The certification walk stays inside the phase-1 ceiling (100) — the bench
    measures the walk rate, it does not phase-advance."""
    report: Dict[str, Any] = {"n": n, "phase_number": n if n in PHASE_NUMBERS
                              else None, "certify_items": certify_items}
    records = make_records(n)
    t0 = time.perf_counter()
    drift = registry.seed(records, actor="bench:r175")
    seed_ms = (time.perf_counter() - t0) * 1000.0
    report["seed_drift"] = drift
    report["seed_ms"] = round(seed_ms, 1)
    report["seed_ms_per_record"] = round(seed_ms / n, 3)
    report["seed_rate_per_s"] = _rate(seed_ms, n)

    # retrieval: full scan (the declared numbers path) + narrowed search.
    best_search = None
    for _ in range(search_repeats):
        t0 = time.perf_counter()
        found = len(registry.search(include_disabled=True, limit=None))
        ms = (time.perf_counter() - t0) * 1000.0
        best_search = ms if best_search is None else min(best_search, ms)
    report["search_found"] = found
    report["search_ms"] = round(best_search, 1)
    report["search_ms_per_1k"] = round(best_search / (n / 1000.0), 3)
    report["search_rate_per_s"] = _rate(best_search, n)
    t0 = time.perf_counter()
    narrowed = registry.search(text="bench tool", limit=20)
    report["narrowed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    report["narrowed_count"] = len(narrowed)

    # the declared numbers scan.
    t0 = time.perf_counter()
    from agent_civilization.core.tool_certification import state_counts
    counts = state_counts(registry)
    counts_ms = (time.perf_counter() - t0) * 1000.0
    report["counts"] = counts
    report["counts_ms"] = round(counts_ms, 1)
    report["counts_ms_per_1k"] = round(counts_ms / (n / 1000.0), 3)

    # the governed walk (inside the phase-1 ceiling; n >= items is expected).
    items = [{"tool_id": f"bench.{i:05d}", "version": "1.0.0",
              "evidence": _EVIDENCE} for i in range(certify_items)]
    t0 = time.perf_counter()
    outcome = certify_batch(registry, items, actor="bench:r175", phase=1)
    certify_ms = (time.perf_counter() - t0) * 1000.0
    report["certify_outcome"] = outcome
    report["certify_ms"] = round(certify_ms, 1)
    report["certify_ms_per_item"] = round(certify_ms / certify_items, 3)
    report["certify_rate_per_s"] = _rate(certify_ms, certify_items)
    report["certified_now"] = state_counts(registry)["lifecycle"]["certified"]

    report["budgets"] = dict(BUDGETS)
    report["within_budget"] = within_budget(report)
    return report


def within_budget(report: Dict[str, Any]) -> bool:
    """Every measured value inside its declared budget (regression guard)."""
    checks = (
        (report.get("seed_ms_per_record", 1e9), BUDGETS["seed_ms_per_record"]),
        (report.get("search_ms_per_1k", 1e9), BUDGETS["search_ms_per_1k"]),
        (report.get("counts_ms_per_1k", 1e9), BUDGETS["counts_ms_per_1k"]),
        (report.get("certify_ms_per_item", 1e9), BUDGETS["certify_ms_per_item"]),
    )
    return all(measured <= budget for measured, budget in checks)


__all__ = ["PHASE_NUMBERS", "BUDGETS", "make_record", "make_records",
           "measure_throughput", "within_budget"]
