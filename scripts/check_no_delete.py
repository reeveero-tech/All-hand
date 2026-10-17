#!/usr/bin/env python3
"""R-152 (D-063/D-064): no-delete / no-move / no-rename gate for tracked files.

Usage: python scripts/check_no_delete.py [BASE] [HEAD]
       (defaults: origin/main HEAD — the same merge-base diff the governance
       gate uses, i.e. ``git diff BASE...HEAD``)

Exit codes: 0 = no tracked file was deleted, moved or renamed;
            1 = at least one deletion/move/rename (each is listed);
            2 = git error (bad ref, not a repository) — never treated as pass.

Design (D-064):
- Zero tolerance. There is deliberately NO in-repo override (no allowlist
  file, no PR-label or PR-body keyword): anything the PR itself can add or
  edit is not verifiable owner authorization. Until the owner authorizes a
  specific deletion through a separate documented decision (which would add
  a reviewed mechanism in its own batch), the rule is zero deletions.
- Detection uses ``--no-renames`` so a rename or a move always shows up as a
  deletion of the old path; it cannot hide behind rename similarity
  thresholds. A second pass with ``-M`` only labels which deletions were
  renames/moves for a clearer error message.
- ``-z`` output is parsed so paths with spaces, quotes or non-ASCII
  characters (e.g. Arabic file names) are reported exactly.
- Stdlib only; reads git state, never PR text.
"""
from __future__ import annotations

import subprocess
import sys


def _git(args: list[str], cwd: str | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True
    ).stdout


def _parse_z(raw: bytes) -> list[tuple[str, list[str]]]:
    """Parse ``git diff --name-status -z`` into (status, [paths])."""
    parts = raw.decode("utf-8", "surrogateescape").split("\0")
    out: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        if not status:
            i += 1
            continue
        # R/C entries carry two paths (old, new); everything else one.
        n = 2 if status[0] in "RC" else 1
        out.append((status, parts[i + 1 : i + 1 + n]))
        i += 1 + n
    return out


def find_violations(base: str, head: str, cwd: str | None = None) -> dict:
    """Return {'deleted': [...], 'renamed': [(old, new, score)], 'counts': {...}}."""
    rng = f"{base}...{head}"
    strict = _parse_z(
        _git(["diff", "--name-status", "--no-renames", "-z", rng], cwd)
    )
    deleted = sorted(p[0] for s, p in strict if s.startswith("D"))
    counts: dict[str, int] = {}
    for s, _ in strict:
        counts[s[0]] = counts.get(s[0], 0) + 1
    renamed: list[tuple[str, str, str]] = []
    if deleted:
        labelled = _parse_z(_git(["diff", "--name-status", "-M", "-z", rng], cwd))
        renamed = sorted(
            (p[0], p[1], s[1:]) for s, p in labelled if s.startswith("R")
        )
    return {"deleted": deleted, "renamed": renamed, "counts": counts}


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/main"
    head = argv[2] if len(argv) > 2 else "HEAD"
    try:
        res = find_violations(base, head)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        err = getattr(exc, "stderr", b"") or b""
        print(f"::error::no-delete check could not read git diff {base}...{head}: "
              f"{err.decode(errors='replace').strip() or exc}")
        return 2
    print(f"no-delete check: {base}...{head} status counts (no-renames): "
          f"{res['counts'] or 'empty diff'}")
    if not res["deleted"]:
        print("No-delete gate passed: 0 tracked files deleted, moved or renamed.")
        return 0
    moved_from = {old: (new, score) for old, new, score in res["renamed"]}
    for path in res["deleted"]:
        if path in moved_from:
            new, score = moved_from[path]
            print(f"::error file={path}::{path} renamed/moved to {new} (similarity {score}%)"
                  " — forbidden by D-063 (no delete/move/rename)")
        else:
            print(f"::error file={path}::{path} deleted — forbidden by D-063 (no delete/move/rename)")
    print(f"::error::No-delete gate failed: {len(res['deleted'])} tracked file(s) "
          "deleted, moved or renamed. Repair/upgrade/adapt instead; deletion needs a "
          "separate explicit owner authorization (D-063/D-064).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
