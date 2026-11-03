"""R-175 batch 2: catalog throughput — the red-first acceptance tests.

They are imported against the missing module first (``ImportError`` on main =
the red), then each scenario measures against a DIRECT empty registry (no
runtime seeding — the bench seeds its own n tools).
"""
from __future__ import annotations

import pytest

from agent_civilization.core.tool_catalog_bench import (
    BUDGETS, PHASE_NUMBERS, make_records, measure_throughput, within_budget)
from agent_civilization.core.tool_certification import state_counts


@pytest.fixture()
def fresh_reg(tmp_path):
    from agent_civilization.core.tool_registry_store import PersistentToolRegistry
    reg = PersistentToolRegistry(str(tmp_path / "r175b2.sqlite3"))
    yield reg
    reg.close()


# -- the module's declared surface --------------------------------------------------

def test_module_declares_the_spec_phase_numbers():
    assert PHASE_NUMBERS == (100, 1_000, 10_000)


def test_budgets_are_declared_and_finite():
    for key in ("seed_ms_per_record", "search_ms_per_1k",
                "counts_ms_per_1k", "certify_ms_per_item"):
        assert key in BUDGETS and BUDGETS[key] > 0


def test_make_records_are_unique_and_alias_free(fresh_reg):
    records = make_records(5)
    assert len(records) == 5
    # unique tool_ids AND unique implementation_refs (no aliases by construction):
    assert len({r.tool_id for r in records}) == 5
    assert len({r.implementation_ref for r in records}) == 5
    drift = fresh_reg.seed(records, actor="test:r175b2")
    assert drift == []
    assert len(fresh_reg) == 5


# -- the measured report (the spec's phase-2 number: 1,000) ------------------------

def test_throughput_report_measured_at_the_phase2_number(fresh_reg):
    report = measure_throughput(fresh_reg, n=1_000, certify_items=25)
    assert report["n"] == 1_000
    assert report["phase_number"] == 1_000          # the spec's phase-2 gate
    assert report["seed_drift"] == []
    assert report["search_found"] == 1_000
    assert report["counts"]["unique_tools"] == 1_000
    # the governed walk stayed inside the phase-1 ceiling and reported honestly:
    assert report["certified_now"] == 25
    assert report["certify_outcome"]["stopped_at"] is None
    assert state_counts(fresh_reg)["lifecycle"]["certified"] == 25


def test_every_measurement_is_positive_and_within_budget(fresh_reg):
    report = measure_throughput(fresh_reg, n=1_000, certify_items=25)
    for key in ("seed_ms", "search_ms", "counts_ms", "certify_ms",
                "seed_rate_per_s", "search_rate_per_s", "certify_rate_per_s"):
        assert report[key] > 0, key
    # the narrowed search is the prompt-budget path: never the whole catalog:
    assert report["narrowed_count"] == 20
    assert within_budget(report) is True
    assert report["within_budget"] is True


def test_the_report_carries_the_raw_measurements(fresh_reg):
    report = measure_throughput(fresh_reg, n=500, certify_items=10)
    # the honest artifact is the measurement itself — every number present:
    for key in ("seed_ms_per_record", "search_ms_per_1k", "counts_ms_per_1k",
                "certify_ms_per_item", "budgets"):
        assert key in report
    assert report["budgets"] == dict(BUDGETS)


def test_within_budget_flags_a_violation():
    poisoned = {"seed_ms_per_record": BUDGETS["seed_ms_per_record"] * 10,
               "search_ms_per_1k": 1.0, "counts_ms_per_1k": 1.0,
               "certify_ms_per_item": 1.0}
    assert within_budget(poisoned) is False
