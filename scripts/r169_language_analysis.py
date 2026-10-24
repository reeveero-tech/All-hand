#!/usr/bin/env python3
"""R-169 language-match regression analysis (R-170 batch 12) — OFFLINE, reproducible.

Reads ONLY committed raw reply files (no provider calls), rescoring each record
with the frozen v5 grader (``r169_run_v5.score_record``). Outputs:
* paired comparison baseline vs grounded-v2 on the SAME cases (exact McNemar),
* per-case classification of every language mismatch (deterministic rules),
* run-to-run consistency (b2 vs b4s) of the mismatching cases.
Model quality only: the R-170 gate does not run on this path.

    python3 scripts/r169_language_analysis.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import r169_run_v5 as v5  # noqa: E402
from agent_civilization.eval import r169_bench_v5 as bench  # noqa: E402

RAW = ROOT / "docs/audit/M8/logs/raw/r169/v5"
RUNS = {  # run -> {config: file}
    "b2": {"grounded-v2": RAW / "test_grounded-v2_b2r2.jsonl", "baseline": RAW / "test_baseline_b2.jsonl"},
    "b4s": {"grounded-v2": RAW / "b4/test_grounded-v2_b4s.jsonl", "baseline": RAW / "b4/test_baseline_b4s.jsonl"},
}
AR, LA = re.compile(r"[\u0621-\u064A]"), re.compile(r"[A-Za-z]")


def records(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines[1:] if x.strip()]


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def classify(case: dict, reply: str) -> str:
    """Deterministic cause classes for a language mismatch (first match wins)."""
    t = reply or ""
    stripped = re.sub(r"```.*?```|`[^`]*`|[\[【［][^\]】］]*[\]】］]|https?://\S+", " ", t, flags=re.S)
    after_token = re.sub(r"INSUFFICIENT_EVIDENCE", " ", stripped)
    ar, la = len(AR.findall(after_token)), len(LA.findall(after_token))
    if case["lang"] == "ar":
        if "INSUFFICIENT_EVIDENCE" in t and ar == 0:
            return "ar_q:token_then_english_explanation"
        if ar == 0:
            return "ar_q:reply_entirely_english"
        if re.search(r"\b(status|summary|PASSED|FAILED|checks|total|passed|failed)\b", after_token):
            return "ar_q:mixed_tool_field_names_in_english"
        return "ar_q:mixed_other_latin"
    if ar > 0 and la == 0:
        return "en_q:reply_entirely_arabic"
    if ar > 0:
        return "en_q:mixed_arabic"
    return "en_q:other(terse_or_no_common_word)"


def analyse():
    cases = {c["id"]: c for c in bench.load_split("test")}
    out = {"inputs": {r: {k: str(p.relative_to(ROOT)) for k, p in d.items()} for r, d in RUNS.items()},
           "runs": {}}
    per_run_fail = {}
    for run, files in RUNS.items():
        sc = {}
        for cfg, p in files.items():
            sc[cfg] = {}
            for r in records(p):
                s = v5.score_record(cases[r["id"]], r)
                sc[cfg][r["id"]] = (s, r.get("reply") or "")
        both = sorted(k for k in sc["baseline"] if k in sc["grounded-v2"]
                      and sc["baseline"][k][0].get("language_match") is not None
                      and sc["grounded-v2"][k][0].get("language_match") is not None)
        b = sum(1 for k in both if sc["baseline"][k][0]["language_match"] and not sc["grounded-v2"][k][0]["language_match"])
        c = sum(1 for k in both if not sc["baseline"][k][0]["language_match"] and sc["grounded-v2"][k][0]["language_match"])
        mism = {}
        for cfg in sc:
            rows = []
            for k, (s, reply) in sorted(sc[cfg].items()):
                if s.get("language_match") is False:
                    rows.append({"id": k, "lang": cases[k]["lang"], "task_type": cases[k]["task_type"],
                                 "abstained": s.get("abstained"), "cause": classify(cases[k], reply),
                                 "reply_head": reply[:160]})
            mism[cfg] = rows
        by = lambda rows, key: {v: sum(1 for r in rows if r[key] == v) for v in sorted({r[key] for r in rows})}  # noqa: E731
        lang_split = {}
        for cfg in sc:
            for lang in ("ar", "en"):
                judged = [k for k, (s, _) in sc[cfg].items() if cases[k]["lang"] == lang and s.get("language_match") is not None]
                ok = sum(1 for k in judged if sc[cfg][k][0]["language_match"])
                lang_split[f"{cfg}:{lang}"] = [ok, len(judged)]
        out["runs"][run] = {
            "paired_n": len(both), "baseline_only_match": b, "grounded_only_match": c,
            "mcnemar_exact_p": round(mcnemar_exact(b, c), 5),
            "match_by_lang": lang_split,
            "mismatch_by_cause": {cfg: by(rows, "cause") for cfg, rows in mism.items()},
            "mismatch_by_task_type": {cfg: by(rows, "task_type") for cfg, rows in mism.items()},
            "mismatch_abstained": {cfg: sum(1 for r in rows if r["abstained"]) for cfg, rows in mism.items()},
            "mismatches": mism,
        }
        per_run_fail[run] = {cfg: {r["id"] for r in rows} for cfg, rows in mism.items()}
    out["consistency_b2_vs_b4s"] = {
        cfg: {"both": len(per_run_fail["b2"][cfg] & per_run_fail["b4s"][cfg]),
              "b2_only": len(per_run_fail["b2"][cfg] - per_run_fail["b4s"][cfg]),
              "b4s_only": len(per_run_fail["b4s"][cfg] - per_run_fail["b2"][cfg])}
        for cfg in ("grounded-v2", "baseline")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(RAW / "b4/LANGUAGE_ANALYSIS.json"))
    a = ap.parse_args(argv)
    res = analyse()
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
