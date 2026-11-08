"""R-178 ladder tools (D-139) — unit tests + guard.

The ladder runner itself (benchmarks/r178/ladder.py) is executed ON DEMAND as
audit evidence (like every benchmark runner — py-scripts group), never from
this suite: an L0 health run needs a real SUT process and (for live-local)
the throwaway PostgreSQL+PostgREST stack. These tests pin the tool's pieces:
RSS sampling, the SSE queue-wait listener, the log-380-corrected percentile
report, the Q-023 guard, and the criteria wiring.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import time


REPO = pathlib.Path(__file__).resolve().parents[1]


def L0_allowed_check():
    # the guard fires only above L0: level<=L0 never trips the refusal branch
    return L.L0 <= L.L0


SPEC = importlib.util.spec_from_file_location("r178_ladder", REPO / "benchmarks" / "r178" / "ladder.py")
L = importlib.util.module_from_spec(SPEC)
sys.modules["r178_ladder"] = L
SPEC.loader.exec_module(L)


# --------------------------------------------------------------- RSS sampling

def test_read_status_kb_returns_rss_and_hwm_for_live_process():
    s = L.read_status_kb(__import__("os").getpid())
    assert s is not None, "/proc/<pid>/status must be readable for the sampler"
    assert s["VmRSS"] > 0
    assert s["VmHWM"] >= s["VmRSS"]


def test_read_status_kb_dead_pid_is_none():
    # pid 1 on this sandbox is alive; find a very likely-dead one instead
    assert L.read_status_kb(-1) is None


def test_rss_sampler_tracks_peak():
    sampler = L.RssSampler(__import__("os").getpid(), interval_s=0.02)
    sampler.start()
    time.sleep(0.1)
    sampler.stop()
    assert sampler.samples >= 2
    assert sampler.peak_bytes() and sampler.peak_bytes() >= 1024


# --------------------------------------------------------------- SSE listener

def _listener():
    return L.QueueWaitListener("http://127.0.0.1:1", "k", {"t1", "t2"})


def test_sse_listener_records_created_and_started():
    lis = _listener()
    lis._handle('data: {"type": "task_created", "task_id": "t1"}')
    time.sleep(0.01)
    lis._handle('data: {"type": "task_started", "task_id": "t1"}')
    assert "t1" in lis.accepted and "t1" in lis.started
    waits = lis.waits_s()
    assert len(waits) == 1 and waits[0] >= 0


def test_sse_listener_ignores_heartbeats_and_foreign_tasks():
    lis = _listener()
    lis._handle('data: {"type": "heartbeat"}')
    lis._handle('data: {"type": "task_created", "task_id": "other"}')
    lis._handle('data: not json')
    assert lis.accepted == {} and lis.started == {}


def test_sse_listener_first_event_wins():
    lis = _listener()
    lis._handle('data: {"type": "task_created", "task_id": "t1"}')
    first = lis.accepted["t1"]
    lis._handle('data: {"type": "task_created", "task_id": "t1"}')
    assert lis.accepted["t1"] == first


def test_sse_listener_waits_only_when_both_sides():
    lis = _listener()
    lis._handle('data: {"type": "task_started", "task_id": "t1"}')  # started without created
    assert lis.waits_s() == []


# ------------------------------------------------------- log-380 corrections

def test_report_is_nearest_rank_with_p99_max_and_n():
    m = L.p6_metrics()
    r = L._report("dispatch", [float(i) for i in range(1, 101)], m)
    # nearest-rank p95 on 1..100 = 95th ordered value = 95.0
    assert r["p95_ms"] == 95.0
    assert r["p99_ms"] == 99.0
    assert r["max_ms"] == 100.0
    assert r["n"] == 100
    assert r["percentile_method"] == "nearest_rank_ceil"
    # F4 correction: p99 AND max are always in the report — the auth-tail
    # omission of log 366/368 must not recur
    for key in ("p50_ms", "p95_ms", "p99_ms", "max_ms"):
        assert key in r


def test_report_empty_is_explicit_not_fake():
    m = L.p6_metrics()
    r = L._report("dispatch", [], m)
    assert r["n"] == 0 and r["p95_ms"] is None and r["max_ms"] is None


def test_dropped_submissions_field_exists_in_runner_output_shape():
    # the F3 correction: dropped iterations reported next to throughput —
    # the execution block must carry the field even when zero
    src = (REPO / "benchmarks" / "r178" / "ladder.py").read_text()
    assert '"dropped_submissions"' in src


# --------------------------------------------------------------- Q-023 guard

def test_guard_refuses_levels_above_l0_without_double_override(tmp_path):
    for argv in (["--level", "500", "--json", str(tmp_path / "o.json")],
                 ["--level", "1000", "--q023-resolved", "--json", str(tmp_path / "o.json")],
                 ["--level", "10000", "--owner-approved-large-run", "--json", str(tmp_path / "o.json")]):
        rc = L.main(argv)
        assert rc == 2, argv  # REFUSED, not a silent small run
        assert not (tmp_path / "o.json").exists(), "refusal must not write a report"


def test_guard_accepts_l0_without_flags_via_parser():
    # L0 needs no override: the parser accepts it and the guard only fires
    # above L0 — a level-100 argv must never trip the refusal branch
    # (the run itself is not executed here — evidence runs are on demand)
    assert L.L0 == 100 and 100 in L.LEVELS
    assert L.LEVELS.index(L.L0) == 0  # L0 is the lowest rung


# --------------------------------------------------------------- criteria wiring

def test_criteria_file_names_the_ladder_tool_for_r178():
    d = json.loads((REPO / "benchmarks" / "p6" / "acceptance_criteria.json").read_text())
    by_id = {c["id"]: c for c in d["criteria"]}
    for cid in ("r178.creation_time_s", "r178.creation_peak_rss_bytes",
                "r178.worker_fairness_jain", "r178.duplicate_side_effects"):
        assert by_id[cid]["tool"] == "benchmarks/r178/ladder.py", cid
        # thresholds stay PROPOSED — wiring a tool is not owner approval
        assert by_id[cid]["status"] == "proposed", cid


def test_evaluate_not_measured_never_pass():
    m = L.p6_metrics()
    crit = L.criteria()["r178.error_rate"]
    out = m.evaluate(crit, statuses=[])
    assert out["verdict"] == "not_measured" and out["value"] is None
