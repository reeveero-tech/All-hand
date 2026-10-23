#!/usr/bin/env python3
"""Q-028 Phase A blind form completeness check — STRUCTURE ONLY, Phase-A-safe.

Checks a human reviewer's returned ``reviewer_blind_form.json`` (Phase A) for
completeness and well-formedness. Unlike ``check_q028_form.py`` (official form,
Phase B), this tool:

* never reads the official form, the Phase B mapping, raw replies, grader output
  or agent labels, so running it at receipt cannot expose Phase B information;
* prints only structural problems keyed by the blind references C01..C27;
* never prints a verdict distribution, an agreement rate, or any judgment.

What the reviewer answered is the reviewer's decision; this tool does not judge it.

Usage:
  python3 scripts/check_q028_blind_form.py FILLED_FORM [--blank BLANK_FORM] [--council]

``--council`` (D-100): checks a form returned by an AI council member. In this
mode ``reviewer.confirmed_human`` MUST be exactly ``false`` (a model may never
claim to be human) and ``reviewer.role`` must declare an AI council member;
``confirmed_no_prior_verdicts`` must still be true. All other checks are equal.
Exit code 0 = complete, 1 = incomplete/ill-formed, 2 = usage/parse error.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

VERDICTS = ("pass", "fail", "uncertain")
CONFIDENCE = ("low", "medium", "high")
EXPECTED_REFS = tuple(f"C{i:02d}" for i in range(1, 28))
META = ("name_or_handle", "review_date", "role")
CONFIRMATIONS = ("confirmed_human", "confirmed_no_prior_verdicts")
FROZEN_TOP = ("kind", "frozen_evaluated_commit")


def _filled(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


COUNCIL_ROLE_MARK = "ai council member"


def check(filled: dict, blank: dict | None = None, council: bool = False) -> dict:
    problems: list = []
    if not isinstance(filled, dict):
        return {"complete": False, "cases": 0, "answered": 0, "problems": ["form is not a JSON object"]}
    rev = filled.get("reviewer") if isinstance(filled.get("reviewer"), dict) else {}
    if council:
        if rev.get("confirmed_human") is not False:
            problems.append("reviewer.confirmed_human must be false for an AI council member (D-100)")
        if rev.get("confirmed_no_prior_verdicts") is not True:
            problems.append("reviewer.confirmed_no_prior_verdicts must be true (set only if it is actually true)")
        role = rev.get("role")
        if not (isinstance(role, str) and COUNCIL_ROLE_MARK in role.lower()):
            problems.append(f"reviewer.role must declare '{COUNCIL_ROLE_MARK}' (D-100)")
    else:
        for k in CONFIRMATIONS:
            if rev.get(k) is not True:
                problems.append(f"reviewer.{k} must be true (set only if it is actually true)")
    for k in META:
        if not _filled(rev.get(k)):
            problems.append(f"reviewer.{k} is empty")
    if blank is not None:
        for k in FROZEN_TOP:
            if filled.get(k) != blank.get(k):
                problems.append(f"top-level field changed: {k}")
    cases = filled.get("cases")
    if not isinstance(cases, list):
        return {"complete": False, "cases": 0, "answered": 0, "problems": problems + ["cases must be a list"]}
    refs = [c.get("case_ref") if isinstance(c, dict) else None for c in cases]
    expected = tuple(c.get("case_ref") for c in blank["cases"]) if blank is not None else EXPECTED_REFS
    if tuple(refs) != tuple(expected):
        missing = sorted(set(expected) - set(r for r in refs if r))
        extra = sorted(set(r for r in refs if r) - set(expected))
        problems.append(f"case set/order changed (missing {missing}, extra {extra}, count {len(refs)})")
    blank_by_ref = {c.get("case_ref"): c for c in blank["cases"]} if blank is not None else {}
    answered = 0
    for c in cases:
        if not isinstance(c, dict):
            problems.append("non-object case entry")
            continue
        ref = c.get("case_ref", "?")
        bad = []
        if c.get("independent_judgment") not in VERDICTS:
            bad.append("independent_judgment (pass|fail|uncertain)")
        if not _filled(c.get("rationale")):
            bad.append("rationale")
        conf = c.get("confidence")
        if not (isinstance(conf, str) and conf.strip().lower() in CONFIDENCE):
            bad.append("confidence (low|medium|high)")
        b = blank_by_ref.get(ref)
        if b is not None and c.get("anchor") != b.get("anchor"):
            problems.append(f"{ref}: anchor changed")
        if bad:
            problems.append(f"{ref}: unanswered/invalid {bad}")
        else:
            answered += 1
    return {"complete": not problems, "cases": len(cases), "answered": answered, "problems": problems}


def main(argv: list) -> int:
    args = list(argv[1:])
    blank_path = None
    council = False
    if "--council" in args:
        council = True
        args.remove("--council")
    if "--blank" in args:
        i = args.index("--blank")
        if i + 1 >= len(args):
            print("usage: check_q028_blind_form.py FILLED_FORM [--blank BLANK_FORM] [--council]", file=sys.stderr)
            return 2
        blank_path = Path(args[i + 1])
        del args[i : i + 2]
    if len(args) != 1:
        print("usage: check_q028_blind_form.py FILLED_FORM [--blank BLANK_FORM] [--council]", file=sys.stderr)
        return 2
    path = Path(args[0])
    raw = path.read_bytes()
    try:
        filled = json.loads(raw.decode("utf-8"))
        blank = json.loads(blank_path.read_text(encoding="utf-8")) if blank_path else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"complete": False, "problems": [f"parse error: {exc}"]}, ensure_ascii=False))
        return 2
    result = check(filled, blank, council=council)
    result["mode"] = "council (D-100, AI — not human)" if council else "human"
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
