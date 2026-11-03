"""R-174 batch 4 (D-124): the quality signal fed from runtime telemetry.

Scope (what this module IS):

* ``runtime_quality()`` — builds the TaskSpec quality callable from the
  runtime's OWN operational counters (``tool_runtime.stats()``), a snapshot
  taken when the callable is built. This wires the automatic feed that batch
  1 declared out of scope ("operator-supplied only").

The score is an OPERATIONAL RELIABILITY ratio, honestly defined:

* ``executed`` counts every run that passed the gate (incremented before the
  handler runs, so it includes runs that later failed);
* the failures that ARE the tool's own operational quality are ``timed_out``,
  ``output_capped``, ``output_malformed`` and ``isolated_crashed`` — the tool exceeded its own
  registered budget, blew its output cap, or crashed its isolated worker;
* ``refused`` (gate refusals: lifecycle/health/approval/scopes policy) and
  ``net_blocked`` (network policy) are NOT quality failures — they are
  task-policy mismatches, not the tool misbehaving, and scoring them would
  punish tools for callers asking the wrong thing;
* score = (executed - failures) / executed, clamped to [0, 1]; a tool with no
  run signal scores 0.0 — neutral, never negative.

Honest limits (what it is NOT):

* the snapshot is taken when the contract is derived — later counter changes
  do not retroactively change a selection (documented, tested);
* ``executed`` is not "succeeded": the counters cannot see the handler's own
  ok/fail result, only operational failures the gate itself detected — the
  ratio is a reliability signal, not a correctness signal;
* the counters are process-wide and best-effort (they reset with
  ``reset_runtime_registry``); a fresh process scores every tool 0.0.
"""
from __future__ import annotations

from typing import Callable

from agent_civilization.core import tool_runtime
from agent_civilization.core.tool_registry import ToolRecord

#: counter families the tool is NOT blamed for (policy, not misbehaviour)
_NOT_QUALITY = ("refused", "net_blocked")


def runtime_quality() -> Callable[[ToolRecord], float]:
    """The TaskSpec quality callable fed from the runtime's operational
    counters (a snapshot taken now). See the module docstring for the exact
    score and its honest limits."""
    snapshot = tool_runtime.stats()
    executed = snapshot.get("executed", {})
    timed_out = snapshot.get("timed_out", {})
    output_capped = snapshot.get("output_capped", {})
    output_malformed = snapshot.get("output_malformed", {})
    isolated_crashed = snapshot.get("isolated_crashed", {})

    def quality(rec: ToolRecord) -> float:
        key = f"{rec.tool_id}@{rec.version}"
        ran = float(executed.get(key, 0))
        if ran <= 0:
            return 0.0                     # no run signal: neutral, never negative
        failures = (float(timed_out.get(key, 0)) + float(output_capped.get(key, 0))
                    + float(output_malformed.get(key, 0))
                    + float(isolated_crashed.get(key, 0)))
        return max(0.0, (ran - failures) / ran)

    return quality


__all__ = ["runtime_quality"]
