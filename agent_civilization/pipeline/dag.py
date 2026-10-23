"""R-165 (D-084) — DAG plans, typed stage artifacts and stage approvals.

The engine behind ``orchestrator.execute_plan``. A plan is a list of stages;
each stage may declare ``depends_on`` (default: the previous stage, so every
linear plan produced by ``auto_router.route()`` keeps its exact order). The
engine:

* validates the plan before running anything (unique names, known
  dependencies, no self-dependency, no cycle — ``PlanError``);
* runs every ready stage concurrently (bounded by ``AGOS_PLAN_PARALLEL``);
* hands each stage the **typed artifacts** of its dependencies
  (``work["inputs"]``, schema-validated with the ``stage_artifact`` v1
  contract) and the latest structured artifact of its ancestors
  (``work["artifact"]``, the R-114 key the handlers already read) — never a
  truncated text summary in place of an artifact;
* treats an artifact that fails validation (or a missing declared
  ``output_kind``) as a stage failure;
* retries a failed stage locally (``max_attempts``, jittered backoff); a
  sensitive stage is never retried automatically (its side effects must not
  repeat without a new decision);
* enforces a per-stage timeout, an overall plan deadline and a budget of
  stage runs; cancellation stops running stages;
* blocks only the stages that depend on a failed / unapproved stage;
* holds a sensitive stage (code execution, external requests to user-given
  targets, self-modification) at ``awaiting_approval`` until the caller
  approves it by name;
* keeps an in-memory, tenant-scoped run record so a run can be inspected and
  resumed: succeeded stages (and their artifacts) are never re-executed.

Limits (documented in RISKS): run records live in this process only — lost
on restart and not shared between instances.
"""

from __future__ import annotations

import asyncio
import collections
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import ValidationError

from agent_civilization.core import backpressure, telemetry
from agent_civilization.core.contracts import StageArtifact

# --------------------------------------------------------------- settings
# Read by literal name (R-156 entry-point map). Invalid values fail import.
PLAN_TIMEOUT_S = backpressure._float_env("AGOS_PLAN_TIMEOUT_S", 600.0, 1.0, 86400.0)
STAGE_TIMEOUT_S = backpressure._float_env("AGOS_STAGE_TIMEOUT_S", 300.0, 1.0, 86400.0)
STAGE_MAX_ATTEMPTS = backpressure._int_env("AGOS_STAGE_MAX_ATTEMPTS", 1, 1, 5)
PLAN_MAX_STAGE_RUNS = backpressure._int_env("AGOS_PLAN_MAX_STAGE_RUNS", 64, 1, 1000)
PLAN_PARALLEL = backpressure._int_env("AGOS_PLAN_PARALLEL", 4, 1, 32)
STAGE_RETRY_BASE_S = backpressure._float_env("AGOS_STAGE_RETRY_BASE_S", 0.5, 0.0, 60.0)
STAGE_RETRY_CAP_S = backpressure._float_env("AGOS_STAGE_RETRY_CAP_S", 10.0, 0.0, 600.0)
STAGE_CONTEXT_CHARS = backpressure._int_env("AGOS_STAGE_CONTEXT_CHARS", 8000, 500, 200000)
PLAN_RUNS_KEPT = backpressure._int_env("AGOS_PLAN_RUNS_KEPT", 200, 1, 10000)

MAX_STAGES = 32
MAX_ATTEMPTS_CAP = 5
ARTIFACT_KINDS = ("code", "sql", "analysis", "text")

#: specialization -> why its stage needs an explicit approval (D-084)
SENSITIVE: Dict[str, str] = {
    "test_runner": "code_execution",
    "modifier": "code_execution",
    "network_client": "external_request",
    "api_integrator": "external_request",
    "self_developer": "self_modification",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CONTEXT_MARK = "\n\n=== Previous stages summary ===\n"
_TRUNC_MARK = "\n[... truncated for the prompt; the full output is in this stage's typed inputs]"

#: stage states
PENDING, RUNNING, SUCCEEDED, FAILED = "pending", "running", "succeeded", "failed"
BLOCKED, AWAITING, TIMED_OUT, SKIPPED, CANCELLED = (
    "blocked", "awaiting_approval", "timed_out", "skipped", "cancelled")
_DONE_BAD = {FAILED, BLOCKED, AWAITING, TIMED_OUT, SKIPPED, CANCELLED}


class PlanError(ValueError):
    """The plan is malformed (bad stage, unknown dependency, cycle...)."""


class RunNotFound(LookupError):
    """No such run for this tenant (HTTP 404 — never reveals other tenants)."""


class RunBusy(RuntimeError):
    """The run is executing right now (HTTP 409)."""


# ------------------------------------------------------------ validation
def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _topo_order(stages: List[Dict[str, Any]]) -> List[str]:
    """Kahn's algorithm, stable on the plan order. Raises PlanError on a cycle
    (the message names one concrete cycle)."""
    names = [s["name"] for s in stages]
    deps = {s["name"]: list(s["depends_on"]) for s in stages}
    indeg = {n: len(deps[n]) for n in names}
    children: Dict[str, List[str]] = {n: [] for n in names}
    for n in names:
        for d in deps[n]:
            children[d].append(n)
    order: List[str] = []
    ready = [n for n in names if indeg[n] == 0]
    pos = {n: i for i, n in enumerate(names)}
    while ready:
        ready.sort(key=pos.__getitem__)
        n = ready.pop(0)
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    if len(order) == len(names):
        return order
    left = [n for n in names if n not in set(order)]
    # walk dependencies inside the leftover set until a node repeats
    path: List[str] = []
    seen: Dict[str, int] = {}
    cur = left[0]
    while cur not in seen:
        seen[cur] = len(path)
        path.append(cur)
        cur = next(d for d in deps[cur] if d in set(left))
    cycle = path[seen[cur]:] + [cur]
    raise PlanError("dependency cycle: " + " -> ".join(cycle))


def normalize_plan(plan: Any) -> Dict[str, Any]:
    """Validate ``plan`` and return a normalized copy (never mutates input):
    every stage gets ``depends_on``, ``max_attempts``, ``timeout_s``,
    ``output_kind``, ``requires_approval`` and ``sensitive``; the plan gets
    ``order`` (topological) and ``budget``."""
    if not isinstance(plan, dict):
        raise PlanError("plan must be an object")
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        raise PlanError("plan has no stages")
    if len(stages) > MAX_STAGES:
        raise PlanError(f"plan has {len(stages)} stages (max {MAX_STAGES})")
    out: List[Dict[str, Any]] = []
    names: Set[str] = set()
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            raise PlanError(f"stage #{i} must be an object")
        name, spec = st.get("name"), st.get("specialization")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise PlanError(f"stage #{i}: invalid name {name!r}")
        if name in names:
            raise PlanError(f"duplicate stage name {name!r}")
        if not isinstance(spec, str) or not spec:
            raise PlanError(f"stage {name!r}: missing specialization")
        if not isinstance(st.get("prompt", ""), str):
            raise PlanError(f"stage {name!r}: prompt must be a string")
        deps = st.get("depends_on")
        if deps is None:
            deps = [out[-1]["name"]] if out else []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise PlanError(f"stage {name!r}: depends_on must be a list of stage names")
        if len(set(deps)) != len(deps):
            raise PlanError(f"stage {name!r}: duplicate dependency")
        if name in deps:
            raise PlanError(f"stage {name!r} depends on itself")
        attempts = st.get("max_attempts", STAGE_MAX_ATTEMPTS)
        if not _is_int(attempts) or not 1 <= attempts <= MAX_ATTEMPTS_CAP:
            raise PlanError(f"stage {name!r}: max_attempts must be 1..{MAX_ATTEMPTS_CAP}")
        timeout = st.get("timeout_s", STAGE_TIMEOUT_S)
        if not _is_num(timeout) or timeout <= 0:
            raise PlanError(f"stage {name!r}: timeout_s must be a positive number")
        kind = st.get("output_kind")
        if kind is not None and kind not in ARTIFACT_KINDS:
            raise PlanError(f"stage {name!r}: output_kind must be one of {ARTIFACT_KINDS}")
        # R-170 (batch 4): a stage may declare output-verification
        # requirements (core/output_validators.KNOWN_REQUIREMENTS). Checked
        # here so a malformed value fails the plan early and explicitly; the
        # requirement keys themselves are judged by the completion gate
        # (unknown keys are rejected there, never silently ignored).
        verification = st.get("verification")
        if verification is not None and not isinstance(verification, dict):
            raise PlanError(f"stage {name!r}: verification must be an object")
        explicit = st.get("requires_approval", False)
        if not isinstance(explicit, bool):
            raise PlanError(f"stage {name!r}: requires_approval must be a boolean")
        sensitive = SENSITIVE.get(spec)
        needs = bool(explicit) or sensitive is not None   # sensitive cannot opt out
        names.add(name)
        out.append({
            **st,
            "depends_on": list(deps),
            # a sensitive stage's side effects never repeat automatically
            "max_attempts": 1 if sensitive else attempts,
            "timeout_s": float(min(timeout, STAGE_TIMEOUT_S)),
            "output_kind": kind,
            "requires_approval": needs,
            "sensitive": sensitive or ("declared" if explicit else None),
            **({"verification": dict(verification)} if verification is not None else {}),
        })
    for st in out:
        for d in st["depends_on"]:
            if d not in names:
                raise PlanError(f"stage {st['name']!r} depends on unknown stage {d!r}")
    order = _topo_order(out)
    budget = plan.get("budget") or {}
    if not isinstance(budget, dict):
        raise PlanError("budget must be an object")
    runs = budget.get("max_stage_runs", PLAN_MAX_STAGE_RUNS)
    dl = budget.get("deadline_s", PLAN_TIMEOUT_S)
    if not _is_int(runs) or runs < 1:
        raise PlanError("budget.max_stage_runs must be a positive integer")
    if not _is_num(dl) or dl <= 0:
        raise PlanError("budget.deadline_s must be a positive number")
    return {**plan, "stages": out, "order": order,
            "budget": {"max_stage_runs": min(runs, PLAN_MAX_STAGE_RUNS),
                       "deadline_s": float(min(dl, PLAN_TIMEOUT_S))}}


# -------------------------------------------------------------- artifacts
def build_artifacts(stage: str, res: Any, output_text: str) -> List[Dict[str, Any]]:
    """Typed artifacts of one stage result, each validated against the
    ``stage_artifact`` v1 contract (raises ``ValueError`` when invalid)."""
    from agent_civilization.pipeline.orchestrator import _artifact_of

    arts: List[Dict[str, Any]] = []
    structured = _artifact_of(res) if isinstance(res, dict) else None
    if structured:
        kind = next(k for k in ("code", "sql", "analysis") if k in structured)
        arts.append({"kind": kind, "stage": stage, **structured})
    if output_text:
        arts.append({"kind": "text", "stage": stage, "text": output_text})
    for a in arts:
        try:
            StageArtifact.model_validate(a)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"invalid artifact ({a.get('kind')}): {first.get('msg')}") from None
    return arts


def _legacy_view(art: Dict[str, Any]) -> Dict[str, Any]:
    """The R-114 ``work["artifact"]`` shape ({"code"| "sql"| "analysis", ...})."""
    return {k: v for k, v in art.items() if k not in ("kind", "stage", "text") and v is not None}


# ------------------------------------------------------------------- runs
class Run:
    def __init__(self, plan: Dict[str, Any], data: Any, tenant_id: Optional[str],
                 approvals: Optional[Set[str]], task_id: Optional[str] = None) -> None:
        self.run_id = uuid.uuid4().hex
        self.tenant_id = tenant_id
        self.task_id = task_id
        self.plan = plan
        self.data = data
        self.gate = approvals is not None          # None = trusted internal caller
        self.approvals: Set[str] = set(approvals or ())
        self.ignored_approvals: List[str] = []
        self.runs_used = 0
        self.deadline_hit = False
        self.budget_hit = False
        self.status = "running"
        self.active = False
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.elapsed_s = 0.0
        self.by_name = {s["name"]: s for s in plan["stages"]}
        self.state: Dict[str, Dict[str, Any]] = {n: self._fresh() for n in self.by_name}
        self.add_approvals(approvals or ())

    @staticmethod
    def _fresh() -> Dict[str, Any]:
        return {"status": PENDING, "attempts": 0, "agent": None, "output": "",
                "artifacts": [], "error": None, "elapsed_s": 0.0}

    def add_approvals(self, names: Iterable[str]) -> None:
        for n in names:
            if n in self.by_name:
                self.approvals.add(n)
            elif n not in self.ignored_approvals:
                self.ignored_approvals.append(n)


class RunStore:
    """In-memory, tenant-scoped run records (bounded; oldest inactive pruned)."""

    def __init__(self, keep: int = PLAN_RUNS_KEPT) -> None:
        self.keep = keep
        self._runs: "collections.OrderedDict[str, Run]" = collections.OrderedDict()

    def add(self, run: Run) -> None:
        self._runs[run.run_id] = run
        while len(self._runs) > self.keep:
            victim = next((k for k, r in self._runs.items() if not r.active), None)
            if victim is None:
                break
            del self._runs[victim]

    def get(self, run_id: str, tenant_id: Optional[str]) -> Run:
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None or tenant_id is None or run.tenant_id != tenant_id:
            raise RunNotFound(run_id)
        return run

    def clear(self) -> None:
        self._runs.clear()

    def __len__(self) -> int:
        return len(self._runs)


RUNS = RunStore()


# --------------------------------------------------------------- engine
def _ancestors(run: Run, name: str) -> Set[str]:
    seen: Set[str] = set()
    stack = list(run.by_name[name]["depends_on"])
    while stack:
        d = stack.pop()
        if d not in seen:
            seen.add(d)
            stack.extend(run.by_name[d]["depends_on"])
    return seen


def _context_block(texts: List[str]) -> str:
    """Full ancestor texts for the LLM prompt, bounded by
    AGOS_STAGE_CONTEXT_CHARS with an explicit marker (the stage still gets the
    complete artifacts in ``work["inputs"]``)."""
    total = sum(len(t) for t in texts)
    if total <= STAGE_CONTEXT_CHARS:
        return "\n---\n".join(texts)
    share = max(200, STAGE_CONTEXT_CHARS // max(len(texts), 1))
    return "\n---\n".join(t if len(t) <= share else t[:share] + _TRUNC_MARK for t in texts)


def _work_for(run: Run, name: str, goal: str, plan_view: List[Dict[str, Any]]) -> Dict[str, Any]:
    st = run.by_name[name]
    order = run.plan["order"]
    anc = _ancestors(run, name)
    anc_ordered = [n for n in order if n in anc and run.state[n]["status"] == SUCCEEDED]
    inputs = {d: list(run.state[d]["artifacts"]) for d in st["depends_on"]}
    inherited = None
    for n in anc_ordered:                         # latest structured artifact wins
        for a in run.state[n]["artifacts"]:
            if a["kind"] != "text":
                inherited = _legacy_view(a)
    context = []
    for n in anc_ordered:
        s = run.state[n]
        if s["output"]:
            context.append(f"[{n}@{s['agent']}] {s['output']}")
    context = context[-3:]
    prompt = st.get("prompt") or ""
    if context:
        prompt = prompt + _CONTEXT_MARK + _context_block(context)
    work = {
        "task_id": f"stage-{uuid.uuid4().hex[:8]}",
        "prompt": prompt,
        "goal": goal,
        "stage": name,
        "plan_stages": plan_view if name == "plan" else None,
        "context": context,
        "artifact": inherited,
        "inputs": inputs,
        "data": run.data,
    }
    # R-170 (batch 4): the stage's declared verification requirements reach
    # the completion gate in agent.execute (a copy: the handler cannot alter
    # the plan). Absent -> key absent (existing work shape unchanged).
    if st.get("verification") is not None:
        work["verification"] = dict(st["verification"])
    return work


def _output_of(res: Any) -> Tuple[bool, str]:
    stage_ok, text = True, ""
    if isinstance(res, dict):
        stage_ok = bool(res.get("ok"))
        r = res.get("result") if "result" in res else res
        if isinstance(r, dict):
            # R-116: a failed stage reports its error, not its partial tool_output
            text = ((r.get("error") or r.get("answer") or r.get("tool_output")) if not stage_ok
                    else (r.get("answer") or r.get("tool_output") or r.get("error"))) or ""
            if not isinstance(text, str):
                text = str(text)
    else:
        stage_ok = False
    return stage_ok, (text or "").strip()


async def _run_stage(run: Run, name: str, civilization: Dict[str, list], goal: str,
                     plan_view: List[Dict[str, Any]], deadline: float, sem: asyncio.Semaphore) -> None:
    from agent_civilization.pipeline.orchestrator import _pick

    st, s = run.by_name[name], run.state[name]
    spec = st["specialization"]
    async with sem:
        s["status"] = RUNNING
        t_stage = time.monotonic()
        try:
            for attempt in range(1, st["max_attempts"] + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    run.deadline_hit = True
                    s.update(status=TIMED_OUT, error="plan deadline reached")
                    return
                # budget check-and-increment is synchronous: concurrent stages
                # can never overspend it
                if run.runs_used >= run.plan["budget"]["max_stage_runs"]:
                    run.budget_hit = True
                    s.update(status=SKIPPED if attempt == 1 else FAILED,
                             error="stage-run budget exhausted")
                    return
                pool = civilization.get(spec)
                if not pool:
                    s.update(status=FAILED, agent=None, output=f"لا يوجد وكيل متاح لتخصص {spec}",
                             error="no agent for specialization")
                    return
                run.runs_used += 1
                s["attempts"] = attempt
                agent = await _pick(pool)
                s["agent"] = agent.name
                work = _work_for(run, name, goal, plan_view)
                limit = min(st["timeout_s"], remaining)
                err: Optional[str] = None
                try:
                    res = await asyncio.wait_for(agent.execute(work), timeout=limit)
                except asyncio.TimeoutError:
                    err, timed = f"stage timed out after {limit:.1f}s", True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    err, timed = f"stage error: {type(exc).__name__}", False
                else:
                    timed = False
                    ok, text = _output_of(res)
                    if ok:
                        try:
                            arts = build_artifacts(name, res, text)
                            want = st["output_kind"]
                            if want and not any(a["kind"] == want for a in arts):
                                raise ValueError(f"invalid artifact: expected a {want!r} artifact")
                        except ValueError as exc:
                            err = str(exc) if str(exc).startswith("invalid artifact") \
                                else f"invalid artifact: {exc}"
                            s["output"] = text
                        else:
                            s.update(status=SUCCEEDED, output=text, artifacts=arts, error=None)
                            return
                    else:
                        err = text or "stage failed"
                        s["output"] = text
                finally:
                    if getattr(agent, "status", None) == "working":
                        agent.status = "idle"
                s["error"] = err
                if attempt < st["max_attempts"]:
                    pause = backpressure.backoff_delay(attempt, STAGE_RETRY_BASE_S, STAGE_RETRY_CAP_S)
                    await asyncio.sleep(max(0.0, min(pause, deadline - time.monotonic())))
                else:
                    s["status"] = TIMED_OUT if timed else FAILED
                    if not s["output"]:
                        s["output"] = err or ""
                    return
        finally:
            s["elapsed_s"] = round(s["elapsed_s"] + time.monotonic() - t_stage, 3)


def _settle(run: Run) -> None:
    """Mark pending stages whose dependency can no longer succeed."""
    changed = True
    while changed:
        changed = False
        for n in run.plan["order"]:
            s = run.state[n]
            if s["status"] != PENDING:
                continue
            bad = [d for d in run.by_name[n]["depends_on"] if run.state[d]["status"] in _DONE_BAD]
            if bad:
                d = bad[0]
                why = run.state[d]["status"]
                s.update(status=BLOCKED, error=f"dependency {d!r} {why}")
                changed = True


def _aggregate(run: Run) -> str:
    sts = [run.state[n]["status"] for n in run.by_name]
    if CANCELLED in sts:
        return "cancelled"
    if run.deadline_hit:
        return "timed_out"
    if run.budget_hit:
        return "budget_exhausted"
    if FAILED in sts or TIMED_OUT in sts:
        return "failed"
    if AWAITING in sts:
        return "awaiting_approval"
    return "completed" if all(x == SUCCEEDED for x in sts) else "failed"


async def _drive(run: Run, civilization: Dict[str, list]) -> None:
    plan = run.plan
    goal = plan.get("goal") or next(
        (st.get("prompt") for st in plan["stages"] if st.get("name") == "execute"), "")
    plan_view = [{"name": st.get("name"), "specialization": st.get("specialization"),
                  "reason": st.get("reason")} for st in plan["stages"]]
    idx = {st["name"]: i for i, st in enumerate(plan["stages"])}
    n_stages = len(plan["stages"])
    deadline = time.monotonic() + plan["budget"]["deadline_s"]
    sem = asyncio.Semaphore(PLAN_PARALLEL)
    tasks: Dict[str, asyncio.Task] = {}

    async def traced(name: str) -> None:
        st = run.by_name[name]
        # R-159: one `workflow` span per stage (never prompt/context/output)
        with telemetry.span("workflow", "stage", stage=name, stage_index=idx[name],
                            stage_count=n_stages, specialization=st["specialization"]) as sp:
            try:
                await _run_stage(run, name, civilization, goal, plan_view, deadline, sem)
            finally:
                s = run.state[name]
                sp.set(outcome="ok" if s["status"] == SUCCEEDED else
                       ("skipped" if s["agent"] is None else "fail"))

    def span_unrun(name: str) -> None:
        st = run.by_name[name]
        with telemetry.span("workflow", "stage", stage=name, stage_index=idx[name],
                            stage_count=n_stages, specialization=st["specialization"]) as sp:
            sp.set(outcome="skipped")

    try:
        while True:
            _settle(run)
            for n in plan["order"]:
                s = run.state[n]
                if s["status"] != PENDING or n in tasks:
                    continue
                if not all(run.state[d]["status"] == SUCCEEDED for d in run.by_name[n]["depends_on"]):
                    continue
                if run.gate and run.by_name[n]["requires_approval"] and n not in run.approvals:
                    s.update(status=AWAITING, error="approval required: " + str(run.by_name[n]["sensitive"]))
                    continue
                if time.monotonic() >= deadline:
                    break
                tasks[n] = asyncio.ensure_future(traced(n))
            _settle(run)
            live = {n: t for n, t in tasks.items() if not t.done()}
            if not live:
                break
            remaining = deadline - time.monotonic()
            done, _ = await asyncio.wait(list(live.values()), timeout=max(0.0, remaining),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done and time.monotonic() >= deadline:
                for n, t in live.items():
                    t.cancel()
                await asyncio.gather(*live.values(), return_exceptions=True)
                for n in live:
                    run.state[n].update(status=TIMED_OUT, error="plan deadline reached")
                run.deadline_hit = True
                break
        # deadline: what never started is skipped
        for n in plan["order"]:
            s = run.state[n]
            if s["status"] == PENDING:
                run.deadline_hit = True
                s.update(status=SKIPPED, error="plan deadline reached")
        _settle(run)
    except asyncio.CancelledError:
        for t in tasks.values():
            t.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for n in plan["order"]:
            if run.state[n]["status"] in (PENDING, RUNNING):
                run.state[n].update(status=CANCELLED, error="run cancelled")
        raise
    finally:
        for n in plan["order"]:
            if n not in tasks and run.state[n]["status"] != SUCCEEDED:
                span_unrun(n)


def report(run: Run) -> Dict[str, Any]:
    stages = []
    for st in run.plan["stages"]:
        s = run.state[st["name"]]
        stages.append({
            **{k: v for k, v in st.items() if k != "sensitive"},
            "agent": s["agent"],
            "ok": s["status"] == SUCCEEDED,
            "output": s["output"] or ("" if s["status"] == SUCCEEDED else (s["error"] or "")),
            "elapsed_s": s["elapsed_s"],
            "status": s["status"],
            "attempts": s["attempts"],
            "error": s["error"],
            "artifacts": [a["kind"] for a in s["artifacts"]],
            "sensitive": st.get("sensitive"),
        })
    final = ""
    for x in reversed(stages):
        if x["ok"] and x["output"]:
            final = x["output"]
            break
    pending = [{"stage": st["name"], "specialization": st["specialization"],
                "why": st.get("sensitive")}
               for st in run.plan["stages"] if run.state[st["name"]]["status"] == AWAITING]
    return {
        "primary": run.plan.get("primary", ""),
        "reason": run.plan.get("reason", ""),
        "stages": stages,
        "final": final,
        "elapsed_s": round(run.elapsed_s, 3),
        "run_id": run.run_id,
        "status": run.status,
        "pending_approvals": pending,
        "budget": {"max_stage_runs": run.plan["budget"]["max_stage_runs"],
                   "stage_runs_used": run.runs_used,
                   "deadline_s": run.plan["budget"]["deadline_s"]},
        "order": list(run.plan["order"]),
        "ignored_approvals": list(run.ignored_approvals),
    }


async def _execute(run: Run, civilization: Dict[str, list]) -> Dict[str, Any]:
    if run.active:
        raise RunBusy(run.run_id)
    run.active = True
    run.status = "running"
    run.deadline_hit = run.budget_hit = False
    t0 = time.monotonic()
    try:
        await _drive(run, civilization)
        run.status = _aggregate(run)
    except asyncio.CancelledError:
        run.status = "cancelled"
        raise
    finally:
        run.elapsed_s = time.monotonic() - t0
        run.updated_at = time.time()
        run.active = False
    return report(run)


async def run_plan(plan: Dict[str, Any], civilization: Dict[str, list], data: Any = None, *,
                   approvals: Optional[Iterable[str]] = None, tenant_id: Optional[str] = None,
                   task_id: Optional[str] = None, store: Optional[RunStore] = None) -> Dict[str, Any]:
    """Validate and run ``plan``. ``approvals=None`` means a trusted internal
    caller (REPL, scripts, self-retest): the approval gate is off. HTTP callers
    always pass a set (possibly empty) and a ``tenant_id``; their run is kept
    in ``store`` for GET/resume."""
    normalized = normalize_plan(plan)
    run = Run(normalized, data, tenant_id, None if approvals is None else set(approvals), task_id)
    if tenant_id is not None:
        (store or RUNS).add(run)
    return await _execute(run, civilization)


async def resume_run(run_id: str, tenant_id: Optional[str], civilization: Dict[str, list], *,
                     approvals: Iterable[str] = (), store: Optional[RunStore] = None) -> Dict[str, Any]:
    """Resume a stored run: succeeded stages (and their artifacts) are kept and
    never re-executed; every other stage is reset to pending. A completed run
    is returned as-is (replay-safe, no execution)."""
    run = (store or RUNS).get(run_id, tenant_id)
    if run.active:
        raise RunBusy(run_id)
    run.add_approvals(approvals)
    if run.status == "completed":
        return report(run)
    for n, s in run.state.items():
        if s["status"] != SUCCEEDED:
            run.state[n] = Run._fresh()
    return await _execute(run, civilization)


def get_report(run_id: str, tenant_id: Optional[str], store: Optional[RunStore] = None) -> Dict[str, Any]:
    return report((store or RUNS).get(run_id, tenant_id))
