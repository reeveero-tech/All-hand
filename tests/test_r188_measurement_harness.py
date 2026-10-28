"""R-188 (log 312, protocol v2): the sustained-pressure measurement tool's
COMPUTATIONS are pinned by focused unit tests — the v1 run exposed that a
measurement harness can itself be the bug (see FIRST_RUN_APPENDIX.md).

These tests do NOT run the harness (that is the official run's job); they pin
the pure functions it reports from, so a computation defect cannot silently
turn a violated run into a "successful" measurement:

1. window occupancy is filtered by EXPLICIT timestamps + phase (v1 trusted
   the phase tag alone and mixed post-window samples in);
2. first-terminal observation is recorded ONCE per task (earliest wins);
3. completion stats exclude warmup/recovery and DISTRIBUTE final statuses
   (v1 merged failed/cancelled into "completions");
4. verification is set-based: a task never sent to the recipient (loss), a
   duplicate send per correlation, an unexpected terminal status, a non-
   terminal task, a worker exception, an HTTP error, a leaked live task, or
   a failed recovery each make the run INVALID — the verdict is explicit,
   never a silent success.
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_HARNESS = os.path.join(os.path.dirname(HERE), "scripts", "r188_sustained_pressure.py")

spec = importlib.util.spec_from_file_location("r188_sustained_pressure", _HARNESS)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


# ---------------------------------------------------------------- window bounds
def test_r188_window_occupancy_filters_by_explicit_timestamps_and_phase():
    samples = [
        {"t": 10.0, "phase": "sustain", "tasks_pending": 400},
        {"t": 20.0, "phase": "sustain", "tasks_pending": 500},   # inside
        {"t": 20.5, "phase": "sustain", "tasks_pending": 499},  # inside
        {"t": 25.0, "phase": "sustain_tail", "tasks_pending": 300},  # wrong phase
        {"t": 30.0, "phase": "drain", "tasks_pending": 100},    # wrong phase
        {"t": 35.0, "phase": "sustain", "tasks_pending": 200},  # AFTER t1
        {"t": 5.0, "phase": "sustain", "tasks_pending": 900},   # BEFORE t0
        {"t": 21.0, "phase": "sustain", "tasks_pending": None},  # unreadable
    ]
    assert h.window_occupancy(samples, 15.0, 22.0) == [500, 499]


def test_r188_window_occupancy_empty_when_no_sample_in_bounds():
    assert h.window_occupancy([], 0, 10) == []
    samples = [{"t": 50.0, "phase": "sustain", "tasks_pending": 1}]
    assert h.window_occupancy(samples, 0, 10) == []


# ---------------------------------------------------------------- first observation
def test_r188_first_observation_recorded_once_per_task_earliest_wins():
    comps = [
        {"tid": "a", "phase": "fill", "send_t": 1.0, "observed_t": 40.0, "status": "completed"},
        {"tid": "a", "phase": "fill", "send_t": 1.0, "observed_t": 41.0, "status": "completed"},
        {"tid": "b", "phase": "sustain", "send_t": 5.0, "observed_t": 60.0, "status": "completed"},
    ]
    dedup = h.dedupe_first_observations(comps)
    assert len(dedup) == 2
    by_tid = {c["tid"]: c for c in dedup}
    assert by_tid["a"]["observed_t"] == 40.0
    assert by_tid["b"]["observed_t"] == 60.0


def test_r188_first_observation_empty_input():
    assert h.dedupe_first_observations([]) == []


# ---------------------------------------------------------------- completion stats
def test_r188_completion_stats_excludes_warmup_and_recovery_and_distributes_statuses():
    comps = [
        {"tid": "w", "phase": "warmup", "send_t": 0.0, "observed_t": 1.0, "status": "completed"},
        {"tid": "r", "phase": "recovery", "send_t": 0.0, "observed_t": 2.0, "status": "completed"},
        {"tid": "f1", "phase": "fill", "send_t": 10.0, "observed_t": 50.0, "status": "completed"},
        {"tid": "f2", "phase": "fill", "send_t": 10.0, "observed_t": 55.0, "status": "failed"},
        {"tid": "s1", "phase": "sustain", "send_t": 20.0, "observed_t": 60.0, "status": "completed"},
    ]
    stats = h.completion_stats(comps)
    assert stats["n"] == 3  # warmup + recovery excluded
    assert stats["status_distribution"] == {"completed": 2, "failed": 1}
    assert set(stats["by_phase"]) == {"fill", "sustain"}
    assert stats["by_phase"]["fill"]["status_distribution"] == {"completed": 1, "failed": 1}
    # latency over the MEASURED tasks only: 40, 45, 40
    assert stats["latency_send_to_first_observation"]["n"] == 3
    assert stats["latency_send_to_first_observation"]["p50"] == 40.0


def test_r188_completion_stats_empty_input():
    stats = h.completion_stats([])
    assert stats["n"] == 0
    assert stats["latency_send_to_first_observation"]["p50"] is None


# ---------------------------------------------------------------- verdict
def _ok_kwargs():
    return dict(
        corr_by_tid={"t1": "c1", "t2": "c2"},
        sends_by_corr={"c1": 1, "c2": 1},
        completions=[
            {"tid": "t1", "phase": "fill", "send_t": 0, "observed_t": 1, "status": "completed"},
            {"tid": "t2", "phase": "sustain", "send_t": 0, "observed_t": 1, "status": "completed"},
        ],
        worker_errors=[],
        http_errors=[],
        non_terminal=0,
        live_after_drain=0,
        recovery_terminal="completed",
    )


def test_r188_verdict_valid_on_a_clean_run():
    v = h.verify_measurement(**_ok_kwargs())
    assert v["measurement_valid"] is True
    assert v["invalidity_reasons"] == []
    assert v["tasks_expected"] == 2
    assert v["tasks_observed_terminal"] == 2
    assert v["recipient_send_counts_per_correlation"] == {"min": 1, "max": 1}


def test_r188_verdict_invalid_when_a_task_is_never_sent_loss():
    kw = _ok_kwargs()
    kw["sends_by_corr"] = {"c1": 1}  # c2 never sent
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "task_never_sent_to_recipient"


def test_r188_verdict_invalid_on_duplicate_recipient_send():
    kw = _ok_kwargs()
    kw["sends_by_corr"] = {"c1": 1, "c2": 2}  # c2 sent twice = duplicate side effect
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "duplicate_recipient_send"


def test_r188_verdict_invalid_on_unexpected_terminal_status():
    kw = _ok_kwargs()
    kw["completions"][1]["status"] = "failed"
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "unexpected_terminal_status"


def test_r188_verdict_invalid_on_non_terminal_task():
    kw = _ok_kwargs()
    kw["non_terminal"] = 1
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "non_terminal_after_drain"


def test_r188_verdict_invalid_on_worker_exception():
    kw = _ok_kwargs()
    kw["worker_errors"] = [{"worker": "poller-3", "exception": "Boom()"}]
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "worker_exception"


def test_r188_verdict_invalid_on_http_error():
    kw = _ok_kwargs()
    kw["http_errors"] = [{"phase": "sustain", "status": 500}]
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "http_error"


def test_r188_verdict_invalid_on_leaked_live_task_or_failed_recovery():
    kw = _ok_kwargs()
    kw["live_after_drain"] = 1
    assert h.verify_measurement(**kw)["measurement_valid"] is False
    kw = _ok_kwargs()
    kw["recovery_terminal"] = None
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert v["invalidity_reasons"][0]["violation"] == "recovery_task_not_completed"


def test_r188_verdict_reports_all_reasons_together():
    kw = _ok_kwargs()
    kw["non_terminal"] = 2
    kw["worker_errors"] = [{"worker": "submitter-0", "exception": "X"}]
    v = h.verify_measurement(**kw)
    assert v["measurement_valid"] is False
    assert len(v["invalidity_reasons"]) == 2
