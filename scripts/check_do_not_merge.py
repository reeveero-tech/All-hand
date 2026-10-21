#!/usr/bin/env python3
"""R-186 (merge-guard): block merging PRs that carry an explicit hold marker.

Context (2026-10-21 incident): PRs #215/#216/#217 were all merged into main
from the owner account while their titles/bodies said "DO NOT MERGE" /
"لا يُدمج" (acceptance blockers Q-028 and the acceptance measurement were
not satisfied). Branch protection could not stop this because
`enforce_admins` was off: an admin account bypasses every required check.
R-186 adds a machine-checked, per-PR merge barrier: the required
`governance` check FAILS while the PR (at its current state, fetched live
from the API at check time) carries a hold marker in its title or body.

Usage:
  python scripts/check_do_not_merge.py --repo OWNER/REPO --pr NUMBER
      # CI mode: fetch title/body via `gh api` (GH_TOKEN) and verify.
  python scripts/check_do_not_merge.py --title-file F --body-file F
      # test/local mode: verify files directly (no network).

Exit codes: 0 = no hold marker; 1 = marker found (merge must be refused);
            2 = lookup/parse error — never treated as a pass.

Design notes (D-097):
- Markers are matched case-insensitively on word-ish boundaries:
  "do not merge", "do-not-merge", "dnm" is deliberately NOT matched (too
  collision-prone), Arabic "لا يُدمج"/"لا يدمج" with any diacritics
  stripped, and "hold-merge"/"hold merge" as an explicit English alias.
- The check reads the PR's LIVE state (not the workflow event payload,
  which can go stale if the PR is edited after the check ran).
- A missing marker is not proof a PR is mergeable: this gate only
  enforces explicit holds. Acceptance criteria live in ROADMAP items and
  the status-evidence check (R-154).
- Zero in-repo override: the PR itself cannot whitelist its own merge,
  because anything the PR can edit is not owner authorization.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata

EN_MARKERS = [
    r"do[\s\-]*not[\s\-]*merge",
    r"hold[\s\-]*merge",
]

# Arabic: "لا" + optional diacritics + "يُدمج/يدمج/يُدمَج" (any voweling),
# tolerant of the tatweel and spacing variants.
AR_MARKERS = [
    r"لا\s*يُ?دُ?مُ?ج",
    r"لا\s*يَدْمُ?ج",
    r"لا\s*يدمج",
]


def _strip_marks(text: str) -> str:
    """Remove Arabic diacritics/tatweel so voweling cannot hide a marker."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Mn" or ch == "ـ":  # combining marks + tatweel
            continue
        out.append(ch)
    return "".join(out)


def find_markers(title: str, body: str) -> list[dict]:
    """Return every hold marker found in the PR title/body.

    Pure function (unit-tested): detection never depends on the network.
    """
    hits: list[dict] = []
    for where, text in (("title", title or ""), ("body", body or "")):
        hay = _strip_marks(text)
        hay = re.sub(r"[\u200f\u200e]", "", hay)  # strip RTL/LTR marks
        patterns = EN_MARKERS + AR_MARKERS
        for pat in patterns:
            m = re.search(pat, hay, re.IGNORECASE)
            if m:
                start = max(0, m.start() - 40)
                end = min(len(hay), m.end() + 40)
                hits.append({
                    "where": where,
                    "pattern": pat,
                    "context": hay[start:end].strip(),
                })
    # de-duplicate identical hits (EN+AR patterns can overlap)
    uniq: list[dict] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    return uniq


def _fetch_pr(repo: str, number: int) -> dict:
    """Fetch the PR's live title/body via the GitHub CLI (auth via GH_TOKEN)."""
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{number}", "--jq",
         '{"title": .title, "body": .body, "state": .state, "draft": .draft, "merged": .merged}'],
        check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"gh api failed ({proc.returncode}): {proc.stderr}\n")
        sys.exit(2)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"cannot parse gh api output: {exc}\n")
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R-186 do-not-merge gate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo-pr", nargs=2, metavar=("REPO", "NUMBER"),
                   help="CI mode: fetch the PR live via gh api")
    g.add_argument("--title-file", help="local mode: file with the PR title")
    ap.add_argument("--body-file", help="local mode: file with the PR body")
    args = ap.parse_args(argv)

    if args.repo_pr:
        repo, num = args.repo_pr
        pr = _fetch_pr(repo, int(num))
        title, body = pr.get("title") or "", pr.get("body") or ""
        state, merged = pr.get("state"), pr.get("merged")
        if state != "open" or merged:
            # already merged/closed — nothing to guard; do not fail the
            # historical PR's checks retroactively.
            print(f"PR is {state} (merged={merged}) — nothing to guard.")
            return 0
    else:
        if not args.title_file or not args.body_file:
            sys.stderr.write("--title-file and --body-file are required together\n")
            return 2
        with open(args.title_file, encoding="utf-8") as fh:
            title = fh.read()
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()

    hits = find_markers(title, body)
    if hits:
        print("::error::PR carries an explicit hold marker — merging is refused "
              "until the marker is removed by the owner after acceptance (R-186). "
              "Markers are an owner instruction, not a PR-editable switch.")
        for h in hits:
            print(f"  found in {h['where']}: ...{h['context']}...")
        return 1
    print("No hold marker in PR title/body (R-186).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
