#!/usr/bin/env python3
"""Q-028 reviewer form completeness check (R-169 v4 b1) — STRUCTURE ONLY.

Reports whether a human reviewer's copy of ``reviewer_form.json`` is fully answered
and well-formed against the committed reference form. It never reads the raw replies,
never compares answers with the grader or the agent labels, and never outputs a verdict
or an agreement rate: what the reviewer answered is the reviewer's decision.

Usage (from repo root):
  python3 scripts/check_q028_form.py [FILLED_FORM]   # default: the committed form
Exit code 0 = complete, 1 = incomplete/ill-formed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/audit/M8/logs/raw/r169/v4/human_review/reviewer_form.json"
SECTIONS = ("adjudication_of_disagreements", "adjudication_of_uncertain", "label_verification_sample")
AGENT_OWNED = ("run", "agent_label", "grader_fail_reason")
OPTIONAL = ("comment",)
META = ("name_or_handle", "review_date", "role")


def answer_fields(case: dict) -> list:
    return [k for k in case if k not in AGENT_OWNED and k not in OPTIONAL]


def _answered(v) -> bool:
    return v is not None and not (isinstance(v, str) and not v.strip())


def check(filled: dict, reference: dict) -> dict:
    problems: list = []
    rev = filled.get("reviewer") or {}
    if rev.get("confirmed_human") is not True:
        problems.append("reviewer.confirmed_human must be true")
    for k in META:
        if not _answered(rev.get(k)):
            problems.append(f"reviewer.{k} is empty")
    cases = answered = 0
    for sec in SECTIONS:
        ref, got = reference.get(sec) or {}, filled.get(sec) or {}
        if set(ref) != set(got):
            problems.append(f"{sec}: case set changed (missing {sorted(set(ref) - set(got))}, extra {sorted(set(got) - set(ref))})")
        for cid, rcase in ref.items():
            cases += 1
            case = got.get(cid)
            if case is None:
                continue
            for k in AGENT_OWNED:
                if k in rcase and case.get(k) != rcase[k]:
                    problems.append(f"{sec}/{cid}: agent-owned field changed: {k}")
            missing = [k for k in answer_fields(rcase) if not _answered(case.get(k))]
            if missing:
                problems.append(f"{sec}/{cid}: unanswered {missing}")
            else:
                answered += 1
    return {"complete": not problems, "cases": cases, "answered": answered, "problems": problems}


def main(argv: list) -> int:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    path = Path(argv[1]) if len(argv) > 1 else REFERENCE
    result = check(json.loads(path.read_text(encoding="utf-8")), reference)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
