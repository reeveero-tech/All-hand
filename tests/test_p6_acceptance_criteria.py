"""D-137: R-178..R-184 acceptance criteria are MEASURABLE, not just numbers.

Checks that every criterion in benchmarks/p6/acceptance_criteria.json names
an implemented metric, an operator, a data source and (for quantiles) a
statistically sufficient minimum sample; that evaluation never passes on
missing/insufficient data; and that a pass on a PROPOSED threshold is not
reported as approved. Synthetic data only — nothing here measures the system.
"""
from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p6_metrics", ROOT / "benchmarks" / "p6" / "metrics.py")
m = importlib.util.module_from_spec(spec)
sys.modules["p6_metrics"] = m
spec.loader.exec_module(m)
DOC = json.loads((ROOT / "benchmarks" / "p6" / "acceptance_criteria.json").read_text())
CRIT = {c["id"]: c for c in DOC["criteria"]}
REQUIRED = {"id", "item", "name_ar", "metric", "op", "threshold", "unit",
            "data_source", "tool", "status", "report_only", "min_samples"}


def test_ids_unique_and_every_item_covered():
    ids = [c["id"] for c in DOC["criteria"]]
    assert len(ids) == len(set(ids))
    assert {c["item"] for c in DOC["criteria"]} == {f"R-{n}" for n in range(178, 185)}


@pytest.mark.parametrize("cid", sorted(CRIT))
def test_criterion_is_measurable(cid):
    c = CRIT[cid]
    assert REQUIRED <= set(c), REQUIRED - set(c)
    assert c["metric"] in m.METRICS, f"{cid}: metric {c['metric']} not implemented"
    assert c["op"] in m.OPS
    assert isinstance(c["threshold"], (int, float))
    assert c["data_source"].strip()
    assert c["status"] == "proposed"  # owner approval pending (D-133 rule)
    if c["tool"] != "not_implemented":
        path = c["tool"].split(" ")[0]
        assert (ROOT / path).exists(), f"{cid}: tool {path} missing"


@pytest.mark.parametrize("cid", sorted(c["id"] for c in DOC["criteria"]
                                       if c["metric"] in ("p95_ms", "p99_ms")))
def test_quantile_criteria_have_sufficient_min_samples(cid):
    c = CRIT[cid]
    q = 0.95 if c["metric"] == "p95_ms" else 0.99
    assert c["min_samples"] >= m.min_samples_for_quantile(q)


def test_plan_and_criteria_do_not_diverge():
    """Key thresholds in the plan document equal the machine-readable ones."""
    plan = (ROOT / "docs/audit/M8/prep/R178_R184_EXECUTION_PLAN.md").read_text()
    assert "≤ 500ms / ≤ 1000ms" in plan and CRIT["r178.dispatch_p95_ms"]["threshold"] == 500
    assert CRIT["r178.dispatch_p99_ms"]["threshold"] == 1000
    assert "≥ 0.90" in plan and CRIT["r178.worker_fairness_jain"]["threshold"] == 0.90
    assert "≤ 120s" in plan and CRIT["r179.rto_s"]["threshold"] == 120
    assert "≤ 5% / ساعة" in plan and CRIT["r180.rss_growth_pct_per_hour"]["threshold"] == 5
    assert "≤ 20%" in plan and CRIT["r181.soak_p95_drift_pct"]["threshold"] == 20
    assert "acceptance_criteria.json" in plan


# ------------------------------------------------------------- evaluation

def test_no_data_is_not_measured_never_pass():
    r = m.evaluate(CRIT["r179.silent_loss"])
    assert r["verdict"] == "not_measured" and r["value"] is None


def test_insufficient_samples_is_not_measured():
    r = m.evaluate(CRIT["r178.dispatch_p99_ms"], latencies_ms=[1.0] * 298)
    assert r["verdict"] == "not_measured" and "insufficient" in r["why"]
    r = m.evaluate(CRIT["r178.dispatch_p99_ms"], latencies_ms=[1.0] * 299)
    assert r["verdict"] == "pass"


def test_pass_on_proposed_threshold_is_not_approved():
    r = m.evaluate(CRIT["r178.dispatch_p95_ms"], latencies_ms=[10.0] * 100)
    assert r["verdict"] == "pass" and r["approved"] is False


def test_fail_and_operator_semantics():
    lat = [100.0] * 90 + [900.0] * 10  # p95 nearest-rank = 900
    assert m.evaluate(CRIT["r178.dispatch_p95_ms"], latencies_ms=lat)["verdict"] == "fail"
    assert m.evaluate(CRIT["r178.error_rate"], statuses=[200] * 99 + [500])["verdict"] == "fail"  # 1% is not < 1%
    assert m.evaluate(CRIT["r178.error_rate"], statuses=[200] * 999 + [500])["verdict"] == "pass"


def test_bad_shape_and_undefined_are_not_measured():
    assert m.evaluate(CRIT["r179.silent_loss"], wrong=1)["verdict"] == "not_measured"
    assert m.evaluate(CRIT["r181.soak_p95_drift_pct"], ts_s=list(range(100)),
                      latencies_ms=[1.0] * 100)["verdict"] == "not_measured"  # < 2 windows


# ------------------------------------------------------------- metric math

def test_quantiles_nearest_rank():
    vals = list(range(1, 101))
    assert m.p95_ms(vals) == 95 and m.p99_ms(vals) == 99
    assert m.nearest_rank([5.0], 0.99) == 5.0 and m.p95_ms([]) is None
    assert m.min_samples_for_quantile(0.99) == 299 and m.min_samples_for_quantile(0.95) == 59


def test_jain_and_imbalance():
    assert m.jain_index([5, 5, 5, 5]) == pytest.approx(1.0)
    assert m.jain_index([1, 0, 0, 0]) == pytest.approx(0.25)
    assert m.jain_index([0, 0]) is None
    assert m.max_min_ratio([2, 4]) == 2.0 and m.max_min_ratio([0, 3]) == math.inf


def test_loss_duplicates_reconciliation_isolation():
    assert m.silent_loss(["a", "b", "c", "d"], ["a"], ["b"], ["c"]) == 1
    assert m.duplicate_side_effects(["k1", "k1", "k1", "k2"]) == 2
    api = {"a": "completed", "b": "failed"}
    assert m.reconciliation_ratio(api, dict(api), dict(api)) == 1.0
    assert m.reconciliation_ratio(api, {"a": "completed", "b": "completed"}, dict(api)) == 0.5
    assert m.reconciliation_ratio({}, {"x": "queued"}, {}) == 0.0  # missing from API is a mismatch
    assert m.isolation_leaks([{"owner": "a", "observer": "a"}, {"owner": "a", "observer": "b"}]) == 1


def test_refusals_and_retry_after():
    rs = [{"status": 429, "retry_after": "2"}, {"status": 503, "retry_after": "1"},
          {"status": 503, "retry_after": None}, {"status": 500, "retry_after": "1"},
          {"status": 502}]
    assert m.unstructured_refusals(rs) == 3
    assert m.retry_after_respected([{"waited_s": 2, "retry_after_s": 2},
                                    {"waited_s": 0.5, "retry_after_s": 1}]) == 0.5


def test_time_series():
    ts = [i * 60.0 for i in range(121)]  # 2 hours, one sample a minute
    flat = [100.0] * len(ts)
    assert m.growth_pct_per_hour(ts, flat) == pytest.approx(0.0)
    leak = [100.0 + i for i in range(len(ts))]  # +60 units/hour on ~160 mean
    assert m.growth_pct_per_hour(ts, leak, warmup_s=600) > 5
    assert m.growth_pct_total([100, 105, 111]) == pytest.approx(11.0)
    assert m.monotonic_growth_fraction(list(range(10))) == 1.0
    assert m.monotonic_growth_fraction([5, 6, 5, 6, 5, 6]) == pytest.approx(0.6)
    ts3 = [float(i) for i in range(3 * 3600)]
    lat = [10.0 if t < 3600 else 13.0 for t in ts3]
    assert m.window_drift_pct(ts3, lat) == pytest.approx(30.0)


def test_rto_requires_stable_recovery():
    s = [{"t": 0, "status": 503}, {"t": 5, "status": 200}, {"t": 6, "status": 503},
         {"t": 30, "status": 200}, {"t": 31, "status": 200}]
    assert m.recovery_time_s(0, s) == 30  # the blip at t=5 is not a recovery
    assert m.recovery_time_s(0, [{"t": 1, "status": 503}]) is None


def test_gaps_are_documented_where_the_plan_was_not_measurable():
    for cid in ("r178.queue_wait_p95_ms", "r178.worker_fairness_jain",
                "r179.orphan_resume_excess_s", "r181.soak_fd_monotonic_fraction",
                "r180.oversize_not_rejected"):
        assert CRIT[cid].get("gap_closed"), cid
    assert all(re.search(r"\S", c["name_ar"]) for c in DOC["criteria"])
