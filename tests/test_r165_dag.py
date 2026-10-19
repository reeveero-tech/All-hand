"""R-165 (D-084): DAG plans, typed stage artifacts and stage approvals.

Acceptance (P3_AGENT_COORDINATION_AR.md): successful workflow, stage failure,
invalid artifact, cycle rejected, deadline/cancel, replay safe, full report —
plus the approved scope: parallel independent stages, local retry, per-stage
timeout, budget, approvals before sensitive stages, tenant-scoped resume, and
additive contracts. Engine tests drive ``dag.run_plan`` with in-process fake
agents (real asyncio scheduling, no mocks of the engine); HTTP tests go
through the real app (TestClient) with real agents.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from agent_civilization.core import contracts as C
from agent_civilization.pipeline import dag, orchestrator
from agent_civilization.routing import route


# ------------------------------------------------------------------ helpers
class FakeAgent:
    def __init__(self, spec, fn, name=None):
        self.specialization = spec
        self.name = name or f"{spec}_fake"
        self.status = "idle"
        self.stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}
        self.fn = fn
        self.calls = []

    async def execute(self, work):
        self.status = "working"
        try:
            self.calls.append(work)
            return await self.fn(work)
        finally:
            self.status = "idle"


def ok(answer="done", tool_output=None):
    return {"ok": True, "agent": "x", "elapsed_s": 0.0,
            "result": {"ok": True, "answer": answer, "tool_output": tool_output}}


def fail(error="boom"):
    return {"ok": False, "agent": "x", "elapsed_s": 0.0, "result": {"ok": False, "error": error}}


def answer(text="done", tool_output=None, delay=0.0):
    async def fn(work):
        if delay:
            await asyncio.sleep(delay)
        return ok(text, tool_output)
    return fn


def civ(**fns):
    """specialization -> fn; one agent per specialization."""
    return {spec: [FakeAgent(spec, fn)] for spec, fn in fns.items()}


def calls(c, spec):
    return c[spec][0].calls


def st(name, spec, deps=None, **kw):
    d = {"name": name, "specialization": spec, "prompt": f"do {name}"}
    if deps is not None:
        d["depends_on"] = deps
    d.update(kw)
    return d


def plan(*stages, **extra):
    return {"primary": "p", "reason": "r", "goal": "g", "stages": list(stages), **extra}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_runs(monkeypatch):
    dag.RUNS.clear()
    monkeypatch.setattr(dag, "STAGE_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(dag, "STAGE_RETRY_CAP_S", 0.0)
    yield
    dag.RUNS.clear()


# ============================================================ plan validation
def test_linear_plans_from_the_router_keep_their_order():
    p = route("write a python function that adds two numbers")
    n = dag.normalize_plan(p)
    names = [s["name"] for s in p["stages"]]
    assert n["order"] == names
    assert [s["depends_on"] for s in n["stages"]] == [[]] + [[x] for x in names[:-1]]
    assert p["stages"][0].get("depends_on") is None            # input never mutated
    test = next(s for s in n["stages"] if s["name"] == "test")
    assert test["requires_approval"] is True and test["sensitive"] == "code_execution"
    assert n["budget"] == {"max_stage_runs": dag.PLAN_MAX_STAGE_RUNS, "deadline_s": dag.PLAN_TIMEOUT_S}


@pytest.mark.parametrize("stages,msg", [
    ([st("a", "x", ["b"]), st("b", "x", ["a"])], "dependency cycle: a -> b -> a"),
    ([st("a", "x", []), st("b", "x", ["c"]), st("c", "x", ["d"]), st("d", "x", ["b"])],
     "dependency cycle: b -> c -> d -> b"),
    ([st("a", "x", ["a"])], "depends on itself"),
    ([st("a", "x", []), st("b", "x", ["zz"])], "unknown stage 'zz'"),
    ([st("a", "x"), st("a", "x")], "duplicate stage name 'a'"),
    ([st("a b", "x")], "invalid name"),
    ([st("a", "")], "missing specialization"),
    ([st("a", "x", max_attempts=0)], "max_attempts"),
    ([st("a", "x", max_attempts=True)], "max_attempts"),
    ([st("a", "x", timeout_s=-1)], "timeout_s"),
    ([st("a", "x", output_kind="blob")], "output_kind"),
    ([st("a", "x", requires_approval="yes")], "requires_approval"),
    ([st("a", "x", ["a", "a"])], "duplicate dependency"),
    ([], "no stages"),
    ([st(f"s{i}", "x") for i in range(dag.MAX_STAGES + 1)], "max 32"),
])
def test_malformed_plans_are_rejected_before_anything_runs(stages, msg):
    c = civ(x=answer())
    with pytest.raises(dag.PlanError, match=msg.replace("(", r"\(")):
        run(dag.run_plan(plan(*stages), c))
    assert calls(c, "x") == []


def test_budget_is_clamped_to_server_maximums():
    n = dag.normalize_plan(plan(st("a", "x"), budget={"max_stage_runs": 10**6, "deadline_s": 10**9}))
    assert n["budget"] == {"max_stage_runs": dag.PLAN_MAX_STAGE_RUNS, "deadline_s": dag.PLAN_TIMEOUT_S}
    with pytest.raises(dag.PlanError, match="budget"):
        dag.normalize_plan(plan(st("a", "x"), budget={"max_stage_runs": 0}))


# =========================================================== successful DAG
def test_successful_dag_passes_full_typed_artifacts_downstream():
    code = "def add(a, b):\n    return a + b\n" + "#" * 5000      # far beyond the old 800-char cut
    c = civ(code_generator=answer("code ready", {"code": code}), reviewer=answer("LGTM " + "r" * 3000),
            analyst=answer("stats", {"mean": 2.0, "n": 3, "samples": [1, 2, 3]}), validator=answer("valid"))
    p = plan(st("gen", "code_generator", []), st("review", "reviewer", ["gen"]),
             st("stats", "analyst", ["gen"]), st("check", "validator", ["review", "stats"]))
    rep = run(dag.run_plan(p, c))
    assert rep["status"] == "completed" and all(s["ok"] for s in rep["stages"])
    assert rep["order"] == ["gen", "review", "stats", "check"]
    rv = calls(c, "reviewer")[0]
    assert rv["artifact"] == {"code": code}                    # R-114 key, full code
    assert rv["inputs"]["gen"][0] == {"kind": "code", "stage": "gen", "code": code}
    assert rv["inputs"]["gen"][1] == {"kind": "text", "stage": "gen", "text": "code ready"}
    chk = calls(c, "validator")[0]
    assert set(chk["inputs"]) == {"review", "stats"}
    assert chk["inputs"]["review"][-1]["text"] == "LGTM " + "r" * 3000   # untruncated
    assert chk["artifact"]["analysis"]["mean"] == 2.0          # latest structured ancestor artifact
    for kinds in ([a for arts in chk["inputs"].values() for a in arts]):
        C.validate("stage_artifact", kinds)
    assert rep["final"] == "valid"
    # full report
    s0 = rep["stages"][0]
    for k in ("name", "specialization", "prompt", "depends_on", "agent", "ok", "output", "elapsed_s",
              "status", "attempts", "error", "artifacts", "requires_approval"):
        assert k in s0, k
    assert s0["artifacts"] == ["code", "text"] and s0["attempts"] == 1
    assert rep["budget"] == {"max_stage_runs": dag.PLAN_MAX_STAGE_RUNS, "stage_runs_used": 4,
                             "deadline_s": dag.PLAN_TIMEOUT_S}
    assert rep["pending_approvals"] == [] and rep["ignored_approvals"] == []
    C.validate("execute_response", {"task_id": "00000000-0000-4000-8000-000000000000", **rep})


def test_context_is_full_text_bounded_with_an_explicit_marker(monkeypatch):
    monkeypatch.setattr(dag, "STAGE_CONTEXT_CHARS", 1000)
    long = "x" * 3000
    c = civ(analyst=answer(long), validator=answer("v"))
    rep = run(dag.run_plan(plan(st("a", "analyst"), st("b", "validator")), c))
    w = calls(c, "validator")[0]
    assert rep["status"] == "completed"
    assert dag._CONTEXT_MARK in w["prompt"] and dag._TRUNC_MARK in w["prompt"]
    assert w["inputs"]["a"][-1]["text"] == long                # the artifact is never cut
    c2 = civ(analyst=answer("y" * 900), validator=answer("v"))
    run(dag.run_plan(plan(st("a", "analyst"), st("b", "validator")), c2))
    w2 = calls(c2, "validator")[0]
    assert "y" * 900 in w2["prompt"] and dag._TRUNC_MARK not in w2["prompt"]   # old cut was 800


def test_independent_stages_run_in_parallel_bounded_by_the_limit(monkeypatch):
    live = {"now": 0, "max": 0}

    async def slow(work):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        await asyncio.sleep(0.2)
        live["now"] -= 1
        return ok("w")

    c = {"w": [FakeAgent("w", slow, f"w{i}") for i in range(4)], "root": [FakeAgent("root", answer())]}
    p = plan(st("r", "root", []), *[st(f"b{i}", "w", ["r"]) for i in range(4)])
    t0 = time.monotonic()
    rep = run(dag.run_plan(p, c))
    assert rep["status"] == "completed" and live["max"] == 4
    assert time.monotonic() - t0 < 0.6                          # 4 x 0.2s sequential would be 0.8s
    monkeypatch.setattr(dag, "PLAN_PARALLEL", 2)
    live.update(now=0, max=0)
    rep = run(dag.run_plan(p, c))
    assert rep["status"] == "completed" and live["max"] == 2


# ============================================================ stage failure
def test_a_failed_stage_blocks_only_its_dependents():
    c = civ(a=answer("A"), b=lambda w: _coro(fail("b broke")), c=answer("C"), d=answer("D"))
    p = plan(st("a", "a", []), st("b", "b", ["a"]), st("c", "c", ["a"]), st("d", "d", ["b"]))
    rep = run(dag.run_plan(p, c))
    by = {s["name"]: s for s in rep["stages"]}
    assert rep["status"] == "failed"
    assert by["a"]["status"] == by["c"]["status"] == "succeeded"
    assert by["b"]["status"] == "failed" and by["b"]["output"] == "b broke" and not by["b"]["ok"]
    assert by["d"]["status"] == "blocked" and by["d"]["error"] == "dependency 'b' failed"
    assert calls(c, "d") == [] and rep["final"] == "C"


async def _coro(v):
    return v


def test_missing_agent_pool_fails_the_stage_without_crashing():
    rep = run(dag.run_plan(plan(st("a", "nobody")), {}))
    s = rep["stages"][0]
    assert rep["status"] == "failed" and s["agent"] is None and s["output"] == "لا يوجد وكيل متاح لتخصص nobody"


def test_an_agent_exception_is_a_stage_failure_reporting_only_its_type():
    async def boom(work):
        raise RuntimeError("secret internal detail")
    rep = run(dag.run_plan(plan(st("a", "x")), civ(x=boom)))
    s = rep["stages"][0]
    assert s["status"] == "failed" and s["output"] == "stage error: RuntimeError"


# ========================================================= invalid artifact
def test_an_invalid_artifact_fails_the_stage_and_blocks_dependents():
    bad = {"mean": 1.0, "n": "three", "samples": [1]}           # violates stage_artifact v1
    c = civ(analyst=answer("stats", bad), validator=answer("v"))
    rep = run(dag.run_plan(plan(st("a", "analyst"), st("b", "validator")), c))
    a, b = rep["stages"]
    assert a["status"] == "failed" and a["error"].startswith("invalid artifact (analysis)")
    assert b["status"] == "blocked" and calls(c, "validator") == []


def test_a_missing_declared_output_kind_is_an_invalid_artifact():
    c = civ(code_generator=answer("prose instead of code"), reviewer=answer("r"))
    p = plan(st("gen", "code_generator", output_kind="code"), st("rev", "reviewer"))
    rep = run(dag.run_plan(p, c))
    assert rep["stages"][0]["error"] == "invalid artifact: expected a 'code' artifact"
    assert rep["stages"][1]["status"] == "blocked"


@pytest.mark.parametrize("payload,ok_", [
    ({"kind": "code", "stage": "s", "code": "x=1"}, True),
    ({"kind": "code", "stage": "s", "code": "x", "source_file": "a.py"}, True),
    ({"kind": "analysis", "stage": "s", "analysis": {"mean": None, "n": 0, "samples": []}}, True),
    ({"kind": "code", "stage": "s", "code": "  "}, False),
    ({"kind": "code", "stage": "s", "code": "x", "sql": "select 1"}, False),
    ({"kind": "sql", "stage": "s", "sql": "select 1", "source_file": "a"}, False),
    ({"kind": "text", "stage": "s"}, False),
    ({"kind": "blob", "stage": "s", "text": "x"}, False),
    ({"kind": "analysis", "stage": "s", "analysis": {"mean": "x", "n": 1, "samples": []}}, False),
    ({"kind": "text", "stage": "", "text": "x"}, False),
    ({"kind": "text", "stage": "s", "text": "x", "extra": 1}, False),
])
def test_stage_artifact_contract(payload, ok_):
    if ok_:
        C.validate("stage_artifact", payload)
    else:
        with pytest.raises(Exception):
            C.validate("stage_artifact", payload)


# ===================================================== retry / timeout / budget
def test_local_retry_reruns_only_the_failing_stage():
    seq = iter([fail("transient"), ok("second time")])
    c = civ(a=answer("A"), b=lambda w: _coro(next(seq)))
    rep = run(dag.run_plan(plan(st("a", "a"), st("b", "b", max_attempts=3)), c))
    b = rep["stages"][1]
    assert rep["status"] == "completed" and b["attempts"] == 2 and b["output"] == "second time"
    assert len(calls(c, "a")) == 1 and rep["budget"]["stage_runs_used"] == 3


def test_retry_uses_jittered_backoff(monkeypatch):
    seen = []
    monkeypatch.setattr(dag.backpressure, "backoff_delay",
                        lambda attempt, base, cap: seen.append((attempt, base, cap)) or 0.0)
    rep = run(dag.run_plan(plan(st("a", "x", max_attempts=3)), civ(x=lambda w: _coro(fail()))))
    assert rep["stages"][0]["attempts"] == 3 and [a for a, _, _ in seen] == [1, 2]


def test_sensitive_stages_are_never_retried_automatically():
    n = dag.normalize_plan(plan(st("t", "test_runner", max_attempts=5), st("m", "modifier", max_attempts=4)))
    assert [s["max_attempts"] for s in n["stages"]] == [1, 1]
    c = civ(test_runner=lambda w: _coro(fail("exec failed")))
    rep = run(dag.run_plan(plan(st("t", "test_runner", max_attempts=5)), c))   # gate off (internal)
    assert rep["stages"][0]["attempts"] == 1 and len(calls(c, "test_runner")) == 1


def test_stage_timeout_cancels_the_agent_and_frees_it():
    cancelled = []

    async def hang(work):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    c = civ(x=hang, y=answer())
    rep = run(dag.run_plan(plan(st("a", "x", timeout_s=0.05), st("b", "y")), c))
    a, b = rep["stages"]
    assert a["status"] == "timed_out" and a["error"].startswith("stage timed out") and cancelled
    assert b["status"] == "blocked" and rep["status"] == "failed"
    assert c["x"][0].status == "idle"


def test_plan_deadline_times_out_running_and_skips_the_rest():
    c = civ(x=answer(delay=5), y=answer())
    t0 = time.monotonic()
    rep = run(dag.run_plan(plan(st("a", "x"), st("b", "y", []), st("c", "y", ["a"]),
                                budget={"deadline_s": 0.2}), c))
    by = {s["name"]: s for s in rep["stages"]}
    assert time.monotonic() - t0 < 1.5
    assert rep["status"] == "timed_out"
    assert by["a"]["status"] == "timed_out" and by["b"]["status"] == "succeeded"
    assert by["c"]["status"] in ("blocked", "skipped")
    assert c["x"][0].status == "idle"


def test_budget_of_stage_runs_is_enforced_across_retries():
    c = civ(x=lambda w: _coro(fail()), y=answer())
    rep = run(dag.run_plan(plan(st("a", "x", max_attempts=5), st("b", "y", []),
                                budget={"max_stage_runs": 3}), c))
    assert rep["status"] == "budget_exhausted" and rep["budget"]["stage_runs_used"] == 3
    assert len(calls(c, "x")) + len(calls(c, "y")) == 3


def test_external_cancellation_stops_running_stages_and_marks_the_run():
    cancelled = []

    async def hang(work):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def main():
        c = civ(x=hang, y=answer())
        t = asyncio.ensure_future(dag.run_plan(plan(st("a", "x"), st("b", "y")), c, tenant_id="t1"))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return c

    c = run(main())
    (r,) = list(dag.RUNS._runs.values())
    assert cancelled and r.status == "cancelled" and not r.active
    rep = dag.report(r)
    assert [s["status"] for s in rep["stages"]] == ["cancelled", "cancelled"]
    assert c["x"][0].status == "idle" and calls(c, "y") == []


# ============================================================ approvals
def _gated():
    return plan(st("execute", "code_generator"), st("test", "test_runner"), st("report", "communicator"))


def _gated_civ():
    return civ(code_generator=answer("code", {"code": "x = 1"}), test_runner=answer("tests passed"),
               communicator=answer("summary"))


def test_a_sensitive_stage_waits_for_approval_and_nothing_downstream_runs():
    c = _gated_civ()
    rep = run(dag.run_plan(_gated(), c, approvals=set(), tenant_id="t1"))
    by = {s["name"]: s for s in rep["stages"]}
    assert rep["status"] == "awaiting_approval"
    assert by["execute"]["status"] == "succeeded"
    assert by["test"]["status"] == "awaiting_approval" and by["report"]["status"] == "blocked"
    assert rep["pending_approvals"] == [{"stage": "test", "specialization": "test_runner",
                                         "why": "code_execution"}]
    assert calls(c, "test_runner") == [] and calls(c, "communicator") == []


def test_requires_approval_false_cannot_disable_the_gate_on_a_sensitive_stage():
    n = dag.normalize_plan(plan(st("t", "test_runner", requires_approval=False),
                                st("x", "analyst", requires_approval=True)))
    assert [s["requires_approval"] for s in n["stages"]] == [True, True]
    assert n["stages"][1]["sensitive"] == "declared"


def test_approved_stages_run_and_unknown_approvals_are_reported():
    c = _gated_civ()
    rep = run(dag.run_plan(_gated(), c, approvals={"test", "nope"}, tenant_id="t1"))
    assert rep["status"] == "completed" and rep["ignored_approvals"] == ["nope"]
    assert len(calls(c, "test_runner")) == 1


def test_internal_callers_keep_the_gate_off():
    c = _gated_civ()
    rep = run(orchestrator.execute_plan(_gated(), c))
    assert rep["status"] == "completed" and len(calls(c, "test_runner")) == 1
    assert len(dag.RUNS) == 0                                  # internal runs are not stored


# ============================================================ resume / replay
def test_resume_after_approval_never_reexecutes_succeeded_stages():
    c = _gated_civ()
    first = run(dag.run_plan(_gated(), c, approvals=set(), tenant_id="t1"))
    rid = first["run_id"]
    second = run(dag.resume_run(rid, "t1", c, approvals=["test"]))
    assert second["status"] == "completed" and second["run_id"] == rid
    assert len(calls(c, "code_generator")) == 1                # replay safe
    assert len(calls(c, "test_runner")) == 1 and len(calls(c, "communicator")) == 1
    assert calls(c, "test_runner")[0]["artifact"] == {"code": "x = 1"}   # kept artifact flows on
    third = run(dag.resume_run(rid, "t1", c))                  # completed: returned as-is
    assert third == second
    assert [len(calls(c, s)) for s in ("code_generator", "test_runner", "communicator")] == [1, 1, 1]


def test_resume_after_failure_reruns_only_unsucceeded_stages():
    seq = iter([fail("flaky"), ok("fixed")])
    c = civ(a=answer("A"), b=lambda w: _coro(next(seq)), d=answer("D"))
    p = plan(st("a", "a"), st("b", "b"), st("d", "d"))
    first = run(dag.run_plan(p, c, approvals=set(), tenant_id="t1"))
    assert first["status"] == "failed"
    again = run(dag.resume_run(first["run_id"], "t1", c))
    assert again["status"] == "completed" and len(calls(c, "a")) == 1 and len(calls(c, "d")) == 1
    assert again["budget"]["stage_runs_used"] == 4             # cumulative: a, b, b, d


def test_resume_is_tenant_scoped_and_refuses_an_active_run():
    c = _gated_civ()
    rep = run(dag.run_plan(_gated(), c, approvals=set(), tenant_id="t1"))
    for tenant in ("t2", None):
        with pytest.raises(dag.RunNotFound):
            run(dag.resume_run(rep["run_id"], tenant, c))
        with pytest.raises(dag.RunNotFound):
            dag.get_report(rep["run_id"], tenant)
    with pytest.raises(dag.RunNotFound):
        dag.get_report("0" * 32, "t1")
    dag.RUNS.get(rep["run_id"], "t1").active = True
    with pytest.raises(dag.RunBusy):
        run(dag.resume_run(rep["run_id"], "t1", c))


def test_run_store_is_bounded_and_never_drops_an_active_run():
    store = dag.RunStore(keep=3)
    runs = [dag.Run(dag.normalize_plan(plan(st("a", "x"))), None, "t", set()) for _ in range(5)]
    runs[0].active = True
    for r in runs:
        store.add(r)
    kept = list(store._runs)
    assert len(store) == 3 and runs[0].run_id in kept and runs[-1].run_id in kept


# ================================================================== HTTP
@pytest.fixture
def client(tenant_auth):
    import server
    with TestClient(server.app, raise_server_exceptions=False, headers=tenant_auth.headers) as c:
        yield c


FETCH = "fetch https://example.com status"


def test_http_execute_reports_the_run_and_get_returns_it(client, tenant_auth):
    r = client.post("/civilization/execute", json={"message": "analyze numbers 4 8 15"})
    assert r.status_code == 200, r.text
    body = r.json()
    C.validate("execute_response", body)
    assert body["status"] == "completed" and len(body["run_id"]) == 32
    g = client.get(f"/civilization/runs/{body['run_id']}")
    assert g.status_code == 200 and g.json() == body
    rec = client.get(f"/api/tasks/{body['task_id']}").json()
    assert rec["run_id"] == body["run_id"] and rec["status"] == "completed"
    C.validate("execute_record", rec)
    # another tenant (the admin identity is tenant test_admin) sees nothing
    other = client.get(f"/civilization/runs/{body['run_id']}", headers=tenant_auth.admin_headers)
    assert other.status_code == 404
    assert client.get("/civilization/runs/" + "0" * 32).status_code == 404
    assert client.get(f"/civilization/runs/{body['run_id']}", headers={"X-API-Key": "wrong"}).status_code == 401


def test_http_sensitive_stage_needs_approval_then_resume_runs_only_the_rest(client, tenant_auth):
    r = client.post("/civilization/execute", json={"message": FETCH})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "awaiting_approval", body
    by = {s["name"]: s for s in body["stages"]}
    assert by["execute"]["status"] == "awaiting_approval" and by["probe"]["status"] == "blocked"
    assert {p["stage"] for p in body["pending_approvals"]} == {"execute"}
    done_before = {n: by[n]["elapsed_s"] for n in ("understand", "plan")}
    # another tenant cannot resume it
    x = client.post(f"/civilization/runs/{body['run_id']}/resume", json={"approve_stages": ["execute", "probe"]},
                    headers=tenant_auth.admin_headers)
    assert x.status_code == 404
    res = client.post(f"/civilization/runs/{body['run_id']}/resume", json={"approve_stages": ["execute", "probe"]})
    assert res.status_code == 200, res.text
    out = res.json()
    C.validate("execute_response", out)
    ob = {s["name"]: s for s in out["stages"]}
    assert out["run_id"] == body["run_id"] and out["task_id"] == body["task_id"]
    assert out["status"] == "completed", out
    assert ob["execute"]["status"] == ob["probe"]["status"] == "succeeded"
    assert ob["execute"]["attempts"] == ob["probe"]["attempts"] == 1
    assert ob["execute"]["output"].startswith("GET https://example.com -> HTTP 200")
    assert {n: ob[n]["elapsed_s"] for n in done_before} == done_before   # not re-executed
    assert out["pending_approvals"] == []


def test_http_approval_in_the_execute_request(client):
    r = client.post("/civilization/execute", json={"message": FETCH, "approve_stages": ["execute", "probe", "zzz"]})
    body = r.json()
    assert r.status_code == 200 and body["pending_approvals"] == [] and body["ignored_approvals"] == ["zzz"]
    assert {s["name"]: s["status"] for s in body["stages"]}["execute"] != "awaiting_approval"


def test_http_resume_of_an_active_run_is_409(client):
    body = client.post("/civilization/execute", json={"message": FETCH}).json()
    run_ = next(iter(dag.RUNS._runs.values()))
    run_.active = True
    try:
        r = client.post(f"/civilization/runs/{body['run_id']}/resume", json={})
        assert r.status_code == 409
    finally:
        run_.active = False


@pytest.mark.parametrize("payload", [
    {"message": "x", "approve_stages": "execute"},
    {"message": "x", "approve_stages": [1]},
    {"message": "x", "approve_stages": ["s"] * 33},
])
def test_http_approve_stages_is_validated(client, payload):
    assert client.post("/civilization/execute", json=payload).status_code == 422


def test_http_resume_request_is_strict(client):
    body = client.post("/civilization/execute", json={"message": FETCH}).json()
    r = client.post(f"/civilization/runs/{body['run_id']}/resume", json={"allow_public_llm": "yes"})
    assert r.status_code == 422


def test_http_malformed_plan_is_400_and_the_record_is_failed(client, monkeypatch):
    import agent_civilization.civilization_routes as routes
    monkeypatch.setattr(routes, "auto_route", lambda m: plan(st("a", "analyst", ["b"]), st("b", "analyst", ["a"])))
    r = client.post("/civilization/execute", json={"message": "cyclic"})
    assert r.status_code == 400 and "dependency cycle" in r.json()["detail"]
    recs = [t for t in client.get("/api/tasks").json()["tasks"] if t.get("message") == "cyclic"]
    assert recs and recs[0]["status"] == "failed"


def test_http_plan_preview_returns_the_normalized_dag(client):
    r = client.post("/civilization/plan", json={"message": FETCH})
    p = r.json()["plan"]
    assert r.status_code == 200 and p["order"] == [s["name"] for s in p["stages"]]
    by = {s["name"]: s for s in p["stages"]}
    assert by["execute"]["requires_approval"] and by["probe"]["requires_approval"]
    assert not by["understand"]["requires_approval"] and by["plan"]["depends_on"] == ["understand"]


def test_contract_change_is_additive_only():
    """The pre-R-165 schemas (main 29b133b) are frozen in the audit log; every
    change against them is additive (no breaking change, no version bump)."""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    base = root / "docs/audit/M8/logs/raw/157_r165_contracts_base"
    names = sorted(p.name for p in base.glob("*.v1.schema.json"))
    assert names == ["execute_record.v1.schema.json", "execute_request.v1.schema.json",
                     "execute_response.v1.schema.json", "task_list.v1.schema.json"]
    for n in names:
        old = json.loads((base / n).read_text())
        new = json.loads((root / "docs/governance/contracts" / n).read_text())
        ch = C.classify_changes(old, new)
        assert ch["breaking"] == [] and ch["additive"], (n, ch)
    for n in ("stage_artifact", "resume_request"):
        assert (root / f"docs/governance/contracts/{n}.v1.schema.json").is_file()


@pytest.mark.parametrize("var,val", [("AGOS_PLAN_PARALLEL", "0"), ("AGOS_STAGE_MAX_ATTEMPTS", "9"),
                                     ("AGOS_PLAN_TIMEOUT_S", "abc")])
def test_invalid_r165_settings_stop_server_startup(var, val):
    import os
    import subprocess
    import sys
    env = {**os.environ, "AGOS_ENV": "test", var: val}
    r = subprocess.run([sys.executable, "-c", "import server"], env=env, capture_output=True, text=True,
                       cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]), timeout=120)
    assert r.returncode != 0 and var in r.stderr, r.stderr[-400:]
