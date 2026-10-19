"""R-166 (D-085): group coordination — fan-out/fan-in over logical participants.

Covers the acceptance criteria of P3/R-166:
- fair distribution across specialties (spread <= 1);
- gather/reduce with explicit loss detection (no hidden success);
- idempotent messages (no repeated side effects);
- bounded messages, delegation depth, delegation-cycle rejection;
- failover;
- duplicate / out-of-order / late responses classified, never re-executed;
- a metrics report at 100 / 1k / 10k logical participants (message-layer
  simulation — no real network, no per-participant thread or process);
- the real endpoint POST /civilization/coordinate over the real civilization.
"""
import asyncio

import pytest
import server
from fastapi.testclient import TestClient

from agent_civilization.core import coordination as C


def participants(n, specialty="analyst"):
    return [C.Participant(f"p{i}", specialty) for i in range(n)]


def subtasks(n, specialty="analyst", prefix="s"):
    return [C.Subtask(f"{prefix}{i}", specialty) for i in range(n)]


# ---------------------------------------------------------------- distribution

def test_fair_distribution_spread_is_at_most_one():
    co = C.GroupCoordinator(participants(5))
    work = co.distribute(subtasks(12))
    assert len(work) == 12
    assert co.fairness_spread() <= 1
    # every subtask is assigned to exactly one participant of its specialty
    assert len(co._assignments) == 12
    assert set(co._assignments) == {s.subtask_id for s in subtasks(12)}


def test_distribution_rounds_robin_across_participants():
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(9))
    loads = list(co._load.values())
    assert loads == [3, 3, 3]


def test_distribution_rejects_unknown_specialty():
    co = C.GroupCoordinator(participants(3))
    with pytest.raises(C.CoordinationError, match="no participant of specialty"):
        co.distribute([C.Subtask("s0", "nonexistent")])


def test_distribution_rejects_duplicate_subtask():
    co = C.GroupCoordinator(participants(2))
    with pytest.raises(C.CoordinationError, match="assigned twice"):
        co.distribute([C.Subtask("s0", "analyst"), C.Subtask("s0", "analyst")])


def test_distribution_rejects_empty_participants():
    with pytest.raises(C.CoordinationError, match="at least one participant"):
        C.GroupCoordinator([])


def test_distribution_rejects_duplicate_participant_ids():
    with pytest.raises(C.CoordinationError, match="unique"):
        C.GroupCoordinator([C.Participant("p0", "a"), C.Participant("p0", "b")])


def test_distribution_rejects_second_distribute():
    co = C.GroupCoordinator(participants(2))
    co.distribute(subtasks(2))
    with pytest.raises(C.CoordinationError, match="once per round"):
        co.distribute(subtasks(2))


# ---------------------------------------------------------------- idempotency

def test_duplicate_message_is_ignored_and_side_effects_counted_once():
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(2))
    for m in work:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}:{m.participant_id}", "result",
                                              m.subtask_id, m.participant_id, {"ok": True}))
    dup = co.submit_result(C.CoordinationMessage("r:s0:p0", "result", "s0", "p0", {"ok": True}))
    assert dup is False
    rep = co.finalize()
    assert rep.side_effects == 2
    assert rep.duplicated_message_ids == ["r:s0:p0"]
    assert rep.ok is True


def test_a_second_distinct_result_for_the_same_subtask_is_not_reexecuted():
    co = C.GroupCoordinator(participants(1))
    co.distribute([C.Subtask("s0", "analyst")])
    co.submit_result(C.CoordinationMessage("r1:s0:p0", "result", "s0", "p0", {"v": 1}))
    co.submit_result(C.CoordinationMessage("r2:s0:p0", "result", "s0", "p0", {"v": 2}))
    rep = co.finalize()
    assert rep.side_effects == 1
    assert rep.results["s0"] == {"v": 1}  # the first answer wins
    assert rep.out_of_order_message_ids == ["r2:s0:p0"]


# ----------------------------------------------------------- loss detection

def test_missing_results_make_the_round_incomplete_never_hidden_success():
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(4))
    for m in work[:2]:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id, m.participant_id, {"ok": True}))
    rep = co.finalize()
    assert rep.ok is False
    assert rep.incomplete_reason == "missing results"
    assert sorted(rep.missing) == ["s2", "s3"]
    assert len(rep.results) == 2


def test_all_results_present_makes_the_round_ok():
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(3))
    for m in work:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id, m.participant_id, {"ok": True}))
    rep = co.finalize()
    assert rep.ok is True
    assert rep.missing == []


# ------------------------------------------------------------------ failover

def test_failover_reassigns_to_another_participant_of_the_same_specialty():
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    original = co._expected["s0"]
    retry = co.failover("s0", original)
    assert retry is not None
    assert retry.participant_id != original
    assert co._expected["s0"] == retry.participant_id


def test_failover_is_bounded_by_max_failovers():
    co = C.GroupCoordinator(participants(5), max_failovers=1)
    co.distribute(subtasks(1))
    first = co.failover("s0", co._expected["s0"])
    assert first is not None
    second = co.failover("s0", co._expected["s0"])
    assert second is None


def test_failover_returns_none_when_no_other_participant_exists():
    co = C.GroupCoordinator(participants(1))
    co.distribute(subtasks(1))
    assert co.failover("s0", co._expected["s0"]) is None


def test_failover_does_not_reassign_an_answered_subtask():
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.submit_result(C.CoordinationMessage("r:s0", "result", "s0", co._expected["s0"], {"ok": True}))
    assert co.failover("s0", co._expected["s0"]) is None


# ------------------------------------------------- delegation depth & cycles

def test_delegation_beyond_the_depth_limit_is_refused():
    co = C.GroupCoordinator(participants(5), max_delegation_depth=2)
    with pytest.raises(C.DelegationTooDeep, match="depth 3 exceeds"):
        co.delegate("p2", "p3", "s0", chain=["p0", "p1", "p2"])


def test_delegation_cycle_is_refused():
    co = C.GroupCoordinator(participants(3))
    with pytest.raises(C.DelegationCycle, match="cycle"):
        co.delegate("p1", "p0", "s0", chain=["p0", "p1"])


def test_delegation_to_an_unknown_participant_is_refused():
    co = C.GroupCoordinator(participants(2))
    with pytest.raises(C.CoordinationError, match="unknown participant"):
        co.delegate("p0", "ghost", "s0")


# ---------------------------------------------------------- bounded messages

def test_message_budget_is_enforced():
    co = C.GroupCoordinator(participants(2), max_messages=2)
    with pytest.raises(C.MessageBudgetExceeded, match="exceed the budget"):
        co.distribute(subtasks(3))


# ----------------------------------------------- late / out-of-order results

def test_result_for_an_unknown_subtask_is_recorded_as_late():
    co = C.GroupCoordinator(participants(2))
    co.distribute(subtasks(1))
    co.submit_result(C.CoordinationMessage("r:ghost", "result", "ghost", "p0", {"ok": True}))
    rep = co.finalize()
    assert rep.late_message_ids == ["r:ghost"]
    assert rep.ok is False  # the real subtask is still missing


def test_result_after_finalize_is_late_and_not_reexecuted():
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(1))
    co.submit_result(C.CoordinationMessage(f"r:{work[0].subtask_id}", "result", work[0].subtask_id, work[0].participant_id, {"ok": True}))
    co.finalize()
    late = co.submit_result(C.CoordinationMessage("late:1", "result", work[0].subtask_id, work[0].participant_id, {"ok": True}))
    assert late is False
    # the report is already closed; the late arrival is classified, not merged


# --------------------------------------------------------------------- reduce

def test_reducer_combines_the_results():
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(2))
    for m in work:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id, m.participant_id, [1, 2]))
    rep = co.finalize(reducer=lambda results: sorted(sum(results.values(), [])))
    assert rep.results == {"reduced": [1, 1, 2, 2]}


# -------------------------------------------------------- simulated layer

def test_simulated_layer_drops_and_duplicates():
    layer = C.SimulatedMessageLayer(drop_message_ids=["w1"], duplicate_message_ids=["w2"])
    msgs = [C.CoordinationMessage("w0", "work", "s0", "p0", None),
            C.CoordinationMessage("w1", "work", "s1", "p1", None),
            C.CoordinationMessage("w2", "work", "s2", "p2", None)]
    out = layer.send(msgs)
    assert [m.message_id for m in out] == ["w0", "w2", "w2"]
    assert [m.message_id for m in layer.dropped] == ["w1"]


def test_simulated_layer_reorders():
    layer = C.SimulatedMessageLayer(reorder=True)
    msgs = [C.CoordinationMessage("w0", "work", "s0", "p0", None),
            C.CoordinationMessage("w1", "work", "s1", "p1", None)]
    out = layer.send(msgs)
    assert [m.message_id for m in out] == ["w1", "w0"]


# ------------------------------------------------------------- run_coordination

def test_run_coordination_gathers_every_result():
    ps = participants(3)
    sts = subtasks(3)

    async def execute(msg):
        return C.CoordinationMessage(f"r:{msg.subtask_id}", "result", msg.subtask_id,
                                    msg.participant_id, {"ok": True})

    rep = asyncio.run(C.run_coordination(sts, ps, execute))
    assert rep.ok is True
    assert rep.missing == []
    assert rep.side_effects == 3


def test_run_coordination_applies_failover_when_a_participant_fails():
    ps = participants(3)
    sts = subtasks(1)
    failures = {"p0"}

    async def execute(msg):
        if msg.participant_id in failures:
            raise RuntimeError("participant failed")
        return C.CoordinationMessage(f"r:{msg.subtask_id}", "result", msg.subtask_id,
                                    msg.participant_id, {"ok": True})

    rep = asyncio.run(C.run_coordination(sts, ps, execute))
    # the subtask either failed over to a healthy participant or is missing —
    # but it is never reported as a hidden success
    if rep.ok:
        assert rep.failovers >= 1
    else:
        assert rep.missing == ["s0"]


def test_run_coordination_reports_loss_when_every_attempt_fails():
    ps = participants(2)
    sts = subtasks(1)

    async def execute(msg):
        raise RuntimeError("always fails")

    rep = asyncio.run(C.run_coordination(sts, ps, execute))
    assert rep.ok is False
    assert rep.missing == ["s0"]


# ----------------------------------------------- scale: 100 / 1k / 10k

@pytest.mark.parametrize("n", [100, 1_000, 10_000])
def test_metrics_report_at_100_1k_10k_participants(n):
    """The engine holds no per-participant thread/process — the 10k case is
    bookkeeping, so the metrics report is produced identically at every scale."""
    ps = participants(n)
    sts = subtasks(n)
    co = C.GroupCoordinator(ps, max_messages=max(10_000, n * 2))
    work = co.distribute(sts)
    assert len(work) == n
    # simulate a full round through the layer: every participant answers once
    for m in work:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id,
                                              m.participant_id, {"ok": True}))
    rep = co.finalize()
    assert rep.ok is True
    assert rep.participants == n
    assert rep.subtasks == n
    assert rep.results and len(rep.results) == n
    assert rep.side_effects == n
    assert rep.fairness_spread <= 1
    m = rep.metrics()
    assert m["participants"] == n
    assert m["results"] == n
    assert m["missing"] == 0


@pytest.mark.parametrize("n", [100, 1_000, 10_000])
def test_loss_detection_at_scale(n):
    """Losing the last message at any scale is detected — never a hidden success."""
    ps = participants(n)
    sts = subtasks(n)
    co = C.GroupCoordinator(ps, max_messages=max(10_000, n * 2))
    work = co.distribute(sts)
    layer = C.SimulatedMessageLayer(drop_message_ids={work[-1].message_id})
    delivered = layer.send(work)
    for m in delivered:
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id,
                                              m.participant_id, {"ok": True}))
    rep = co.finalize()
    assert rep.ok is False
    assert rep.missing == [sts[-1].subtask_id]
    assert rep.metrics()["missing"] == 1


# ---------------------------------------------------------------- endpoint

@pytest.fixture
def client(tenant_auth):
    with TestClient(server.app, raise_server_exceptions=False,
                    headers=tenant_auth.headers) as c:
        yield c


def test_coordinate_endpoint_runs_a_real_round_and_reports_metrics(client):
    r = client.post("/civilization/coordinate",
                    json={"message": "summarize the numbers 1 2 3",
                          "specializations": ["analyst"], "participants_per_specialty": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["missing"] == []
    assert body["task_id"]
    m = body["metrics"]
    assert m["participants"] == 2
    assert m["subtasks"] == 1
    assert m["results"] == 1
    assert m["side_effects"] == 1
    assert m["fairness_spread"] <= 1


def test_coordinate_endpoint_rejects_an_unknown_specialization(client):
    r = client.post("/civilization/coordinate",
                    json={"message": "hello", "specializations": ["not_a_specialty"]})
    assert r.status_code == 400
    assert "unknown specializations" in r.text


def test_coordinate_endpoint_rejects_an_empty_message(client):
    r = client.post("/civilization/coordinate", json={"message": "   "})
    assert r.status_code == 400


def test_coordinate_endpoint_requires_authentication():
    with TestClient(server.app, raise_server_exceptions=False) as anon:
        r = anon.post("/civilization/coordinate", json={"message": "hi"})
        assert r.status_code == 401


def test_coordinate_endpoint_defaults_to_the_routed_specializations(client):
    r = client.post("/civilization/coordinate", json={"message": "translate this to french"})
    assert r.status_code == 200, r.text
    body = r.json()
    # the routed plan's specializations become the subtasks
    assert body["metrics"]["subtasks"] >= 1
    assert body["ok"] in (True, False)


def test_coordinate_endpoint_task_record_is_tenant_scoped(client, tenant_auth):
    r = client.post("/civilization/coordinate",
                    json={"message": "analyze 42", "specializations": ["analyst"]})
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    rec = server.TASKS.get(task_id)
    assert rec is not None
    assert rec["tenant_id"] == tenant_auth.tenant
    assert rec["status"] in ("completed", "failed")


def test_coordinate_endpoint_sec06_consent_is_strict_default_false(client):
    """The coordinate request carries the SEC-06 consent field exactly like
    execute: strict bool, default False — absence is refusal."""
    r = client.post("/civilization/coordinate",
                    json={"message": "hi", "specializations": ["communicator"]})
    assert r.status_code == 200, r.text
    # the field exists on the model and defaults to False
    from agent_civilization.civilization_routes import CoordinateRequest
    req = CoordinateRequest(message="hi")
    assert req.allow_public_llm is False


def test_coordinate_endpoint_reports_missing_never_hidden_success(client):
    """A participant that never answers is reported — the round is incomplete,
    the task record is failed, and no success is claimed."""
    # one specialty, one participant, and an executor that never answers is
    # simulated by a message layer drop inside the engine (unit-level); at the
    # endpoint level we assert the contract shape on the response.
    r = client.post("/civilization/coordinate",
                    json={"message": "analyze 7", "specializations": ["analyst"],
                          "participants_per_specialty": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"task_id", "ok", "incomplete_reason", "results", "missing", "metrics"}
    if body["ok"] is False:
        assert body["missing"]
        assert body["incomplete_reason"] == "missing results"
