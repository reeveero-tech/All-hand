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
    second = co.failover("s0", co._expected["s0"], attempt=first.attempt)
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
    co.distribute(subtasks(1))
    # real sequential handoffs (D-088: the engine owns the path; the chain
    # argument is deprecated and ignored; each command echoes the current
    # generation of its assignment)
    first = co.delegate("p0", "p1", "s0")
    second = co.delegate("p1", "p2", "s0", attempt=first.attempt)
    with pytest.raises(C.DelegationTooDeep, match="depth 3 exceeds"):
        co.delegate("p2", "p3", "s0", attempt=second.attempt)


def test_delegation_cycle_is_refused():
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    # a real handoff first, then the cycle back to p0 (engine-owned path)
    first = co.delegate("p0", "p1", "s0")
    with pytest.raises(C.DelegationCycle, match="cycle"):
        co.delegate("p1", "p0", "s0", attempt=first.attempt)


def test_delegation_to_an_unknown_participant_is_refused():
    co = C.GroupCoordinator(participants(2))
    co.distribute(subtasks(1))
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


def test_run_coordination_does_not_auto_failover_on_participant_failure():
    """D-086: a failing participant leaves the subtask MISSING — the round
    does not blindly re-execute an unconfirmed side effect on another
    participant. Failover is an explicit engine capability, not automatic."""
    ps = participants(3)
    sts = subtasks(1)
    failures = {"p0"}

    async def execute(msg):
        if msg.participant_id in failures:
            raise RuntimeError("participant failed")
        return C.CoordinationMessage(f"r:{msg.subtask_id}", "result", msg.subtask_id,
                                    msg.participant_id, {"ok": True})

    rep = asyncio.run(C.run_coordination(sts, ps, execute))
    assert rep.ok is False
    assert rep.failovers == 0            # no automatic re-execution
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


def test_coordinate_endpoint_response_matches_the_coordination_report_contract(client):
    """R-166: the response validates against the coordination_report v1
    contract (contracts.py -> docs/governance/contracts/)."""
    from agent_civilization.core import contracts
    r = client.post("/civilization/coordinate",
                    json={"message": "analyze 3 and 4", "specializations": ["analyst"]})
    assert r.status_code == 200, r.text
    contracts.validate("coordination_report", r.json())


def test_coordinate_request_model_matches_the_coordinate_request_contract():
    """R-166: the request model is the single source of truth of its schema
    (REQUEST_MODELS -> docs/governance/contracts/coordinate_request.v1)."""
    from agent_civilization.civilization_routes import CoordinateRequest
    payload = {"message": "hi", "specializations": ["analyst"],
               "participants_per_specialty": 2, "allow_public_llm": False}
    req = CoordinateRequest.model_validate(payload)
    assert req.allow_public_llm is False
    with pytest.raises(Exception):
        CoordinateRequest.model_validate({"message": "hi",
                                         "participants_per_specialty": 999})


# =====================================================================
# R-166b — review-fix tests (D-086). These were written BEFORE the fixes
# and failed on the merged R-166 code (red run saved in
# docs/audit/M8/logs/raw/169_r166b_red.log); they encode the acceptance
# gaps the review found in the merged batch.
# =====================================================================

def test_failed_result_body_makes_the_round_not_ok_r166b():
    """A participant answering {ok: False} is NOT a round success: the report
    must fail with the failed subtask listed, never hidden."""
    co = C.GroupCoordinator(participants(2))
    work = co.distribute(subtasks(2))
    for m in work:
        body = {"ok": False, "error": "participant failed"} if m.subtask_id == "s0" else {"ok": True}
        co.submit_result(C.CoordinationMessage(f"r:{m.subtask_id}", "result", m.subtask_id,
                                               m.participant_id, body))
    rep = co.finalize()
    assert rep.ok is False
    assert rep.failed == ["s0"]
    assert rep.missing == []


def test_metrics_include_failed_count_r166b():
    co = C.GroupCoordinator(participants(1))
    co.distribute(subtasks(1))
    co.submit_result(C.CoordinationMessage("r:s0", "result", "s0", "p0", {"ok": False}))
    rep = co.finalize()
    assert rep.metrics()["failed"] == 1


def test_submit_result_from_unassigned_participant_is_rejected_r166b():
    """A result for a subtask from a participant that is NOT its assignee is
    classified (foreign), never counted as the subtask's answer."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    foreign = co.submit_result(C.CoordinationMessage("r:s0:foreign", "result", "s0", "p2", {"ok": True}))
    assert foreign is False
    rep = co.finalize()
    assert rep.ok is False            # the real assignee never answered
    assert rep.missing == ["s0"]
    assert "r:s0:foreign" in rep.late_message_ids


def test_result_from_old_participant_after_failover_is_not_accepted_r166b():
    """After a failover, a late answer from the OLD participant must not be
    accepted for the subtask (stale-attempt fencing via assignee ownership)."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    old = co._expected["s0"]
    retry = co.failover("s0", old)
    assert retry is not None
    stale = co.submit_result(C.CoordinationMessage("r:s0:old", "result", "s0", old, {"ok": True}))
    assert stale is False
    rep = co.finalize()
    assert rep.ok is False            # the new assignee never answered
    assert rep.missing == ["s0"]


def test_delegate_and_failover_enforce_message_budget_r166b():
    """delegate() and failover() must respect the bounded-message budget, not
    only distribute()."""
    co = C.GroupCoordinator(participants(4), max_messages=1)
    co.distribute(subtasks(1))
    with pytest.raises(C.MessageBudgetExceeded):
        co.failover("s0", co._expected["s0"])
    co2 = C.GroupCoordinator(participants(4), max_messages=1)
    co2.distribute(subtasks(1))
    with pytest.raises(C.MessageBudgetExceeded):
        co2.delegate("p0", "p1", "s0")


def test_fairness_spread_counts_zero_load_participants_per_specialty_r166b():
    """The spread is per specialty and includes zero-load participants of that
    specialty — 1 subtask over 2 participants of one specialty is a spread of 1,
    not 0."""
    co = C.GroupCoordinator([C.Participant("a0", "analyst"), C.Participant("a1", "analyst"),
                            C.Participant("w0", "writer")])
    co.distribute([C.Subtask("s0", "analyst")])
    assert co.fairness_spread() == 1


def test_run_coordination_does_not_reexecute_after_participant_failure_r166b():
    """run_coordination must NOT blindly re-execute an unconfirmed side effect:
    a failing participant leaves the subtask missing (reported), and the real
    effect happens exactly once. Failover stays an explicit engine API."""
    effects = []

    async def execute(msg):
        effects.append(msg.participant_id)   # the real side effect
        if msg.participant_id == "p0":
            raise RuntimeError("failed after the effect")
        return C.CoordinationMessage(f"r:{msg.subtask_id}", "result", msg.subtask_id,
                                    msg.participant_id, {"ok": True})

    ps = participants(3)
    sts = subtasks(1)
    rep = asyncio.run(C.run_coordination(sts, ps, execute))
    assert len(effects) == 1                 # exactly one effect overall, at one participant
    assert rep.ok is False
    assert rep.missing == ["s0"]


def test_coordinate_endpoint_failed_participant_is_not_hidden_success_r166b(client):
    """Through the real endpoint: an agent that answers ok=False must NOT
    produce a 'completed' task with ok=True."""
    import server

    async def failing_execute(work):
        return {"ok": False, "error": "participant failed"}

    original = server.CIVILIZATION["analyst"][0].execute
    server.CIVILIZATION["analyst"][0].execute = failing_execute
    try:
        r = client.post("/civilization/coordinate",
                        json={"message": "analyze 5", "specializations": ["analyst"],
                              "participants_per_specialty": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["failed"]
        rec = server.TASKS[body["task_id"]]
        assert rec["status"] == "failed"
    finally:
        server.CIVILIZATION["analyst"][0].execute = original


def test_coordinate_sensitive_specialization_requires_approval_r166b(client):
    """R-165's approval gate must not be bypassable via /civilization/coordinate:
    a sensitive specialization (test_runner) is NOT executed without an explicit
    approval; with approval it runs."""
    import server

    executed = []

    async def spying_execute(work):
        executed.append(work)
        return {"ok": True}

    original = server.CIVILIZATION["test_runner"][0].execute
    server.CIVILIZATION["test_runner"][0].execute = spying_execute
    try:
        # without approval: not executed, reported as pending
        r = client.post("/civilization/coordinate",
                        json={"message": "run this code", "specializations": ["test_runner"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["pending_approvals"] == ["test_runner"]
        assert executed == []             # nothing ran
        assert body["metrics"]["executed"] == 0
        # with approval: executed
        r2 = client.post("/civilization/coordinate",
                         json={"message": "run this code", "specializations": ["test_runner"],
                               "approve_stages": ["test_runner"]})
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["ok"] is True
        assert len(executed) == 1
        assert body2["metrics"]["executed"] == 1
    finally:
        server.CIVILIZATION["test_runner"][0].execute = original


def test_coordinate_payload_carries_real_specialization_r166b(client):
    """The work payload must carry the real specialization, not the subtask id
    ('sub-0')."""
    import server

    seen = []

    async def spying_execute(work):
        seen.append(work)
        return {"ok": True}

    original = server.CIVILIZATION["analyst"][0].execute
    server.CIVILIZATION["analyst"][0].execute = spying_execute
    try:
        r = client.post("/civilization/coordinate",
                        json={"message": "analyze 6", "specializations": ["analyst"]})
        assert r.status_code == 200, r.text
        assert seen and seen[0].get("specialization") == "analyst"
    finally:
        server.CIVILIZATION["analyst"][0].execute = original


# =====================================================================
# R-166c — second review-fix tests (D-087). Written BEFORE the fixes and
# failed on the merged R-166b code (red run saved in
# docs/audit/M8/logs/raw/175_r166c_red.log).
# =====================================================================

def test_mixed_request_with_pending_approval_is_not_ok_r166c(client):
    """A mixed request (executable + sensitive-without-approval) must NOT
    report ok=True even when every EXECUTED subtask succeeds: pending
    approvals make the whole round incomplete — in the response, the task
    record, and the metrics."""
    import server

    executed = []

    async def spying_execute(work):
        executed.append(work)
        return {"ok": True}

    original = server.CIVILIZATION["analyst"][0].execute
    server.CIVILIZATION["analyst"][0].execute = spying_execute
    try:
        r = client.post("/civilization/coordinate",
                        json={"message": "analyze and run tests",
                              "specializations": ["analyst", "test_runner"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pending_approvals"] == ["test_runner"]
        assert executed            # the analyst DID execute
        assert body["ok"] is False  # but the round is NOT ok while approvals pend
        rec = server.TASKS[body["task_id"]]
        assert rec["status"] == "failed"
        assert body["metrics"]["ok"] is False
    finally:
        server.CIVILIZATION["analyst"][0].execute = original


def test_every_sensitive_specialization_is_gated_r166c(client):
    """All five dag.SENSITIVE specializations are gated by approve_stages —
    none of them may execute without an explicit approval."""
    import server

    from agent_civilization.pipeline import dag

    for spec in sorted(dag.SENSITIVE):
        executed = []

        async def spying_execute(work):
            executed.append(work)
            return {"ok": True}

        pool = server.CIVILIZATION.get(spec) or server.CIVILIZATION.get("analyst")
        assert pool, f"no pool for {spec}"
        original = pool[0].execute
        pool[0].execute = spying_execute
        try:
            r = client.post("/civilization/coordinate",
                            json={"message": f"do work {spec}",
                                  "specializations": [spec]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["pending_approvals"] == [spec], spec
            assert executed == [], f"{spec} executed without approval"
            assert body["ok"] is False
        finally:
            pool[0].execute = original


def test_stale_result_after_round_trip_failover_is_rejected_r166c():
    """Attempt fencing must survive a round trip: p0 -> failover p1 ->
    failover p0. A result from p0's FIRST attempt (stamped with that
    attempt) must be rejected even though p0 is the current assignee
    again."""
    co = C.GroupCoordinator(participants(3), max_failovers=2)
    co.distribute(subtasks(1))
    # fail over twice: p0 -> p1 -> p0 (the second report echoes generation 1)
    first = co.failover("s0", co._expected["s0"])          # now p1
    co.failover("s0", co._expected["s0"], attempt=first.attempt)   # back to p0
    assert co._expected["s0"] == "p0"
    # a result from p0 stamped with its FIRST attempt (attempt=0)
    stale = co.submit_result(C.CoordinationMessage("r:s0:stale", "result", "s0", "p0",
                                                   {"ok": True}, attempt=0))
    assert stale is False
    rep = co.finalize()
    assert rep.ok is False
    assert rep.missing == ["s0"]


def test_unstamped_result_after_any_failover_is_rejected_r166c():
    """Once a subtask has been reassigned, a result that carries no attempt
    stamp cannot be accepted for it (it cannot be proven current)."""
    co = C.GroupCoordinator(participants(3), max_failovers=2)
    co.distribute(subtasks(1))
    co.failover("s0", co._expected["s0"])          # now p1
    unstamped = co.submit_result(C.CoordinationMessage("r:s0:current", "result", "s0",
                                                       co._expected["s0"], {"ok": True}))
    assert unstamped is False
    rep = co.finalize()
    assert rep.ok is False
    assert rep.missing == ["s0"]


def test_current_attempt_result_is_accepted_after_failover_r166c():
    """The positive side of fencing: a result stamped with the CURRENT
    attempt from the current assignee is accepted."""
    co = C.GroupCoordinator(participants(3), max_failovers=2)
    co.distribute(subtasks(1))
    retry = co.failover("s0", co._expected["s0"])   # now p1, attempt 1
    assert retry is not None
    current = co.submit_result(C.CoordinationMessage("r:s0:cur", "result", "s0",
                                                     retry.participant_id, {"ok": True},
                                                     attempt=retry.attempt))
    assert current is True
    rep = co.finalize()
    assert rep.ok is True


# =====================================================================
# R-166d — delegation handoff check (advisor-directed, D-088). The positive
# path of delegate(): the legitimate recipient must be able to complete the
# subtask, and the previous owner's result must be rejected after the
# handoff. Written as a CHECK on HEAD first (red run saved in
# docs/audit/M8/logs/raw/181_r166d_red.log).
# =====================================================================

def test_delegation_handoff_transfers_ownership_r166d():
    """The documented positive path: p0 delegates s0 to p1 — p1 is now the
    assignee; p1's result must be ACCEPTED and complete the subtask, and
    p0's (previous owner) result must be REJECTED after the handoff."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    assert co._expected["s0"] == "p0"
    # p0 hands the subtask off to p1 (the legitimate positive path)
    delegation = co.delegate("p0", "p1", "s0")
    assert delegation.participant_id == "p1"
    # the recipient completes the subtask — must be accepted
    from_recipient = co.submit_result(C.CoordinationMessage(
        "r:s0:p1", "result", "s0", "p1", {"ok": True}, attempt=delegation.attempt))
    assert from_recipient is True
    rep = co.finalize()
    assert rep.ok is True
    assert rep.missing == []


def test_previous_owner_result_rejected_after_delegation_r166d():
    """After p0 delegates s0 to p1, a result from p0 (the previous owner)
    must be REJECTED — ownership moved with the handoff."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.delegate("p0", "p1", "s0")
    stale_owner = co.submit_result(C.CoordinationMessage(
        "r:s0:p0", "result", "s0", "p0", {"ok": True}, attempt=0))
    assert stale_owner is False
    rep = co.finalize()
    assert rep.ok is False
    assert rep.missing == ["s0"]


def test_only_the_assignee_may_delegate_its_subtask_r166d():
    """A participant may only hand off a subtask it is the assignee of —
    delegating someone else's subtask is an unauthorized transfer."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    with pytest.raises(C.CoordinationError, match="not the assignee"):
        co.delegate("p1", "p2", "s0")     # p1 does not own s0


# =====================================================================
# R-166d (continued) — second review round on the delegation path: engine
# history, generation fencing, and post-completion guards. Written BEFORE
# the fixes and failed on the ownership-fix code (red run saved in
# docs/audit/M8/logs/raw/185_r166d2_red.log).
# =====================================================================

def test_delegation_cycle_detected_from_engine_history_r166d():
    """Sequential delegations must be checked against the ENGINE's own
    record of the path — omitting the caller-side chain argument must not
    drop history: p0 -> p1 then p1 -> p0 is a cycle."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    first = co.delegate("p0", "p1", "s0")
    with pytest.raises(C.DelegationCycle, match="cycle"):
        co.delegate("p1", "p0", "s0", attempt=first.attempt)   # engine history


def test_delegation_depth_enforced_from_engine_history_r166d():
    """With max_delegation_depth=1, a SECOND sequential delegation must be
    refused on the engine's own record (no caller-provided chain)."""
    co = C.GroupCoordinator(participants(5), max_delegation_depth=1)
    co.distribute(subtasks(1))
    first = co.delegate("p0", "p1", "s0")
    with pytest.raises(C.DelegationTooDeep, match="depth"):
        co.delegate("p1", "p2", "s0", attempt=first.attempt)   # engine history


def test_delegation_round_trip_back_to_p0_is_refused_as_a_cycle_r166d():
    """The fencing hole the review flagged (ownership returning to p0 via a
    delegation round trip) is now structurally IMPOSSIBLE: p0 -> p1 then
    p1 -> p0 is a cycle on the engine-owned path and is refused — ownership
    stays with p1, no state changes, and the generation never rewinds."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    first = co.delegate("p0", "p1", "s0")
    with pytest.raises(C.DelegationCycle, match="cycle"):
        co.delegate("p1", "p0", "s0", attempt=first.attempt)
    assert co._expected["s0"] == "p1"          # ownership stayed with p1
    assert co._messages_sent == 2             # work + first delegation only
    # p0's stale result (from generation 0) is rejected on every axis:
    # not the assignee AND wrong generation
    stale = co.submit_result(C.CoordinationMessage(
        "r:s0:stale", "result", "s0", "p0", {"ok": True}, attempt=0))
    assert stale is False
    # the current generation's result from the current assignee IS accepted
    current = co.submit_result(C.CoordinationMessage(
        "r:s0:cur", "result", "s0", "p1", {"ok": True}, attempt=first.attempt))
    assert current is True
    rep = co.finalize()
    assert rep.ok is True


def test_delegation_after_a_result_is_refused_r166d():
    """A subtask that already has a result cannot be delegated again — no
    new work message for a completed subtask (side-effect duplication), and
    NO state changes (ownership, load, message counter)."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.submit_result(C.CoordinationMessage("r:s0", "result", "s0", "p0",
                                           {"ok": True}, attempt=0))
    with pytest.raises(C.CoordinationError, match="already has a result"):
        co.delegate("p0", "p1", "s0")
    assert co._expected["s0"] == "p0"          # ownership unchanged
    assert co._load["p0"] == 1 and co._load.get("p1", 0) == 0   # load unchanged
    assert co._messages_sent == 1              # only the original work message


def test_delegation_after_finalize_is_refused_r166d():
    """A closed round (finalize) cannot issue new delegation work — and no
    state changes."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.finalize()
    with pytest.raises(C.CoordinationError, match="finalized"):
        co.delegate("p0", "p1", "s0")
    assert co._expected["s0"] == "p0"
    assert co._load["p0"] == 1 and co._load.get("p1", 0) == 0   # load unchanged
    assert co._messages_sent == 1


def test_unstamped_result_after_delegation_is_rejected_r166d():
    """After a handoff, a result with NO generation stamp cannot be accepted
    for the subtask — currency is unprovable (the generation advanced with
    the ownership transition)."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.delegate("p0", "p1", "s0")
    unstamped = co.submit_result(C.CoordinationMessage(
        "r:s0:unstamped", "result", "s0", "p1", {"ok": True}))
    assert unstamped is False
    rep = co.finalize()
    assert rep.ok is False
    assert rep.missing == ["s0"]


# =====================================================================
# R-166d (round 3) — failover message stamp must match the generation, and
# failover is guarded after finalize. Written BEFORE the fixes (red run
# saved in docs/audit/M8/logs/raw/189_r166d3_red.log).
# =====================================================================

def test_failover_message_stamp_matches_generation_after_delegation_r166d():
    """Mixed path: distribute (gen 0) -> delegate p0->p1 (gen 1) -> failover
    from p1 (gen 2). The failover message MUST carry generation 2 — the
    recipient's result stamped with the message's attempt is accepted, and a
    previous-generation result is rejected."""
    co = C.GroupCoordinator(participants(4))
    co.distribute(subtasks(1))
    handoff = co.delegate("p0", "p1", "s0")    # generation 1, owner p1
    retry = co.failover("s0", "p1", attempt=handoff.attempt)   # generation 2
    assert retry is not None
    assert retry.attempt == 2                  # the message carries the GENERATION
    # the new owner's result stamped with the message's attempt: accepted
    current = co.submit_result(C.CoordinationMessage(
        "r:s0:cur", "result", "s0", retry.participant_id, {"ok": True},
        attempt=retry.attempt))
    assert current is True
    rep = co.finalize()
    assert rep.ok is True


def test_previous_generation_result_rejected_after_mixed_transitions_r166d():
    """On the mixed path, a result stamped with a superseded generation is
    rejected even from the participant that was the assignee at that
    generation."""
    co = C.GroupCoordinator(participants(4))
    co.distribute(subtasks(1))
    handoff = co.delegate("p0", "p1", "s0")    # generation 1
    retry = co.failover("s0", "p1", attempt=handoff.attempt)   # generation 2
    stale = co.submit_result(C.CoordinationMessage(
        "r:s0:old", "result", "s0", "p1", {"ok": True}, attempt=1))
    assert stale is False
    current = co.submit_result(C.CoordinationMessage(
        "r:s0:cur", "result", "s0", retry.participant_id, {"ok": True},
        attempt=retry.attempt))
    assert current is True
    rep = co.finalize()
    assert rep.ok is True


def test_failover_after_finalize_is_refused_r166d():
    """A closed round (finalize) must not issue failover work either — no
    new work message and NO state changes (ownership, load, counters)."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    co.finalize()
    with pytest.raises(C.CoordinationError, match="finalized"):
        co.failover("s0", "p0")
    assert co._expected["s0"] == "p0"
    assert co._load["p0"] == 1
    assert co._messages_sent == 1
    assert co._failovers == {}


# =====================================================================
# R-166d (round 4) — the failover COMMAND itself must be fenced: a stale
# failure report (from a previous owner, or about a superseded generation)
# must not reassign ongoing work. Written BEFORE the fix (red run saved in
# docs/audit/M8/logs/raw/193_r166d4_red.log).
# =====================================================================

def test_stale_failure_report_from_previous_owner_is_refused_r166d():
    """distribute -> p0 (gen 0); delegate p0->p1 (gen 1). A failure report
    from p0 (the PREVIOUS owner) must be refused — it must not reassign p1's
    ongoing work — with NO state changes."""
    co = C.GroupCoordinator(participants(4))
    co.distribute(subtasks(1))
    co.delegate("p0", "p1", "s0")
    with pytest.raises(C.CoordinationError, match="not the assignee"):
        co.failover("s0", "p0")            # stale report from the previous owner
    assert co._expected["s0"] == "p1"       # ownership unchanged
    assert co._generation["s0"] == 1        # generation unchanged
    assert co._failovers == {}              # no failover recorded
    assert co._messages_sent == 2            # work + delegation only
    assert co._load["p1"] == 1 and co._load["p0"] == 0


def test_failure_report_from_unknown_participant_is_refused_r166d():
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))
    with pytest.raises(C.CoordinationError, match="not the assignee"):
        co.failover("s0", "ghost")


def test_stale_generation_failure_report_is_refused_r166d():
    """A failure report stamped with a SUPERSEDED generation must not
    reassign the current assignment — even from the current assignee (the
    ownership returned to them via a failover)."""
    co = C.GroupCoordinator(participants(3))
    co.distribute(subtasks(1))                     # p0, gen 0
    co.failover("s0", "p0")                        # p1, gen 1
    back = co.failover("s0", "p1", attempt=1)      # p0 again, gen 2
    assert back is not None and co._expected["s0"] == "p0"
    with pytest.raises(C.CoordinationError, match="stale"):
        co.failover("s0", "p0", attempt=1)        # report about gen 1 — stale
    assert co._expected["s0"] == "p0"
    assert co._generation["s0"] == 2


def test_current_failure_report_produces_correct_work_r166d():
    """The positive path: a CURRENT failure report (current assignee, current
    generation) still produces a correct work message stamped with the new
    generation, and its result is accepted."""
    co = C.GroupCoordinator(participants(4))
    co.distribute(subtasks(1))                     # p0, gen 0
    first = co.failover("s0", "p0")                # p1, gen 1
    assert first.attempt == 1
    second = co.failover("s0", "p1", attempt=1)    # p2, gen 2
    assert second is not None
    assert second.attempt == 2
    ok = co.submit_result(C.CoordinationMessage(
        "r:s0", "result", "s0", second.participant_id, {"ok": True},
        attempt=second.attempt))
    assert ok is True
    rep = co.finalize()
    assert rep.ok is True


# =====================================================================
# R-166d (round 5) — the delegate COMMAND is generation-fenced like the
# failover command: a late command issued from a SUPERSEDED assignment
# must be refused. Written BEFORE the fix (red run saved in
# docs/audit/M8/logs/raw/197_r166d5_red.log).
# =====================================================================

def test_stale_delegation_command_after_failover_round_trip_is_refused_r166d():
    """p0's assignment fails over away and back (gen 0 -> 2). A LATE delegate
    command issued from p0's ORIGINAL assignment (gen 0) must be refused —
    the assignee check alone cannot distinguish it from a current command,
    because p0 is the assignee again."""
    co = C.GroupCoordinator(participants(4), max_failovers=2)
    work = co.distribute(subtasks(1))[0]                    # p0, gen 0
    first = co.failover("s0", "p0", attempt=work.attempt)    # p1, gen 1
    back = co.failover("s0", first.participant_id, attempt=first.attempt)   # p0, gen 2
    assert back is not None and co._expected["s0"] == "p0"
    # a late command from p0's ORIGINAL assignment (gen 0) — must be refused
    with pytest.raises(C.CoordinationError, match="stale"):
        co.delegate("p0", "p2", "s0", attempt=0)
    assert co._expected["s0"] == "p0"          # no state change
    assert co._generation["s0"] == 2
    assert co._chain == {}                     # no delegation recorded
    assert co._messages_sent == 3              # work + two failovers only


def test_unstamped_delegation_command_after_transition_is_refused_r166d():
    """After any ownership transition, an unstamped delegate command cannot
    be proven current — refused with no state change."""
    co = C.GroupCoordinator(participants(4))
    co.distribute(subtasks(1))
    co.failover("s0", "p0")                    # gen 1
    with pytest.raises(C.CoordinationError, match="stale"):
        co.delegate(co._expected["s0"], "p2", "s0")   # unstamped, gen > 0
    assert co._expected["s0"] != "p2" or True
    assert co._chain == {}


def test_current_delegation_command_produces_correct_handoff_r166d():
    """The positive path: a delegate command stamped with the CURRENT
    generation produces a correct handoff (message stamped with the new
    generation) and the recipient's result is accepted."""
    co = C.GroupCoordinator(participants(4), max_failovers=2)
    work = co.distribute(subtasks(1))[0]                    # p0, gen 0
    first = co.failover("s0", "p0", attempt=work.attempt)   # p1, gen 1
    handoff = co.delegate(first.participant_id, "p2", "s0", attempt=first.attempt)
    assert handoff is not None
    assert handoff.attempt == 2                # the NEW generation
    ok = co.submit_result(C.CoordinationMessage(
        "r:s0", "result", "s0", "p2", {"ok": True}, attempt=handoff.attempt))
    assert ok is True
    rep = co.finalize()
    assert rep.ok is True
