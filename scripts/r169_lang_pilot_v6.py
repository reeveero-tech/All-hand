#!/usr/bin/env python3
"""R-170 لاحق 72 — v6 pilot: language-fix candidate v4 judged on the v6 holdout.

Why v4 (log 296 root cause): the failed v3 candidate's rule 9 carried an Arabic
example only and an Arabic-focused "never write English" instruction, and it
was developed on Arabic dev only — so it over-generalized to English questions
(A2 failed: 3 English questions answered in Arabic/mixed). The v4 candidate
REBALANCES rule 9: symmetric instructions and BOTH examples (Arabic for Arabic
questions, English for English questions), and it is developed on BOTH languages
of the v5 dev split before being frozen and judged on the NEW v6 holdout
(46 unseen cases, per log 296: "judged on new unseen cases (v6 holdout)").

Nothing frozen is edited: ``grounded-v4-lang`` = the frozen grounded-v2 messages
with rule 9 replaced AT RUNTIME (module attribute swap inside this process);
every keyless request carries a seed (b4 method) so no provider cache is reused.

Pre-registration (committed before any run):
docs/audit/M8/logs/raw/r169/v6/lang_pilot/PREREGISTRATION_V6.md

    python3 scripts/r169_lang_pilot_v6.py run --split dev --arms v4      # development (v5 dev)
    python3 scripts/r169_lang_pilot_v6.py run --split dev --arms v2,v4   # development, paired
    python3 scripts/r169_lang_pilot_v6.py run --split holdout             # judgment (v6, interleaved 3 arms)
    python3 scripts/r169_lang_pilot_v6.py score                            # after the holdout run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import r169_run_v5 as v5run  # noqa: E402  (version-agnostic runner + score_record)
import r169_run_b4 as b4  # noqa: E402  (seeded keyless post + CALLS context)
from r169_language_analysis import mcnemar_exact  # noqa: E402

OUT = ROOT / "docs/audit/M8/logs/raw/r169/v6/lang_pilot"
PREREG = OUT / "PREREGISTRATION_V6.md"
ATTEMPT_TIMEOUT_S = 600
ARMS = ("grounded-v2", "grounded-v4-lang", "baseline")
RULE9_V2_PREFIX = "9. Write the ENTIRE reply in the language of the question"

# v4 — rebalanced rule 9 (symmetric instructions AND both examples; the v3
# failure mode was an Arabic-only example plus an Arabic-focused prohibition).
RULE9_V4 = (
    "9. Write the ENTIRE reply in the language of the question: Arabic if the question is Arabic, English "
    "if the question is English. The INSUFFICIENT_EVIDENCE token stays in English in every reply. For an "
    "Arabic question, everything after the token must be Arabic. Example (Arabic question): "
    "\"INSUFFICIENT_EVIDENCE — المصادر المتاحة لا تذكر هذه المعلومة.\" For an English question, everything "
    "after the token must be English. Example (English question): \"INSUFFICIENT_EVIDENCE — The available "
    "sources do not mention this fact.\" Match the question's language exactly and never mix the two "
    "languages in one reply.\n"
)


def v4_system(v2_system: str) -> str:
    start = v2_system.index(RULE9_V2_PREFIX)
    end = v2_system.index("\n", start) + 1
    return v2_system[:start] + RULE9_V4 + v2_system[end:]


def _bench_for(split: str):
    """dev uses the v5 bench (the tuning surface); holdout uses the v6 bench."""
    if split == "dev":
        from agent_civilization.eval import r169_bench_v5 as b
        return b
    if split == "holdout":
        from agent_civilization.eval import r169_bench_v6 as b
        return b
    raise SystemExit(f"refusing: split {split!r} is not dev or holdout")


def _prereg_committed() -> bool:
    if not PREREG.exists():
        return False
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(PREREG)],
                       cwd=ROOT, capture_output=True)
    return r.returncode == 0


async def _one_arm_case(bench, arm: str, case: dict) -> dict:
    """Run ONE case for ONE arm with the arm's system prompt swapped at runtime."""
    original = bench.GROUNDED_V2_SYSTEM
    try:
        if arm == "grounded-v4-lang":
            bench.GROUNDED_V2_SYSTEM = v4_system(original)
        rec, history = None, []
        b4.CALLS.clear()
        ctx = b4._case.set(case["id"])
        try:
            for attempt in range(3):
                try:
                    rec = await asyncio.wait_for(
                        v5run._one(case, "baseline" if arm == "baseline" else "grounded-v2"),
                        timeout=ATTEMPT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    rec = {"id": case["id"], "reply": "",
                           "error": f"attempt timeout > {ATTEMPT_TIMEOUT_S}s"}
                if not rec.get("error"):
                    break
                history.append(rec.get("error"))
                await asyncio.sleep(30 * (attempt + 1))
        finally:
            b4._case.reset(ctx)
        rec["config"], rec["calls"], rec["retry_errors"] = arm, list(b4.CALLS), history
        return rec
    finally:
        bench.GROUNDED_V2_SYSTEM = original


async def run(split: str, arms: list[str]) -> list[Path]:
    from agent_civilization.core import llm as _llm
    bench = _bench_for(split)
    if not _prereg_committed():
        raise SystemExit("refusing: PREREGISTRATION_V6.md is not committed (run nothing before the prereg)")
    if split == "holdout" and set(arms) != set(ARMS):
        raise SystemExit(f"refusing: the holdout judgment needs ALL THREE arms interleaved ({ARMS})")
    if os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("refusing: a paid key is set; this pilot uses the free keyless provider only")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    cases = bench.load_split(split)
    # interleaved, preregistered order: for case i, all arms before case i+1 —
    # separates arm identity from time drift (prereg §5).
    paths, handles = {}, {}
    for arm in arms:
        p = OUT / f"{split}_{arm}.jsonl"
        if p.exists():
            raise SystemExit(f"refusing to overwrite {p}")
        paths[arm] = p
        handles[arm] = p.open("w", encoding="utf-8")
        handles[arm].write(json.dumps(
            {"kind": "meta", "split": split, "config": arm, "cases": len(cases),
             "code_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                        capture_output=True, text=True).stdout.strip(),
             "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "system_prompt_sha256": hashlib.sha256(
                 (v4_system(bench.GROUNDED_V2_SYSTEM) if arm == "grounded-v4-lang"
                  else bench.GROUNDED_V2_SYSTEM).encode()).hexdigest(),
             "prereg": str(PREREG.relative_to(ROOT))}) + "\n")
    _llm._keyless_post = b4.make_seeded_post(f"lang-pilot-v6|{split}")
    consent = _llm.set_public_llm_consent(True)  # synthetic benchmark fixtures only (as b4)
    try:
        for i, case in enumerate(cases, 1):
            for arm in arms:
                rec = await _one_arm_case(bench, arm, case)
                handles[arm].write(json.dumps(rec, ensure_ascii=False) + "\n")
                handles[arm].flush()
                hits = sum(1 for x in rec["calls"] if (x.get("x_cache") or "").upper() == "HIT")
                print(f"[{i}/{len(cases)}] {arm.split('-')[-1]} {case['id']} "
                      f"calls={len(rec['calls'])} hits={hits} err={'y' if rec.get('error') else 'n'}",
                      flush=True)
                await asyncio.sleep(6)  # provider politeness (b4 spacing)
    finally:
        for h in handles.values():
            h.close()
        _llm.reset_public_llm_consent(consent)
    return [paths[a] for a in arms]


def _scores(split: str, arm: str) -> dict:
    bench = _bench_for(split)
    p = OUT / f"{split}_{arm}.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    n_declared = meta["cases"]
    rows = [json.loads(x) for x in lines[1:] if x.strip()]
    if len(rows) != n_declared:
        raise SystemExit(f"refusing: {p.name} has {len(rows)}/{n_declared} cases — incomplete run")
    cases = {c["id"]: c for c in bench.load_split(split)}
    return {r["id"]: v5run.score_record(cases[r["id"]], r) for r in rows}


def score() -> dict:
    """Paired comparison on the v6 holdout (prereg §6) + descriptive baseline arms."""
    from agent_civilization.eval import r169_bench_v6 as b6
    cases = {c["id"]: c for c in b6.load_split("holdout")}
    runs = {arm: _scores("holdout", arm) for arm in ARMS}

    def paired(a, c, key, sub=lambda k: True):
        ids = sorted(set(a) & set(c))
        both = [k for k in ids if sub(k) and not a[k].get("provider_error") and not c[k].get("provider_error")
                and a[k].get(key) is not None and c[k].get(key) is not None]
        a_only = sum(1 for k in both if a[k][key] and not c[k][key])
        c_only = sum(1 for k in both if c[k][key] and not a[k][key])
        return {"n": len(both), "a": sum(1 for k in both if a[k][key]), "b": sum(1 for k in both if c[k][key]),
                "a_only": a_only, "b_only": c_only, "mcnemar_exact_p": round(mcnemar_exact(a_only, c_only), 5)}

    res = {"runs": {arm: {"n": len(r), "provider_errors": sum(1 for v in r.values() if v.get("provider_error")),
                          "language_match_none": sum(1 for v in r.values() if v.get("language_match") is None)}
                    for arm, r in runs.items()}, "comparisons": {}}
    pairs = [("grounded-v2", "grounded-v4-lang"), ("baseline", "grounded-v4-lang"), ("baseline", "grounded-v2")]
    for a_cfg, c_cfg in pairs:
        a, c = runs[a_cfg], runs[c_cfg]
        res["comparisons"][f"{a_cfg} (a) vs {c_cfg} (b)"] = {
            "language_match_ar": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "ar"),
            "language_match_en": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "en"),
            "pass": paired(a, c, "pass"),
            "pass_ar": paired(a, c, "pass", lambda k: cases[k]["lang"] == "ar"),
            "pass_en": paired(a, c, "pass", lambda k: cases[k]["lang"] == "en")}
    v = res["comparisons"]["grounded-v2 (a) vs grounded-v4-lang (b)"]
    ar, en, ps = v["language_match_ar"], v["language_match_en"], v["pass"]
    n_err = sum(res["runs"][x]["provider_errors"] for x in ("grounded-v2", "grounded-v4-lang"))
    n_cases = len(cases)
    res["criteria"] = {"A1": ar["b"] > ar["a"] and ar["b_only"] - ar["a_only"] >= 3 and ar["a_only"] <= 1,
                      "A2": en["a_only"] - en["b_only"] <= 1,
                      "A3": ps["a_only"] - ps["b_only"] <= 2,
                      "provider_error_share": round(n_err / max(2 * n_cases, 1), 4),
                      "inconclusive_by_errors": n_err > 0.10 * 2 * n_cases}
    (OUT / "holdout_score.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", required=True, choices=["dev", "holdout"])
    r.add_argument("--arms", default="v2,v4,baseline")
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        arms = {"v2": "grounded-v2", "v4": "grounded-v4-lang", "baseline": "baseline"}
        chosen = [arms[x] for x in a.arms.split(",") if x.strip()]
        for p in asyncio.run(run(a.split, chosen)):
            print(p)
    else:
        print(json.dumps(score(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
