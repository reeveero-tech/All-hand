#!/usr/bin/env python3
"""R-170 batch 12 — hollow-success evaluation (pre-registered:
docs/audit/M8/logs/raw/r170/hollow/PREREGISTRATION.md).

    python3 scripts/r170_hollow_eval.py run --split dev|validation|test --tag T
    python3 scripts/r170_hollow_eval.py score <stem>

Offline and deterministic (no network, no model). For each case the HANDLER is
run (families *_completable / *_not_completable) or the supplied prose result is
used (prose_*); the outcome is recorded with the gate OFF (handler ok) and ON
(``apply_output_verification`` with requirements captured BEFORE the handler).
The ORACLE below checks correctness against the case's independent truth and
never calls core/task_fit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = ROOT / "benchmarks/r170/hollow/cases.jsonl"
OUT = ROOT / "docs/audit/M8/logs/raw/r170/hollow"
PINNED_SHA = "4a3a623fc7a88be1f3d65bcb63d37b5d0e35e271f4323b9708b72ec5e8f1988b"


def wilson(k: int, n: int):
    if n == 0:
        return [None, None]
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def load(split: str):
    raw = CASES.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_SHA:
        raise SystemExit("corpus sha mismatch with the pre-registration")
    return [c for c in (json.loads(x) for x in raw.decode().splitlines()) if c["split"] == split]


def _n(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def oracle(case: dict, result: dict) -> tuple:
    """Correctness vs the independent truth (None = not applicable)."""
    t, spec = case["truth"], case["spec"]
    if case["expect"] != "completable":
        return None, ""
    if case["family"] == "prose_correct":
        return True, "hand-written correct answer"
    to = result.get("tool_output") or {}
    if spec == "strategist":
        got = [_n(s.get("goal", "")) for s in to.get("roadmap") or []]
        want = [_n(o) for o in t["objectives"]]
        return got == want, f"roadmap {got} vs {want}"
    if spec == "architect":
        got = {c.get("name") for c in to.get("components") or []}
        return set(t["components"]) <= got, f"components {sorted(got)} ⊇ {t['components']}"
    if spec == "designer":
        src = str(to.get("source", "")).lower()
        key = t.get("hex") or t.get("word")
        ok = key.lower() in src and to.get("theme") == t.get("theme", "light")
        return ok, f"source={src!r} theme={to.get('theme')} want {key}/{t.get('theme', 'light')}"
    if spec == "reviewer":
        kinds = {f.get("kind") for f in to.get("findings") or []}
        if not t["defects"]:
            return not kinds, f"findings {sorted(kinds)} on clean code"
        return set(t["defects"]) <= kinds, f"findings {sorted(kinds)} ⊇ {t['defects']}"
    return None, ""


async def run(split: str, tag: str) -> Path:
    os.environ["AGOS_OUTPUT_VALIDATORS"] = "1"  # in-process only, for the gate-on verdict
    from agent_civilization.core.agents.real_dispatcher import _HANDLERS
    from agent_civilization.core.output_validators import apply_output_verification, requirements_for
    path = OUT / f"{split}_{tag}.jsonl"
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    rows = []
    for c in load(split):
        work = json.loads(json.dumps(c["work"]))
        req = requirements_for(c["spec"], work)  # captured BEFORE the handler
        if c["family"].startswith("prose_"):
            res = dict(c["prose_result"])
            off = None  # not a handler output: nothing checks it with the gate off (A1)
        else:
            res = await _HANDLERS[c["spec"]](work)
            off = bool(res.get("ok"))
        on_ok, on_res = apply_output_verification(req, bool(res.get("ok")), res)
        correct, why = oracle(c, res)
        rows.append({"id": c["id"], "spec": c["spec"], "lang": c["lang"], "family": c["family"],
                     "expect": c["expect"], "gate_off_success": off, "gate_on_success": bool(on_ok),
                     "handler_error": None if res.get("ok") else res.get("error"),
                     "gate_reasons": (on_res.get("verification") or {}).get("reasons") if isinstance(on_res, dict) else None,
                     "answer": str(res.get("answer") or res.get("error") or "")[:300],
                     "oracle_correct": correct, "oracle_note": why[:300]})
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "split": split, "tag": tag, "code_sha": sha,
                            "cases_sha256": PINNED_SHA, "n": len(rows)}) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def score(stem: str) -> dict:
    rows = [json.loads(x) for x in (OUT / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines()[1:]]
    def rate(sel, pred):
        n = len(sel); k = sum(1 for r in sel if pred(r))
        return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}
    out = {"stem": stem}
    for mode in ("gate_off", "gate_on"):
        key = f"{mode}_success"
        sel = [r for r in rows if r[key] is not None]
        nc = [r for r in sel if r["expect"] == "not_completable"]
        co = [r for r in sel if r["expect"] == "completable"]
        out[mode] = {"H1_hollow_detected": rate(nc, lambda r: not r[key]),
                     "H2_false_rejection": rate(co, lambda r: not r[key]),
                     "H3_accepted_correct": rate([r for r in co if r[key]], lambda r: r["oracle_correct"]),
                     "by_spec": {s: {"H1": rate([r for r in nc if r["spec"] == s], lambda r: not r[key]),
                                     "H2": rate([r for r in co if r["spec"] == s], lambda r: not r[key])}
                                 for s in ("strategist", "architect", "designer", "reviewer")},
                     "by_lang": {lg: {"H1": rate([r for r in nc if r["lang"] == lg], lambda r: not r[key]),
                                      "H2": rate([r for r in co if r["lang"] == lg], lambda r: not r[key])}
                                 for lg in ("ar", "en")},
                     "by_family": {fam: rate([r for r in sel if r["family"] == fam],
                                             lambda r: r[key] == (r["expect"] == "completable"))
                                   for fam in sorted({r["family"] for r in sel})},
                     "false_rejections": [{"id": r["id"], "why": r["handler_error"] or r["gate_reasons"]}
                                          for r in co if not r[key]],
                     "hollow_accepted": [{"id": r["id"], "answer": r["answer"][:160]} for r in nc if r[key]],
                     "accepted_incorrect": [{"id": r["id"], "note": r["oracle_note"]}
                                            for r in co if r[key] and r["oracle_correct"] is False]}
    (OUT / f"{stem}.score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    r.add_argument("--tag", required=True)
    sub.add_parser("score").add_argument("stem")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        print(asyncio.run(run(a.split, a.tag)))
    else:
        s = score(a.stem)
        for m in ("gate_off", "gate_on"):
            print(m, {k: (v["k"], v["n"], v["wilson95"]) for k, v in s[m].items() if k.startswith("H")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
