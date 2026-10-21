#!/usr/bin/env python3
"""R-169 v2 (D-095): run the SECOND version of the hallucination/quality
benchmark against the REAL model reachable at no cost, and re-score committed
runs offline.

    # freeze the exact measured configuration (must be committed BEFORE any
    # test-split run; the test run refuses to start otherwise)
    python scripts/r169_run_v2.py freeze

    # live run (needs the internet; never run by CI)
    python scripts/r169_run_v2.py run --config baseline --split dev
    python scripts/r169_run_v2.py run --config grounded-v2 --split test --final

    # offline: recompute scores/summary from a committed raw run
    python scripts/r169_run_v2.py rescore docs/audit/M8/logs/raw/r169/<run>.jsonl

Differences from the v1 runner (independent review of PR #214, finding 3):
the freeze now covers the COMPLETE executed configuration — dataset hash,
prompt fingerprints, scorer hash, THIS runner's hash, token limits, retry
policy, provider URL/model, and sha256 of every baseline dependency module —
so a post-freeze change to any of them makes the test-split run refuse to
start (the v1 freeze left these mutable; a verified mutation of
MAX_TOKENS_TEXT left the v1 freeze intact).

Provider: the project's own keyless path (agent_civilization.core.llm,
AGOS_LLM_KEYLESS=1 + per-request consent) — a public endpoint without an API
key or account, so no cost is incurred. Only synthetic fixtures and five
public facts are sent; no secret or user data is. OPENROUTER_API_KEY must be
unset. render.yaml and production settings are not touched.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169"
FREEZE = ROOT / "benchmarks" / "r169" / "v2" / "FREEZE.json"
SCORER = ROOT / "agent_civilization" / "eval" / "r169_bench_v2.py"
MAX_TOKENS_TEXT = 1024   # data_worker.process_text default (LLMClient.complete)
MAX_TOKENS_CODE = 800    # real_dispatcher._code_generator
RETRY_POLICY = "provider error: up to 2 retries (30s, 60s backoff), then recorded as provider_error"
# baseline execution dependencies: a hash change in any of these modules
# changes what "baseline" actually runs, so the freeze pins them all.
BASELINE_DEPS = (
    "agent_civilization/utils/__init__.py",
    "agent_civilization/agents/executors/data_worker.py",
    "agent_civilization/core/agents/real_dispatcher.py",
    "agent_civilization/core/llm.py",
    "agent_civilization/core/safety.py",
)


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def scorer_sha() -> str:
    return hashlib.sha256(SCORER.read_bytes()).hexdigest()


def runner_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _deps_sha() -> dict:
    out = {}
    for rel in BASELINE_DEPS:
        out[rel] = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16]
    return out


def freeze_state() -> dict:
    """The COMPLETE executed configuration (v2: this is what FREEZE.json
    pins — the v1 freeze missed runner/limits/retries/provider/deps)."""
    from agent_civilization.eval import r169_bench_v2 as b
    from agent_civilization.core import llm as llm_mod
    m = b.verify_dataset()
    return {"dataset_version": m["dataset_version"], "dataset_sha256": m["dataset_sha256"],
            "prompt_fingerprint": b.prompt_fingerprint(), "scorer_sha256": scorer_sha(),
            "runner_sha256": runner_sha(),
            "max_tokens_text": MAX_TOKENS_TEXT, "max_tokens_code": MAX_TOKENS_CODE,
            "retry_policy": RETRY_POLICY,
            "provider_url": llm_mod.KEYLESS_URL, "requested_model": llm_mod.KEYLESS_MODEL,
            "baseline_dependency_sha256": _deps_sha()}


def check_freeze(state: dict, frozen: dict) -> list:
    """Return the list of drifted keys (empty = exact frozen configuration)."""
    drift = []
    for k in ("dataset_version", "dataset_sha256", "prompt_fingerprint", "scorer_sha256",
              "runner_sha256", "max_tokens_text", "max_tokens_code", "retry_policy",
              "provider_url", "requested_model", "baseline_dependency_sha256"):
        if frozen.get(k) != state[k]:
            drift.append(k)
    return drift


async def _one(case: dict, config: str) -> dict:
    from agent_civilization.eval import r169_bench_v2 as b
    from agent_civilization.core.llm import get_llm
    t0 = time.monotonic()
    rec = {"id": case["id"], "config": config}
    if case["expected_behavior"] == "pass_tests":
        if config == "baseline":
            from agent_civilization.core.agents.real_dispatcher import _code_generator
            out = await _code_generator({"prompt": case["question"]})
            rec["handler_ok"] = bool(out.get("ok"))
            code = ((out.get("tool_output") or {}).get("code") or "") if out.get("ok") else ""
            rec["reply"] = code
            # honest provenance limit (v1 review finding 8): _code_generator does
            # not return the served model or the raw completion; we record the
            # placeholder rather than inferring the model from the shared client.
            rec["model"] = "keyless (not returned by _code_generator; same shared client)"
            rec["error"] = None if out.get("ok") else out.get("error")
        else:
            msgs = b.grounded_v2_messages(case)
            llm = get_llm()
            out = await llm.complete(msgs["system"], msgs["user"], max_tokens=MAX_TOKENS_CODE)
            reply = out.get("content") or ""
            code, err = b.extract_code(reply) if out.get("ok") else ("", "llm failed")
            if out.get("ok") and err:   # same single retry as the live generator
                out = await llm.complete(msgs["system"], f"{msgs['user']}\n\nYour previous answer was not valid "
                                         f"Python ({err}). Return only valid Python code.",
                                         max_tokens=MAX_TOKENS_CODE + 100)
                reply = out.get("content") or ""
                code, err = b.extract_code(reply) if out.get("ok") else ("", "llm failed")
            rec["reply"] = reply
            rec["model"] = out.get("model")
            rec["error"] = None if out.get("ok") else out.get("error")
            code = code if not err else ""
        rec["code"] = code
    else:
        if config == "baseline":
            from agent_civilization.agents.executors.data_worker import process_text
            out = await process_text(b.baseline_text_payload(case))
            rec["reply"] = out.get("answer") or ""
            rec["model"] = out.get("model")
            rec["error"] = None if out.get("ok") else out.get("error")
        else:
            msgs = b.grounded_v2_messages(case)
            out = await get_llm().complete(msgs["system"], msgs["user"], max_tokens=MAX_TOKENS_TEXT)
            rec["reply"] = out.get("content") or ""
            rec["model"] = out.get("model")
            rec["error"] = None if out.get("ok") else out.get("error")
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    return rec


def score_record(case: dict, rec: dict) -> dict:
    from agent_civilization.eval import r169_bench_v2 as b
    code_result = None
    if case["expected_behavior"] == "pass_tests" and rec.get("code"):
        code_result = b.run_code_tests(rec["code"], case["reference"]["tests"])
    s = b.score_case(case, rec.get("reply") or "", code=rec.get("code"), code_result=code_result,
                     provider_error=rec.get("error"))
    if code_result is not None:
        s["code_result"] = {k: code_result[k] for k in ("passed", "returncode", "timed_out")}
    return s


async def run(config: str, split: str, final: bool, tag: str) -> Path:
    from agent_civilization.eval import r169_bench_v2 as b
    if os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("refusing: OPENROUTER_API_KEY is set — this runner only uses the no-cost keyless path")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    state = freeze_state()
    sha = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--", ".", ":!docs/audit/M8/logs/raw/r169"))
    if split == "test":
        if not final:
            sys.exit("refusing: the held-out test split needs --final")
        if dirty:
            sys.exit("refusing: the tree is dirty — commit the frozen configuration first")
        if not FREEZE.exists():
            sys.exit("refusing: benchmarks/r169/v2/FREEZE.json is missing (run the freeze subcommand and commit it)")
        frozen = json.loads(FREEZE.read_text())
        drift = check_freeze(state, frozen)
        if drift:
            sys.exit(f"refusing: current configuration differs from FREEZE.json: {drift}")
    cases = b.load_split(split)
    from agent_civilization.core import llm as llm_mod
    from agent_civilization.core.llm import set_public_llm_consent, reset_public_llm_consent
    run_id = f"{split}_{config}_{tag}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{run_id}.jsonl"
    if path.exists():
        sys.exit(f"refusing to overwrite {path}")
    meta = {"kind": "meta", "run_id": run_id, "benchmark_version": "r169-v2", "config": config, "split": split,
            "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "code_sha": sha, "tree_dirty": dirty, **state,
            "max_tokens_text": MAX_TOKENS_TEXT, "max_tokens_code": MAX_TOKENS_CODE,
            "sampling": "provider defaults (the live path sends no temperature/seed)",
            "python": platform.python_version(), "cases": len(cases)}
    token = set_public_llm_consent(True)   # synthetic fixtures only (SEC-06 per-request consent)
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for i, case in enumerate(cases):
                rec = await _one(case, config)
                retried = []
                for backoff in (30, 60):             # up to two patient retries on a provider error
                    if not rec.get("error"):
                        break
                    retried.append(str(rec["error"])[:200])
                    await asyncio.sleep(backoff)
                    rec = await _one(case, config)
                if retried:
                    rec["retried_after_errors"] = retried   # kept in the raw record (auditable)
                rec["score"] = score_record(case, rec)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"[{i + 1}/{len(cases)}] {case['id']} pass={rec['score']['pass']} "
                      f"model={rec.get('model')} err={'y' if rec.get('error') else 'n'}", flush=True)
    finally:
        reset_public_llm_consent(token)
    write_summary(path)
    return path


def load_run(path: Path):
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[0], lines[1:]


def rescore(path: Path) -> list:
    """Recompute every verdict from the stored replies (code is re-executed)."""
    from agent_civilization.eval import r169_bench_v2 as b
    meta, recs = load_run(path)
    cases = {c["id"]: c for c in b.load_split(meta["split"])}
    return [score_record(cases[r["id"]], r) for r in recs]


def write_summary(path: Path) -> dict:
    from agent_civilization.eval import r169_bench_v2 as b
    meta, recs = load_run(path)
    scores = rescore(path)
    models = sorted({str(r.get("model")) for r in recs})
    summary = {"meta": meta, "models_reported": models, "scored_with_scorer_sha256": scorer_sha(),
               "mean_latency_s": round(sum(r.get("latency_s", 0) for r in recs) / max(1, len(recs)), 2),
               "metrics": b.aggregate(scores)}
    out = path.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", choices=["baseline", "grounded-v2"], required=True)
    r.add_argument("--split", choices=["dev", "validation", "test"], required=True)
    r.add_argument("--final", action="store_true")
    r.add_argument("--tag", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    s = sub.add_parser("rescore")
    s.add_argument("path")
    sub.add_parser("freeze-state")
    f = sub.add_parser("freeze")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        p = asyncio.run(run(a.config, a.split, a.final, a.tag))
        print(f"wrote {p}")
    elif a.cmd == "rescore":
        print(json.dumps(write_summary(Path(a.path))["metrics"]["pass"]))
    elif a.cmd == "freeze":
        FREEZE.parent.mkdir(parents=True, exist_ok=True)
        state = freeze_state()
        FREEZE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"wrote {FREEZE}")
    else:
        print(json.dumps(freeze_state(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
