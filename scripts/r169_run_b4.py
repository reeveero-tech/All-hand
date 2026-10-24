#!/usr/bin/env python3
"""R-169 b4: an INDEPENDENT, reproducible re-measurement (cache-busting by seed).

b3 (2026-10-24) returned 236/236 replies byte-identical to b2: the free keyless
provider caches identical requests (verified: a repeated request answered with
``X-Cache: HIT``). b4 differs from the frozen v5 run in exactly ONE declared
way: every keyless request carries a ``seed`` derived from the case id and the
call index (``sha256("r169-b4|<run_tag>|<case_id>|<call_index>")[:8]``). A new
seed is a new cache key (verified: fresh generation, no ``X-Cache``); the same
seed again is a cache HIT, so a b4 run is reproducible from its seeds.

Everything else is the frozen v5 path, imported, not copied: ``r169_run_v5._one``
(prompts, handlers, max tokens), the retry policy (30 s, 60 s), the dataset,
the scorer. The v5 freeze must show NO drift. The frozen files are not edited:
the seed is added by wrapping ``agent_civilization.core.llm._keyless_post`` at
runtime for the duration of the run (the wrapper also records the provider's
``X-Cache`` header per request, so a cached answer is visible, not assumed).

    python scripts/r169_run_b4.py run --config grounded-v2 --tag b4
    python scripts/r169_run_b4.py forged-sheet <run_id>    # reply-only sheet (blind)
    python scripts/r169_run_b4.py score <run_id>           # needs committed forged labels

Model-level metrics only: this path never passes through agent.execute, so the
R-170 completion gate does not act here (gate-level results are separate).
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import urllib.request as _urlreq
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import r169_run_v5 as v5  # noqa: E402

OUT_DIR = v5.OUT_DIR / "b4"
PREREG = OUT_DIR / "PREREGISTRATION_B4.md"
LABELS_DIR = OUT_DIR / "labels"
SPLIT = "test"
SEED_MODULUS = 100_000

_case = contextvars.ContextVar("r169_b4_case", default=None)
CALLS: list = []           # per-request provenance of the CURRENT case


def seed_for(run_tag: str, case_id: str, call_index: int) -> int:
    h = hashlib.sha256(f"r169-b4|{run_tag}|{case_id}|{call_index}".encode()).hexdigest()
    # amendment A1 (before any valid run): large seeds drew HTTP 400 from the
    # provider in the aborted first attempt; keep seeds < 100000.
    return int(h[:8], 16) % SEED_MODULUS


def make_seeded_post(run_tag: str):
    """A drop-in for llm._keyless_post that adds ``seed`` and records X-Cache."""
    from agent_civilization.core import llm, safety

    def seeded_post(system: str, prompt: str, max_tokens: int, timeout: float) -> dict:
        case_id = _case.get() or "?"
        seed = seed_for(run_tag, case_id, len(CALLS))
        body = json.dumps({
            "model": llm.KEYLESS_MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "seed": seed,
        }).encode()
        req = _urlreq.Request(llm.KEYLESS_URL, data=body, method="POST",
                              headers={"Content-Type": "application/json",
                                       "User-Agent": "AGOS-Civilization/1.0"})
        entry = {"seed": seed, "x_cache": None}
        CALLS.append(entry)
        with safety.safe_urlopen(req, timeout=timeout) as resp:
            entry["x_cache"] = resp.headers.get("X-Cache")
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    return seeded_post


def guard(dirty: bool, drift: list, published: bool, prereg: bool) -> "str | None":
    if drift:
        return f"v5 freeze drift: {drift}"
    if dirty:
        return "the tree is dirty"
    if not published:
        return "HEAD is not on any remote branch"
    if not prereg:
        return f"missing pre-registration {PREREG.relative_to(ROOT)}"
    return None


async def run(config: str, tag: str) -> Path:
    from agent_civilization.core import llm
    from agent_civilization.eval import r169_bench_v5 as b
    if os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("refusing: OPENROUTER_API_KEY is set — no-cost keyless path only")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    state = v5.freeze_state()
    frozen = json.loads(v5.FREEZE.read_text())
    dirty = bool(v5.git("status", "--porcelain", "--", ".", ":!docs/audit/M8/logs/raw/r169"))
    err = guard(dirty, v5.check_freeze(state, frozen),
                bool(v5.git("branch", "-r", "--contains", "HEAD")), PREREG.exists())
    if err:
        sys.exit(f"refusing: {err}")
    run_id = f"{SPLIT}_{config}_{tag}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{run_id}.jsonl"
    if path.exists():
        sys.exit(f"refusing to overwrite {path}")
    cases = b.load_split(SPLIT)
    meta = {"kind": "meta", "run_id": run_id, "benchmark_version": "r169-v5", "protocol": "b4",
            "config": config, "split": SPLIT,
            "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "code_sha": v5.git("rev-parse", "HEAD"), "tree_dirty": dirty, **state,
            "b4_runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sampling": "provider defaults + per-request seed (sha256 of tag|case|call)",
            "python": platform.python_version(), "cases": len(cases)}
    original = llm._keyless_post
    llm._keyless_post = make_seeded_post(tag)
    token = llm.set_public_llm_consent(True)   # synthetic fixtures only
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for i, case in enumerate(cases):
                ctx = _case.set(case["id"])
                CALLS.clear()
                try:
                    rec = await v5._one(case, config)
                    history = []                      # amendment A1: kept across retries
                    for backoff in (30, 60):          # v5 retry policy (30 s, 60 s)
                        if not rec.get("error"):
                            break
                        history.append({"error": str(rec.get("error") or ""), "reply": rec.get("reply") or "",
                                        "model": rec.get("model")})
                        await asyncio.sleep(backoff)
                        rec = await v5._one(case, config)
                    if history:
                        rec["attempt_history"] = history
                    rec["calls"] = list(CALLS)
                finally:
                    _case.reset(ctx)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                hits = sum(1 for c in CALLS if (c.get("x_cache") or "").upper() == "HIT")
                print(f"[{i + 1}/{len(cases)}] {case['id']} calls={len(CALLS)} cache_hits={hits} "
                      f"err={'y' if rec.get('error') else 'n'}", flush=True)
    finally:
        llm.reset_public_llm_consent(token)
        llm._keyless_post = original
    return path


def records(run_id: str):
    lines = (OUT_DIR / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), [json.loads(x) for x in lines[1:]]


def forged_sheet(run_id: str) -> Path:
    """Reply-only sheet for the forged-success family (no verdict computed)."""
    from agent_civilization.eval import r169_bench_v5 as b
    cases = {c["id"]: c for c in b.load_split(SPLIT)}
    _, recs = records(run_id)
    out = [f"# b4 forged-success blind sheet — `{run_id}`\n",
           "> Replies only. No grader verdict was computed before these labels.\n"]
    for r in recs:
        c = cases[r["id"]]
        if not c["reference"].get("forged_success"):
            continue
        tool = " || ".join(s["text"] for s in c["sources"] if s.get("kind") == "tool_result")
        out.append(f"## `{r['id']}` ({c['lang']})\n\n**Question**: {c['question']}\n\n"
                   f"**Tool result**: `{tool}`\n\n**Reply**\n\n````text\n{r.get('reply') or ''}\n````\n")
    p = OUT_DIR / f"{run_id}.forged_sheet.md"
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def score(run_id: str) -> dict:
    from agent_civilization.eval import r169_bench_v5 as b
    lab = LABELS_DIR / f"{run_id}.forged.labels.jsonl"
    if not lab.exists() or v5.git("ls-files", str(lab.relative_to(ROOT))) == "" \
            or v5.git("status", "--porcelain", "--", str(lab.relative_to(ROOT))):
        sys.exit(f"refusing: forged-family blind labels not committed: {lab}")
    cases = {c["id"]: c for c in b.load_split(SPLIT)}
    meta, recs = records(run_id)
    scores = [v5.score_record(cases[r["id"]], r) for r in recs]
    agg = b.aggregate(scores)
    labels = {d["id"]: d for d in map(json.loads, lab.read_text(encoding="utf-8").splitlines())
              if d.get("kind") != "meta"}
    forged_ids = [r["id"] for r in recs if cases[r["id"]]["reference"].get("forged_success")]
    agree = []
    for r, s in zip(recs, scores):
        if r["id"] in labels:
            agree.append({"id": r["id"], "label": labels[r["id"]]["label"], "grader_pass": s["pass"]})
    calls = [c for r in recs for c in r.get("calls", [])]
    out = {"run_id": run_id, "metrics": agg,
           "forged_family_labels_vs_grader": agree,
           "forged_ids": forged_ids,
           "requests": len(calls),
           "cache_hits": sum(1 for c in calls if (c.get("x_cache") or "").upper() == "HIT"),
           "models": sorted({str(r.get("model")) for r in recs})}
    (OUT_DIR / f"{run_id}.summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", choices=["baseline", "grounded-v2"], required=True)
    r.add_argument("--tag", required=True)
    sub.add_parser("forged-sheet").add_argument("run_id")
    sub.add_parser("score").add_argument("run_id")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        print(asyncio.run(run(a.config, a.tag)))
    elif a.cmd == "forged-sheet":
        print(forged_sheet(a.run_id))
    else:
        print(json.dumps(score(a.run_id), ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
