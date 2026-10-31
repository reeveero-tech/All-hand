#!/usr/bin/env python3
"""R-160 (G-159-1): find logging/print calls that emit a caught exception's raw text.

A call is flagged when, inside ``except ... as <name>:``, it passes ``<name>``
itself, ``str(<name>)``/``repr(<name>)``, an f-string interpolating it, or uses
``exc_info``/``logger.exception`` (which prints the message and traceback).
``type(<name>)`` / ``<name>.__class__`` is the safe form (the exception TYPE).

Reviewed exceptions (safe by construction) live in ``REVIEWED`` with the reason;
an entry that no longer matches any flagged call is reported as stale, so the
list cannot rot silently.

Usage: python3 scripts/scan_raw_exception_logs.py [paths...]   (rc=1 on findings)
"""
from __future__ import annotations

import ast
import pathlib
import sys

DEFAULT_PATHS = ("agent_civilization", "agos_kernel", "server.py", "run.py")
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

# (path, substring of the flagged source line) -> why it is safe
REVIEWED = {
    ("agent_civilization/core/tool_runtime.py", 'getattr(e, "reason", "unknown")'):
        "RegistryStoreError.reason is one of a closed set of fixed strings set in "
        "tool_registry_store.py (cannot open/cannot lock/read failed/write failed/"
        "integrity) — never exception text or a path (R-171 batch 3, D-113)",
    ("server.py", "e.reason, e.retry_after"):
        "sse.StreamLimit.reason is one of a closed set of fixed strings set in sse.py "
        "(never exception text); retry_after is an int",
    ("server.py", "type(e).__name__, e.name"):
        "ImportError.name is the module name of a first-party import, not data",
    ("agent_civilization/agents/external_adapter.py", 'getattr(e, "lineno", "?")'):
        "JSON decode position (ints) only — the message and the env value are not logged",
}


def _uses_raw(arg: ast.AST, names: set[str]) -> bool:
    bad = []

    class V(ast.NodeVisitor):
        def visit_Call(self, c):  # type(e) / e.__class__ are the safe forms
            if (isinstance(c.func, ast.Name) and c.func.id == "type" and len(c.args) == 1
                    and isinstance(c.args[0], ast.Name) and c.args[0].id in names):
                return
            self.generic_visit(c)

        def visit_Attribute(self, a):
            if isinstance(a.value, ast.Name) and a.value.id in names and a.attr == "__class__":
                return
            self.generic_visit(a)

        def visit_Name(self, n):
            if n.id in names:
                bad.append(n)

    V().visit(arg)
    return bool(bad)


def _is_log_call(n: ast.Call) -> bool:
    if isinstance(n.func, ast.Attribute) and n.func.attr in LOG_METHODS:
        return True
    return isinstance(n.func, ast.Name) and n.func.id == "print"


def scan_file(path: pathlib.Path, rel: str) -> list[tuple[str, int, str]]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    for h in ast.walk(ast.parse(src)):
        if not (isinstance(h, ast.ExceptHandler) and h.name):
            continue
        names = {h.name}
        for n in ast.walk(h):
            if not (isinstance(n, ast.Call) and _is_log_call(n)):
                continue
            args = list(n.args) + [k.value for k in n.keywords if k.arg != "exc_info"]
            raw = any(_uses_raw(a, names) for a in args)
            tb = (isinstance(n.func, ast.Attribute) and n.func.attr == "exception") or any(
                k.arg == "exc_info" and not (isinstance(k.value, ast.Constant) and k.value.value in (False, None))
                for k in n.keywords)
            if raw or tb:
                seg = "\n".join(lines[n.lineno - 1:(n.end_lineno or n.lineno)])
                out.append((rel, n.lineno, seg.strip()))
    return out


def scan(paths=DEFAULT_PATHS, root: pathlib.Path | None = None):
    root = root or pathlib.Path(__file__).resolve().parent.parent
    found = []
    for p in paths:
        base = root / p
        files = sorted(base.rglob("*.py")) if base.is_dir() else [base]
        for f in files:
            found.extend(scan_file(f, f.relative_to(root).as_posix()))
    unreviewed, used = [], set()
    for rel, line, seg in found:
        key = next((k for k in REVIEWED if k[0] == rel and k[1] in seg), None)
        if key:
            used.add(key)
        else:
            unreviewed.append((rel, line, seg))
    stale = sorted(set(REVIEWED) - used)
    return found, unreviewed, stale


def main(argv):
    found, unreviewed, stale = scan(tuple(argv) or DEFAULT_PATHS)
    print(f"flagged={len(found)} reviewed={len(found) - len(unreviewed)} unreviewed={len(unreviewed)} stale_reviewed={len(stale)}")
    for rel, line, seg in unreviewed:
        print(f"UNREVIEWED {rel}:{line}: {seg.splitlines()[0]}")
    for k in stale:
        print(f"STALE REVIEWED ENTRY {k}")
    return 1 if unreviewed or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
