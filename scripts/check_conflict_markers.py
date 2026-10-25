#!/usr/bin/env python3
"""Governance gate: reject merge-conflict markers added by a PR.

Usage: python scripts/check_conflict_markers.py [BASE] [HEAD]
       (defaults: origin/main HEAD — same merge-base diff as the other gates,
       i.e. ``git diff BASE...HEAD``)

Exit codes: 0 = no conflict marker added;
            1 = at least one added line is a conflict marker (each is listed);
            2 = git error (bad ref, not a repository) — never treated as pass.

Why (log 295): on 2026-10-25 a rebase committed and pushed unresolved
``<<<<<<<``/``>>>>>>>`` blocks into ROADMAP/PROJECT_STATE/CHANGELOG and every
existing gate (no-delete, atomic v2, status evidence) passed.

Design:
- Only lines ADDED by the PR are inspected (``-U0`` diff), so historical
  files are never re-judged.
- A line is a marker if it starts with exactly seven ``<``, ``>`` or ``|``
  followed by a space or end of line (git's ``merge``/``diff3`` formats).
- A bare ``=======`` line is common in recorded pytest output, so it is
  reported only when the same file also gains a ``<<<<<<<``/``>>>>>>>`` line.
- Quoted paths (non-ASCII such as Arabic names, quotes, tabs) are unquoted;
  ``core.quotePath=false`` is also passed. Binary files are skipped by git. Stdlib only; reads git state only.
"""
from __future__ import annotations

import re
import subprocess
import sys

MARKER = re.compile(r"^(<{7}|>{7}|\|{7})(?: |$)")
SEPARATOR = re.compile(r"^={7}$")


def _unquote(target: str) -> str:
    """Undo git's C-style path quoting (``"b/\\331\\205.md"``) so non-ASCII,
    quoted or tab-containing paths are scanned, never silently skipped."""
    if not (len(target) >= 2 and target[0] == '"' and target[-1] == '"'):
        return target
    body, out, i = target[1:-1], bytearray(), 0
    simple = {"n": 10, "t": 9, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "r": 13, "v": 11}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567" and all(ch in "01234567" for ch in body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
        out.extend(c.encode("utf-8", "surrogateescape"))
        i += 1
    return out.decode("utf-8", "surrogateescape")


def scan(diff_text: str) -> list[tuple[str, int, str]]:
    """Return (path, new_line_no, line) for every added conflict marker."""
    hits: dict[str, list[tuple[int, str]]] = {}
    seps: dict[str, list[tuple[int, str]]] = {}
    path = None
    line_no = 0
    for raw in diff_text.split("\n"):
        if raw.startswith("+++ "):
            # git appends one TAB after paths containing spaces (patch compat)
            target = raw[4:]
            if target.endswith("\t"):
                target = target[:-1]
            target = _unquote(target)
            path = target[2:] if target.startswith("b/") else None
            continue
        if raw.startswith("@@"):
            m = re.match(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@", raw)
            line_no = int(m.group(1)) if m else 0
            continue
        if path is None or not raw.startswith("+"):
            continue
        body = raw[1:].rstrip("\r")
        if MARKER.match(body):
            hits.setdefault(path, []).append((line_no, body))
        elif SEPARATOR.match(body):
            seps.setdefault(path, []).append((line_no, body))
        line_no += 1
    out: list[tuple[str, int, str]] = []
    for p, items in hits.items():
        out.extend((p, n, b) for n, b in items)
        out.extend((p, n, b) for n, b in seps.get(p, []))
    return sorted(out)


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/main"
    head = argv[2] if len(argv) > 2 else "HEAD"
    try:
        raw = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "-U0", f"{base}...{head}"],
            check=True, capture_output=True,
        ).stdout.decode("utf-8", "surrogateescape")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        err = getattr(exc, "stderr", b"") or b""
        print(f"conflict-marker gate: git error: {err.decode(errors='replace').strip() or exc}")
        return 2
    found = scan(raw)
    if found:
        print(f"Conflict-marker gate FAILED: {len(found)} marker line(s) added in {base}...{head}:")
        for p, n, b in found:
            print(f"  {p}:{n}: {b[:80]}")
        return 1
    print(f"Conflict-marker gate passed: no conflict markers added in {base}...{head}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
