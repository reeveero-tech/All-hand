#!/usr/bin/env python3
"""R-170 batch 13/13b — independent acceptance runs for the pr-st-3 fix and its
bare-claim-list supplement.

Pre-registered: docs/audit/M8/logs/raw/r170/hollow/PREREGISTRATION_PRST3.md and
PREREGISTRATION_PRST3B.md (written before the code changes). Reads the frozen
acceptance sets (separate from the frozen 244-case corpus; each sha256 is
pinned below and in its pre-registration), runs the gate
(AGOS_OUTPUT_VALIDATORS=1, in-process only) on each case's prose result, and
writes the evidence next to the other hollow logs. Refuses to overwrite
existing evidence. Offline, deterministic.

    python3 scripts/r170_prst3_acceptance.py a   # batch 13 set
    python3 scripts/r170_prst3_acceptance.py b   # batch 13b supplement set
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SETS = {  # set -> (cases path, pinned sha256)
    "a": (ROOT / "benchmarks/r170/hollow/acceptance_prst3.jsonl",
          "0f5038cb7350cfcbe66ccefd5546fda886c46325d8b78817ca63e298dc0a7f4e"),
    "b": (ROOT / "benchmarks/r170/hollow/acceptance_prst3b.jsonl",
          "178640ea3cbf2dcc10a8ed705ffab91bfec51a6f5568209a5291db6031c2bf41"),
}
OUT = ROOT / "docs/audit/M8/logs/raw/r170/hollow"


def load(which: str):
    cases_path, pinned = SETS[which]
    raw = cases_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pinned:
        raise SystemExit("acceptance set sha mismatch with the pre-registration")
    return [json.loads(x) for x in raw.decode().splitlines() if x.strip()]


def run(which: str) -> Path:
    os.environ["AGOS_OUTPUT_VALIDATORS"] = "1"  # in-process only
    from agent_civilization.core.output_validators import apply_output_verification, requirements_for
    path = OUT / f"acceptance_prst3{which}_a1.jsonl"
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip()
    rows = []
    for c in load(which):
        req = requirements_for(c["spec"], c["work"])
        ok, res = apply_output_verification(req, True, dict(c["prose_result"]))
        expect_ok = c["expect"] != "not_completable"
        rows.append({"id": c["id"], "expect": c["expect"], "gate_on_success": bool(ok),
                     "expected_ok": expect_ok, "match": bool(ok) == expect_ok,
                     "reasons": (res.get("verification") or {}).get("reasons")})
    matched = sum(1 for r in rows if r["match"])
    out = OUT / f"acceptance_prst3{which}_a1.score.json"
    out.write_text(json.dumps({"stem": f"acceptance_prst3{which}_a1", "code_sha": sha,
                               "cases_sha256": SETS[which][1], "n": len(rows),
                               "matched": matched,
                               "all_match": matched == len(rows),
                               "mismatches": [r for r in rows if not r["match"]]},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "code_sha": sha, "cases_sha256": SETS[which][1],
                            "n": len(rows)}, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "a"
    print(run(which), f"-> evidence written; score: acceptance_prst3{which}_a1.score.json")
