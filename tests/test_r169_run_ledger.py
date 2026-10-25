"""R-169 run ledger (scripts/r169_run_ledger.py) — offline classifier regression tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r169_run_ledger", ROOT / "scripts/r169_run_ledger.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

META = {"kind": "meta", "benchmark_version": "r169-v5", "split": "validation", "config": "grounded-v2",
        "tree_dirty": False}
N = 46  # v5 validation size


def rows(n, reply="ok", error=None):
    return [{"id": f"c{i}", "reply": reply if error is None else "", "error": error, "latency_s": 1.0}
            for i in range(n)]


def test_complete_real_calls_are_valid():
    r = L.classify_run("x/a.jsonl", META, rows(N), {})
    assert r["status"] == "valid" and r["provider"] == "called" and r["valid_for_analysis"]


def test_missing_key_means_provider_never_called():
    r = L.classify_run("x/a.jsonl", META, rows(6, error="OPENROUTER_API_KEY is not configured"), {})
    assert r["provider"] == "never_called" and r["status"] == "no_provider_call"
    assert not r["valid_for_analysis"]


def test_stopped_run_is_incomplete():
    r = L.classify_run("x/a.jsonl", META, rows(33), {})
    assert r["status"] == "incomplete_stopped" and "incomplete 33/46" in r["reasons"]


def test_byte_identical_replay_is_not_independent():
    earlier: dict = {}
    L.classify_run("x/a.jsonl", META, rows(N), earlier)
    r = L.classify_run("x/b.jsonl", META, rows(N), earlier)
    assert r["status"] == "duplicate_not_independent" and r["identical_to_earlier"]["run"] == "x/a.jsonl"


def test_distinct_replies_are_not_flagged_duplicate():
    earlier: dict = {}
    L.classify_run("x/a.jsonl", META, rows(N, reply="one"), earlier)
    r = L.classify_run("x/b.jsonl", META, rows(N, reply="two"), earlier)
    assert r["status"] == "valid" and r["identical_to_earlier"] is None


def test_provider_error_rate_over_10pct_blocks_analysis():
    body = rows(40) + rows(6, error="TimeoutError: timed out")
    for i, b in enumerate(body):
        b["id"] = f"c{i}"
    r = L.classify_run("x/a.jsonl", META, body, {})
    assert r["status"] == "provider_error_heavy" and r["provider_errors"] == 6


def test_duplicate_case_ids_are_reported():
    body = rows(N) + rows(1)
    r = L.classify_run("x/a.jsonl", META, body, {})
    assert "1 duplicate case ids" in r["reasons"] and not r["valid_for_analysis"]


def test_committed_ledger_is_current_and_matches_known_facts():
    assert L.main(["--check"]) == 0
    led = json.loads((L.OUT_JSON).read_text(encoding="utf-8"))
    by = {r["run"]: r for r in led["runs"]}
    assert by["v5/lang_pilot/dev_grounded-v3-lang_ar.jsonl"]["provider"] == "never_called"
    assert by["v5/test_grounded-v2_b3.jsonl"]["status"] == "duplicate_not_independent"
    assert by["v5/b4/test_grounded-v2_b4s.jsonl"]["status"] == "valid"
    for stopped in ("validation_grounded-v2_r1", "validation_grounded-v2_r2"):
        assert by[f"v5/lang_pilot/{stopped}.jsonl"]["status"] == "incomplete_stopped"


def test_lang_pilot_refuses_to_score_stopped_validation_runs():
    import sys
    import pytest
    sys.path.insert(0, str(ROOT / "scripts"))
    import r169_lang_pilot as lp
    for tag in ("r1", "r2"):
        with pytest.raises(SystemExit, match="never scored"):
            lp.score(tag)
    assert not (lp.OUT / "validation_score_r1.json").exists()
    assert not (lp.OUT / "validation_score_r2.json").exists()
