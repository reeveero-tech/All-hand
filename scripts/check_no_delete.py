#!/usr/bin/env python3
"""R-152 (D-063/D-064): no-delete / no-move / no-rename gate for tracked files.

Usage: python scripts/check_no_delete.py [BASE] [HEAD]
       (defaults: origin/main HEAD — the same merge-base diff the governance
       gate uses, i.e. ``git diff BASE...HEAD``)

Exit codes: 0 = no tracked file was deleted, moved or renamed;
            1 = at least one deletion/move/rename (each is listed);
            2 = git error (bad ref, not a repository) — never treated as pass.

Design (D-064, extended by D-152 / R-203):
- Zero tolerance by default. The only way a deletion passes is a reviewed
  deletion list that is ALREADY MERGED: ``docs/governance/deletions/DEL-NNN.list``
  read from the merge base of BASE and HEAD (``git show <merge-base>:...``),
  never from the PR's own tree. Anything the PR itself adds or edits still
  authorizes nothing (the D-064 objection to an in-repo allowlist). There is
  still no PR-label, PR-body or environment override.
- A list names its owner decision (``# decision: D-NNN``) and the D-NNN
  section of DECISIONS.md at the same commit must cite the list file name.
  Paths are exact strings (no globs, no directories); one path per line.
- Lists are immutable once merged (modifying or deleting one fails), and a
  PR that deletes files may not add or change a list in the same diff, so
  authorization (batch 1) and deletion (batch 2) are always separate merges.
- A list added by the PR is validated against the PR's own tree (format,
  decision present and citing it, every path tracked at HEAD) so a malformed
  list cannot land on main.
- Detection uses ``--no-renames`` so a rename or a move always shows up as a
  deletion of the old path; it cannot hide behind rename similarity
  thresholds. A second pass with ``-M`` only labels which deletions were
  renames/moves for a clearer error message.
- ``-z`` output is parsed so paths with spaces, quotes or non-ASCII
  characters (e.g. Arabic file names) are reported exactly.
- Stdlib only; reads git state, never PR text.
"""
from __future__ import annotations

import re
import subprocess
import sys

LIST_DIR = "docs/governance/deletions/"
LIST_RE = re.compile(r"^docs/governance/deletions/(DEL-\d{3})\.list$")
DECISIONS = "docs/governance/DECISIONS.md"
LIST_README = LIST_DIR + "README.md"  # the human explanation of the lists
DECISION_RE = re.compile(r"^#\s*decision:\s*(D-\d{3,})\s*$")


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


def _show(rev: str, path: str, cwd: str | None) -> str | None:
    try:
        return _git(["show", f"{rev}:{path}"], cwd).decode("utf-8")
    except subprocess.CalledProcessError:
        return None


def _tracked(rev: str, cwd: str | None, prefix: str = "") -> list[str]:
    args = ["ls-tree", "-r", "-z", "--name-only", rev]
    if prefix:
        args += ["--", prefix]
    raw = _git(args, cwd).decode("utf-8", "surrogateescape")
    return [p for p in raw.split("\0") if p]


def _decision_section(decisions: str, did: str) -> str | None:
    """Text of the D-NNN section (from its heading to the next decision heading)."""
    m = re.search(rf"^#{{2,3}} {re.escape(did)}\b.*$", decisions, re.M)
    if not m:
        return None
    nxt = re.search(r"^#{2,3} D-\d{3,}\b", decisions[m.end():], re.M)
    return decisions[m.start(): m.end() + (nxt.start() if nxt else len(decisions))]


def parse_list(list_id: str, text: str, decisions: str | None,
               tracked: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (paths, problems) for one deletion list."""
    problems: list[str] = []
    paths: list[str] = []
    did = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#"):
            m = DECISION_RE.match(line)
            if m:
                did = m.group(1)
            continue
        if line != line.strip():
            problems.append(f"line {n}: surrounding whitespace")
        elif line.startswith("/") or line.endswith("/") or ".." in line.split("/"):
            problems.append(f"line {n}: not an exact relative file path: {line}")
        elif line.startswith(LIST_DIR) or line == DECISIONS:
            problems.append(f"line {n}: deletion lists and DECISIONS.md cannot be listed")
        elif line in paths:
            problems.append(f"line {n}: duplicate path: {line}")
        else:
            if tracked is not None and line not in tracked:
                problems.append(f"line {n}: not a tracked file: {line}")
            paths.append(line)
    if not did:
        problems.append("missing '# decision: D-NNN' header")
    elif decisions is None:
        problems.append(f"{DECISIONS} not found")
    else:
        sec = _decision_section(decisions, did)
        if sec is None:
            problems.append(f"decision {did} not found in {DECISIONS}")
        elif f"{list_id}.list" not in sec:
            problems.append(f"decision {did} does not cite {LIST_DIR}{list_id}.list")
    if not paths:
        problems.append("lists no path")
    return paths, problems


def load_lists(rev: str, cwd: str | None, validate_tracked: bool = False) -> dict:
    """{list_id: {'paths': [...], 'problems': [...]}} for the lists present at rev."""
    out: dict[str, dict] = {}
    files = [p for p in _tracked(rev, cwd, LIST_DIR) if LIST_RE.match(p)]
    if not files:
        return out
    decisions = _show(rev, DECISIONS, cwd)
    tracked = set(_tracked(rev, cwd)) if validate_tracked else None
    for f in files:
        lid = LIST_RE.match(f).group(1)
        paths, problems = parse_list(lid, _show(rev, f, cwd) or "", decisions, tracked)
        out[lid] = {"paths": paths, "problems": problems, "file": f}
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
    changed = {p[0]: s[0] for s, p in strict}
    merge_base = _git(["merge-base", base, head], cwd).decode().strip()
    base_lists = load_lists(merge_base, cwd)
    allowed: dict[str, str] = {}
    for lid, info in sorted(base_lists.items()):
        if info["problems"]:
            continue  # an invalid list authorizes nothing
        for p in info["paths"]:
            allowed.setdefault(p, lid)
    list_changes = sorted(p for p, st in changed.items()
                          if LIST_RE.match(p) or (p.startswith(LIST_DIR) and p != LIST_README))
    problems: list[str] = []
    base_list_files = {i["file"] for i in base_lists.values()}
    for p in list_changes:
        if p in base_list_files:
            problems.append(f"{p}: merged deletion lists are immutable (status {changed[p]})")
        elif p == LIST_README:
            continue
        elif not LIST_RE.match(p):
            problems.append(f"{p}: only docs/governance/deletions/DEL-NNN.list files belong here")
    new_ids = {LIST_RE.match(p).group(1) for p in list_changes
               if LIST_RE.match(p) and p not in base_list_files and changed[p] == "A"}
    if new_ids:
        head_lists = load_lists(head, cwd, validate_tracked=True)
        for lid in sorted(new_ids):
            for prob in head_lists.get(lid, {"problems": ["unreadable"]})["problems"]:
                problems.append(f"{LIST_DIR}{lid}.list: {prob}")
    if deleted and list_changes:
        problems.append("a PR that deletes files may not add or change a deletion list in the "
                        "same diff: merge the list (with its decision) first, delete in a later PR")
    authorized = {p: allowed[p] for p in deleted if p in allowed}
    unauthorized = [p for p in deleted if p not in allowed]
    return {"deleted": deleted, "renamed": renamed, "counts": counts,
            "authorized": authorized, "unauthorized": unauthorized,
            "problems": problems, "merge_base": merge_base, "new_lists": sorted(new_ids),
            "lists": {k: len(v["paths"]) for k, v in base_lists.items() if not v["problems"]},
            "invalid_lists": {k: v["problems"] for k, v in base_lists.items() if v["problems"]}}


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
    print(f"deletion lists merged at merge-base {res['merge_base'][:12]}: "
          f"{res['lists'] or 'none'}")
    if res["new_lists"]:
        print(f"new deletion lists in this PR (validated, authorize nothing until merged): "
              f"{res['new_lists']}")
    for lid, probs in sorted(res["invalid_lists"].items()):
        print(f"::warning::{LIST_DIR}{lid}.list is invalid and authorizes nothing: {'; '.join(probs)}")
    rc = 0
    for prob in res["problems"]:
        print(f"::error::{prob}")
        rc = 1
    by_list: dict[str, int] = {}
    for lid in res["authorized"].values():
        by_list[lid] = by_list.get(lid, 0) + 1
    if res["authorized"]:
        print(f"authorized deletions (D-152 mechanism): {len(res['authorized'])} "
              f"by list {dict(sorted(by_list.items()))}")
    moved_from = {old: (new, score) for old, new, score in res["renamed"]}
    for path in res["unauthorized"]:
        if path in moved_from:
            new, score = moved_from[path]
            print(f"::error file={path}::{path} renamed/moved to {new} (similarity {score}%)"
                  " — forbidden by D-063 (not in a merged deletion list)")
        else:
            print(f"::error file={path}::{path} deleted — forbidden by D-063 (not in a merged "
                  "deletion list)")
        rc = 1
    if res["unauthorized"]:
        print(f"::error::No-delete gate failed: {len(res['unauthorized'])} tracked file(s) "
              "deleted, moved or renamed without a merged deletion list. Repair/upgrade/adapt "
              "instead, or merge a DEL-NNN.list with its owner decision first (D-064/D-152).")
    if rc == 0:
        if res["deleted"]:
            print(f"No-delete gate passed: {len(res['deleted'])} deletion(s), all in merged "
                  "deletion lists.")
        else:
            print("No-delete gate passed: 0 tracked files deleted, moved or renamed.")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
