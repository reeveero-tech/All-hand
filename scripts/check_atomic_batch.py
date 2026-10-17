#!/usr/bin/env python3
"""R-153 (D-065): atomic-batch check v2 — testable, stricter superset of the
inline "Atomic batch check" step in .github/workflows/governance-gate.yml.

Usage: python scripts/check_atomic_batch.py [BASE] [HEAD]
       (defaults: origin/main HEAD; diff = ``git diff BASE...HEAD``)

Exit codes: 0 = batch complete; 1 = at least one rule violated (all listed);
            2 = git error (never treated as a pass).

Rules (every violated rule is reported, not only the first):
  R0  any change at all  -> ROADMAP.md, PROJECT_STATE.md, CHANGELOG.md   (= v1)
  R1  executable change  -> evidence file under docs/audit/              (v1 + wider)
  R2  server.py / civilization_routes.py -> docs/governance/API.md       (= v1)
  R3  agent_civilization/storage/        -> docs/governance/DATA_MODEL.md (= v1)
  R4  .github/workflows/                 -> docs/governance/QUALITY.md   (= v1)
  R5  runtime Python change              -> a change under tests/        (new)
  R6  web/ source change                 -> a change under web/tests/ or tests/ (new)

"Executable" (R1) = the v1 paths (server.py, run.py, agent_civilization/,
agos_kernel/, src/, web/, tests/) PLUS scripts/, .github/, Docker/Render
files, dependency manifests and lockfiles, and build/test configuration.
The v1 inline step is kept unchanged in the workflow (no interface is
removed); this script can only add failures, never remove one.
Reads git state only — never PR text, labels or environment overrides.
"""
from __future__ import annotations

import re
import subprocess
import sys

MANDATORY = (
    "docs/governance/ROADMAP.md",
    "docs/governance/PROJECT_STATE.md",
    "docs/governance/CHANGELOG.md",
)
# v1 CODE_RE, verbatim from governance-gate.yml (R-020)
V1_CODE_RE = re.compile(r"^(server\.py|run\.py|agent_civilization/|agos_kernel/|src/|web/|tests/)")
# R-153 additions to "executable"
EXTRA_EXEC_RE = re.compile(
    r"^(scripts/|\.github/|Dockerfile$|\.dockerignore$|render\.yaml$|requirements[^/]*$"
    r"|package(-lock)?\.json$|pytest\.ini$|vite\.config\.ts$|tsconfig\.json$"
    r"|eslint\.config\.js$|components\.json$|public/|\.env\.example$|docs/sql/)"
)
EVIDENCE_RE = re.compile(r"^docs/audit/.+")
API_RE = re.compile(r"^server\.py|agent_civilization/civilization_routes\.py")  # v1 (unanchored 2nd alt kept)
STORAGE_RE = re.compile(r"agent_civilization/storage/")  # v1 (unanchored, kept)
WORKFLOW_RE = re.compile(r"^\.github/workflows/")
RUNTIME_PY_RE = re.compile(r"^(server\.py|run\.py|(agent_civilization|agos_kernel)/.+\.py)$")
WEB_SRC_RE = re.compile(r"^web/(?!tests/).+\.(m?js|jsx|ts|tsx|html|css)$")
TESTS_RE = re.compile(r"^tests/.+")
WEB_TESTS_RE = re.compile(r"^web/tests/.+")


def changed_files(base: str, head: str, cwd: str | None = None) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "-z", f"{base}...{head}"],
        cwd=cwd, check=True, capture_output=True,
    ).stdout.decode("utf-8", "surrogateescape")
    return [p for p in out.split("\0") if p]


def evaluate(changed: list[str]) -> list[str]:
    """Return a list of human-readable violations (empty = pass)."""
    if not changed:
        return []
    s = set(changed)
    any_match = lambda rx: [p for p in changed if rx.search(p)]  # noqa: E731
    v: list[str] = []
    missing = [f for f in MANDATORY if f not in s]
    if missing:
        v.append("R0 mandatory governance docs not updated: " + ", ".join(missing))
    execs = sorted(set(any_match(V1_CODE_RE)) | set(any_match(EXTRA_EXEC_RE)))
    if execs and not any_match(EVIDENCE_RE):
        v.append("R1 executable change without evidence under docs/audit/: "
                 + ", ".join(execs[:10]) + (" ..." if len(execs) > 10 else ""))
    if any_match(API_RE) and "docs/governance/API.md" not in s:
        v.append("R2 API changed without docs/governance/API.md")
    if any_match(STORAGE_RE) and "docs/governance/DATA_MODEL.md" not in s:
        v.append("R3 storage changed without docs/governance/DATA_MODEL.md")
    if any_match(WORKFLOW_RE) and "docs/governance/QUALITY.md" not in s:
        v.append("R4 CI changed without docs/governance/QUALITY.md")
    rt = any_match(RUNTIME_PY_RE)
    if rt and not any_match(TESTS_RE):
        v.append("R5 runtime Python changed without any change under tests/: "
                 + ", ".join(rt[:10]))
    web = any_match(WEB_SRC_RE)
    if web and not (any_match(WEB_TESTS_RE) or any_match(TESTS_RE)):
        v.append("R6 web/ source changed without any change under web/tests/ or tests/: "
                 + ", ".join(web[:10]))
    return v


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "origin/main"
    head = argv[2] if len(argv) > 2 else "HEAD"
    try:
        changed = changed_files(base, head)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        err = getattr(exc, "stderr", b"") or b""
        print(f"::error::atomic-batch check could not read git diff {base}...{head}: "
              f"{err.decode(errors='replace').strip() or exc}")
        return 2
    print(f"atomic-batch v2: {len(changed)} changed file(s) in {base}...{head}")
    violations = evaluate(changed)
    if not violations:
        print("Atomic batch v2 passed (R0-R6).")
        return 0
    for msg in violations:
        print(f"::error::{msg}")
    print(f"::error::Atomic batch v2 failed: {len(violations)} rule(s) violated "
          "(D-065). Add the missing docs/evidence/tests to the SAME PR.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
