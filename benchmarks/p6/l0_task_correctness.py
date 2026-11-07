"""R-179 L0 (D-137): 100-task CORRECTNESS run on the throwaway LOCAL stack.

NOT a capacity measurement and NOT a step of the R-179 ladder (D-134: no
mini-ladders as a substitute). It exercises, on a real SUT process with the
real local PostgREST store, exactly the R-179 correctness criteria that do
not depend on scale, and evaluates them through benchmarks/p6/metrics.py
with the thresholds of benchmarks/p6/acceptance_criteria.json:

  * r179.silent_loss            — accepted vs terminal rows after the drain
  * r179.duplicate_side_effects — PROXY: task_completed events per task_id
                                  (L0 has no external recipient; an executed
                                  task writes exactly one completion event)
  * r179.reconciliation_ratio   — API state == DB row state == event state
  * idempotency                 — every key resubmitted once (half of them
                                  CONCURRENTLY with the original, the race
                                  path): same task_id, exactly one DB row

Generator and SUT are separate OS processes on ONE 2-CPU sandbox (process-
level separation only). Tool-only workload (validator + JSON prompt) — no
LLM provider is called.

Usage: R177_STORAGE_URL=http://127.0.0.1:54321 R177_STORAGE_KEY=... \
       python3 benchmarks/p6/l0_task_correctness.py --json out.json [--n 100]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.util
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def rest(path: str) -> list:
    url = os.environ["R177_STORAGE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["R177_STORAGE_KEY"]
    req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--drain-timeout-s", type=float, default=180)
    a = ap.parse_args()
    r177 = _load("r177", REPO / "scripts" / "r177_per_service.py")
    m = _load("p6_metrics", REPO / "benchmarks" / "p6" / "metrics.py")
    crit = {c["id"]: c for c in json.loads(
        (REPO / "benchmarks" / "p6" / "acceptance_criteria.json").read_text())["criteria"]}
    run = "l0-" + secrets.token_hex(4)
    keys = [f"{run}-{i:04d}" for i in range(a.n)]
    sut = r177.SutProcess(storage="live-local")
    key_a = sut.tenants.a_key
    body = lambda k: {"specialization": "validator", "prompt": r177.TRACK_PROMPT, "idempotency_key": k}  # noqa: E731
    post = lambda k: r177._http(sut.url + "/api/tasks", "POST", key_a, body(k), timeout=15)  # noqa: E731
    out: dict = {"tool": "benchmarks/p6/l0_task_correctness.py", "run": run, "n": a.n,
                 "storage": "live-local PostgREST", "workload": "validator + JSON (tool-only, no LLM)",
                 "not": "not a capacity measurement; not an R-179 ladder step (D-134)"}
    try:
        out["startup_s"] = round(sut.start(), 2)
        first: dict = {}
        dup: dict = {}
        with cf.ThreadPoolExecutor(max_workers=16) as ex:
            # half: original and duplicate fired TOGETHER (race path)
            race = keys[: a.n // 2]
            futs = {}
            for k in race:
                futs[ex.submit(post, k)] = ("first", k)
                futs[ex.submit(post, k)] = ("dup", k)
            for f in cf.as_completed(futs):
                kind, k = futs[f]
                (first if kind == "first" else dup)[k] = f.result()
            # half: original, then the duplicate after it was accepted
            seq = keys[a.n // 2:]
            for k, res in zip(seq, ex.map(post, seq)):
                first[k] = res
            for k, res in zip(seq, ex.map(post, seq)):
                dup[k] = res
        tid = lambda r: r[1].get("task_id") if isinstance(r[1], dict) else None  # noqa: E731
        accepted = {k: tid(first[k]) for k in keys if first[k][0] in (200, 202) and tid(first[k])}
        # for the race half either response may be the "original": both must agree
        idem_mismatch = [k for k in keys
                         if tid(first[k]) is None or tid(dup[k]) is None or tid(first[k]) != tid(dup[k])]
        out["submit_status_codes"] = sorted({str(first[k][0]) for k in keys} | {str(dup[k][0]) for k in keys})
        out["idempotency_mismatches"] = len(idem_mismatch)
        ids = sorted(set(accepted.values()))
        # drain
        api_state: dict = {}
        t0 = time.monotonic()
        while time.monotonic() - t0 < a.drain_timeout_s:
            for i in ids:
                if api_state.get(i) in r177.TERMINAL:
                    continue
                st, b = r177._http(f"{sut.url}/api/tasks/{i}", key=key_a, timeout=10)
                if st == 200 and isinstance(b, dict):
                    api_state[i] = b.get("status")
            if all(api_state.get(i) in r177.TERMINAL for i in ids):
                break
            time.sleep(0.5)
        out["drain_s"] = round(time.monotonic() - t0, 2)
        inlist = "(" + ",".join(ids) + ")"
        rows = rest("tasks?select=task_id,status,idempotency_key&task_id=in." + urllib.parse.quote(inlist))
        by_key = rest("tasks?select=task_id,idempotency_key&idempotency_key=like." + urllib.parse.quote(run + "-*"))
        ev = rest("events?select=event_data&event_type=eq.task_completed&event_data->>task_id=in."
                  + urllib.parse.quote(inlist))
        db_state = {r["task_id"]: r["status"] for r in rows}
        ev_ids = [e["event_data"]["task_id"] for e in ev]
        ev_state = {e["event_data"]["task_id"]: ("completed" if e["event_data"].get("ok") else "failed") for e in ev}
        rows_per_key: dict = {}
        for r in by_key:
            rows_per_key[r["idempotency_key"]] = rows_per_key.get(r["idempotency_key"], 0) + 1
        out["db_rows_for_run_keys"] = len(by_key)
        out["db_rows_for_ids"] = len(rows)
        out["task_completed_events"] = len(ev_ids)  # non-vacuous: must equal accepted
        out["keys_with_more_than_one_row"] = sum(1 for v in rows_per_key.values() if v > 1)
        out["final_states"] = {s: list(api_state.values()).count(s) for s in set(api_state.values())}
        terminal = [i for i, s in db_state.items() if s in r177.TERMINAL]
        evals = [
            m.evaluate(crit["r179.silent_loss"], accepted_ids=ids, terminal_ids=terminal),
            m.evaluate(crit["r179.duplicate_side_effects"], effect_keys=ev_ids),
            m.evaluate(crit["r179.reconciliation_ratio"], api_state=api_state,
                       db_state=db_state, event_state=ev_state),
        ]
        out["criteria"] = evals
        out["accepted"] = len(ids)
        out["l0_correct"] = (len(ids) == a.n and out["idempotency_mismatches"] == 0
                             and out["keys_with_more_than_one_row"] == 0
                             and len(by_key) == a.n and len(rows) == a.n
                             and len(ev_ids) == a.n
                             and all(e["verdict"] == "pass" for e in evals))
    finally:
        sut.stop()
    pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "criteria"}, indent=1))
    for e in out.get("criteria", []):
        print(e["id"], e["verdict"], e["value"], "approved" if e["approved"] else "threshold proposed")
    return 0 if out.get("l0_correct") else 1


if __name__ == "__main__":
    sys.exit(main())
