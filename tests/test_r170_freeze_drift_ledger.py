#!/usr/bin/env python3
"""R-170 (batch 10): the R-169 freeze-drift ledger is exact, and the runners
still refuse held-out runs on drift (the guard is not relaxed)."""
from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
LEDGER = json.loads((ROOT / "benchmarks" / "r169" / "FREEZE_DRIFT.json").read_text())["entries"]


def _undocumented(version, state, frozen):
    allowed = {(e["key"], e["file"]): (e["frozen"], e["current"]) for e in LEDGER if version in e["benchmarks"]}
    bad = []
    for key in importlib.import_module(f"r169_run_{version}").check_freeze(state, frozen):
        if key != "baseline_dependency_sha256":
            bad.append(key)
            continue
        for p, h in state[key].items():
            if frozen[key].get(p) != h and allowed.get((key, p)) != (frozen[key].get(p), h):
                bad.append((key, p))
    return bad


@pytest.mark.parametrize("version", ["v2", "v3", "v4", "v5"])
def test_current_drift_is_exactly_the_ledger(version):
    run = importlib.import_module(f"r169_run_{version}")
    frozen = json.loads(run.FREEZE.read_text())
    assert _undocumented(version, run.freeze_state(), frozen) == []


@pytest.mark.parametrize("version", ["v2", "v3", "v4", "v5"])
def test_any_further_change_is_caught(version):
    run = importlib.import_module(f"r169_run_{version}")
    frozen = json.loads(run.FREEZE.read_text())
    state = copy.deepcopy(run.freeze_state())
    state["baseline_dependency_sha256"]["agent_civilization/core/llm.py"] = "0" * 16
    assert ("baseline_dependency_sha256", "agent_civilization/core/llm.py") in _undocumented(version, state, frozen)
    state = copy.deepcopy(run.freeze_state())
    state["baseline_dependency_sha256"]["agent_civilization/core/agents/real_dispatcher.py"] = "1" * 16
    assert _undocumented(version, state, frozen)          # a NEW dispatcher change needs a new entry


@pytest.mark.parametrize("version", ["v2", "v3", "v4", "v5"])
def test_runner_guard_still_refuses_held_out_runs_on_drift(version):
    run = importlib.import_module(f"r169_run_{version}")
    drift = run.check_freeze(run.freeze_state(), json.loads(run.FREEZE.read_text()))
    assert drift == ["baseline_dependency_sha256"]
    assert "differs from FREEZE.json" in run.test_split_guard(True, False, True, drift)
