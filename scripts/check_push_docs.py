#!/usr/bin/env python3
"""R-187 (D-102): push-scope documentation check.

The owner's single binding rule (D-102): documentation travels WITH every
push — same push, same batch, never after. The existing gates judge the PR
aggregate diff (``origin/main...HEAD``), so a PR whose first push carries
docs but whose second push is code-only passes them. This check closes that
gap at the same-event scope:

    python3 scripts/check_push_docs.py BEFORE AFTER

- BEFORE/AFTER = github.event.before / github.event.after of a push event.
- Normal push: changed files = ``git diff --name-only BEFORE AFTER`` — the
  TREE diff of the exact commits this push delivered (two-dot, NOT
  merge-base: a merge-base diff is empty on rollbacks and drags in changes
  already contained in BEFORE on divergent history).
- Branch creation (BEFORE = 40 zeros): scope = the branch's own commits vs
  origin/main (merge-base semantics fit here — ``origin/main...AFTER``);
  falls back to local ``main`` then a direct diff against the empty tree.
- Branch deletion / no-op (AFTER = 40 zeros): exit 0 (nothing to check).
- Rules: the SAME engine as check_atomic_batch.py (R0..R6) — this script adds
  a scope, not a second opinion on what needs docs.

Exit codes: 0 = this push carries its docs; 1 = rule violated (R-187);
            2 = git error (never treated as a pass).

Honest limit (documented in QUALITY.md): CI runs after the ref is updated, so
a red check cannot prevent the push from ARRIVING — it makes the violation
loud (red run on the branch/main, blocking merges via required checks). CI
prevents merges, not push arrival.
"""
from __future__ import annotations

import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import check_atomic_batch as cab  # noqa: E402

ZEROS = "0" * 40
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# The push-scope check shares the atomic-batch rule engine (R0..R6) — it adds
# a scope, not a second opinion on what needs docs.
evaluate = cab.evaluate


def _rev_exists(repo: str, ref: str) -> bool:
    res = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                         cwd=repo, capture_output=True, text=True)
    return res.returncode == 0


def _tree_diff(before: str, after: str, cwd: str | None = None) -> list[str]:
    """Two-dot tree diff — the actual changes a push delivered, regardless
    of ancestry (merge-base diffs are empty on rollback and wrong on
    divergent history)."""
    out = subprocess.run(["git", "diff", "--name-only", "-z", f"{before}..{after}"],
                         cwd=cwd, check=True, capture_output=True)
    return [p for p in out.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def push_scope(before: str, after: str, cwd: str | None = None) -> list[str]:
    """Changed files for THIS push only (same-event scope)."""
    if after == ZEROS:
        return []  # branch deletion / no-op: nothing delivered
    if before == ZEROS:  # branch creation: the branch's own commits
        for base in ("origin/main", "main"):
            if _rev_exists(cwd or ".", base):
                return cab.changed_files(base, after, cwd=cwd)
        return _tree_diff(EMPTY_TREE, after, cwd=cwd)
    return _tree_diff(before, after, cwd=cwd)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: check_push_docs.py BEFORE AFTER", file=sys.stderr)
        return 2
    before, after = argv[1], argv[2]
    repo = "."
    try:
        if after != ZEROS:
            if not _rev_exists(repo, after):
                print(f"::error::after ref not found: {after}")
                return 2
            if before != ZEROS and not _rev_exists(repo, before):
                print(f"::error::before ref not found: {before}")
                return 2
        changed = push_scope(before, after, cwd=repo)
    except subprocess.CalledProcessError as e:
        print(f"::error::git failed: {e}")
        return 2
    if not changed:
        print("Push-scope docs check passed (R-187): empty push, nothing to document.")
        return 0
    violations = cab.evaluate(changed)
    for v in violations:
        print(f"::error::R-187 {v}")
    if violations:
        print("R-187: the docs must travel WITH the push, not after it "
              "(aggregate PR diffs do not count).")
        return 1
    print("Push-scope docs check passed (R-187): documentation traveled with this push.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
