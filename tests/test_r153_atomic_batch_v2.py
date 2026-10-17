"""R-153 (D-065): positive/negative tests for the atomic-batch check v2.

Two layers:
1. ``evaluate()`` on file lists — every rule R0..R6 has a passing and a
   failing case, plus parity cases proving v2 never passes something the
   v1 inline rules (governance-gate.yml) reject.
2. End-to-end: the real script on throwaway git repositories, exactly as
   the workflow runs it (exit codes 0/1/2).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_atomic_batch.py"
WORKFLOW = ROOT / ".github" / "workflows" / "governance-gate.yml"
sys.path.insert(0, str(ROOT / "scripts"))
import check_atomic_batch as cab  # noqa: E402

DOCS = ["docs/governance/ROADMAP.md", "docs/governance/PROJECT_STATE.md",
        "docs/governance/CHANGELOG.md"]
EV = "docs/audit/M8/logs/raw/999_x.log"


def rules(changed):
    return [v[:2] for v in cab.evaluate(changed)]


# ---- R0 ------------------------------------------------------------------------

def test_empty_diff_passes():
    assert cab.evaluate([]) == []


def test_docs_only_with_mandatory_passes():
    assert rules(DOCS + ["docs/governance/RISKS.md"]) == []


@pytest.mark.parametrize("drop", DOCS)
def test_missing_any_mandatory_doc_fails(drop):
    changed = [d for d in DOCS if d != drop] + ["docs/governance/RISKS.md"]
    v = cab.evaluate(changed)
    assert [x[:2] for x in v] == ["R0"] and drop in v[0]


# ---- R1 (v1 paths + R-153 additions) -----------------------------------------

@pytest.mark.parametrize("path", [
    # v1 paths
    "server.py", "run.py", "agent_civilization/core/x.txt", "agos_kernel/a.txt",
    "src/routes/index.tsx", "web/index.html",
    # R-153 additions
    "scripts/run_x.py", ".github/dependabot.yml", "Dockerfile", ".dockerignore",
    "render.yaml", "requirements.lock", "requirements-dev.in", "package.json",
    "package-lock.json", "pytest.ini", "vite.config.ts", "tsconfig.json",
    "eslint.config.js", "components.json", "public/favicon.ico", ".env.example",
    "docs/sql/002_x.sql",
])
def test_executable_change_requires_evidence(path):
    # only R1 is asserted; R2/R5/R6 may also fire for some paths. No helper
    # file under tests/ or web/ is added: those are executable themselves and
    # would trigger R1 regardless (caught by mutation M2b, log 026).
    assert "R1" in rules(DOCS + [path])
    assert "R1" not in rules(DOCS + [path, EV])


def test_plain_docs_are_not_executable():
    assert rules(DOCS + ["docs/governance/ARCHITECTURE.md", "README.md"]) == []


# ---- R2/R3/R4 (v1 parity) -----------------------------------------------------

def test_api_rule():
    base = DOCS + [EV, "tests/test_x.py"]
    assert "R2" in rules(base + ["agent_civilization/civilization_routes.py"])
    assert "R2" not in rules(base + ["agent_civilization/civilization_routes.py",
                                     "docs/governance/API.md"])


def test_storage_rule():
    base = DOCS + [EV, "tests/test_x.py"]
    assert "R3" in rules(base + ["agent_civilization/storage/x.py"])
    assert "R3" not in rules(base + ["agent_civilization/storage/x.py",
                                     "docs/governance/DATA_MODEL.md"])


def test_workflow_rule():
    base = DOCS + [EV]
    assert "R4" in rules(base + [".github/workflows/ci.yml"])
    assert "R4" not in rules(base + [".github/workflows/ci.yml", "docs/governance/QUALITY.md"])


# ---- R5/R6 (new) --------------------------------------------------------------

@pytest.mark.parametrize("path", ["server.py", "run.py", "agent_civilization/core/a.py",
                                  "agos_kernel/metrics.py"])
def test_runtime_python_requires_tests(path):
    base = DOCS + [EV, "docs/governance/API.md"]
    assert "R5" in rules(base + [path])
    assert "R5" not in rules(base + [path, "tests/test_a.py"])


def test_non_python_runtime_file_does_not_trigger_r5():
    assert "R5" not in rules(DOCS + [EV, "agent_civilization/data/seed.json"])


@pytest.mark.parametrize("path", ["web/app.js", "web/index.html", "web/src/x.ts"])
def test_web_source_requires_tests(path):
    base = DOCS + [EV]
    assert "R6" in rules(base + [path])
    assert "R6" not in rules(base + [path, "web/tests/api.test.mjs"])
    assert "R6" not in rules(base + [path, "tests/test_web_api_contract.py"])


def test_web_tests_alone_do_not_need_more_tests():
    assert rules(DOCS + [EV, "web/tests/api.test.mjs"]) == []


def test_all_violations_reported_together():
    got = rules(["server.py", "agent_civilization/storage/s.py", ".github/workflows/ci.yml"])
    assert got == ["R0", "R1", "R2", "R3", "R4", "R5"]


# ---- parity: v2 never accepts what v1 rejects ---------------------------------

V1_CASES = [
    ["server.py"],
    DOCS + ["server.py"],
    DOCS + ["server.py", EV],
    DOCS + ["agent_civilization/storage/x.py", EV],
    DOCS + [".github/workflows/x.yml", EV],
    DOCS + ["tests/test_x.py"],
    ["docs/governance/RISKS.md"],
]


def _v1(changed):
    """Independent re-implementation of the inline bash step (v1)."""
    s = set(changed)
    if not changed:
        return True
    if any(d not in s for d in DOCS):
        return False
    if any(re.match(r"^(server\.py|run\.py|agent_civilization/|agos_kernel/|src/|web/|tests/)", p)
           for p in changed) and not any(re.fullmatch(r"docs/audit/.+", p) for p in changed):
        return False
    if any(re.search(r"^server\.py|agent_civilization/civilization_routes\.py", p) for p in changed) \
            and "docs/governance/API.md" not in s:
        return False
    if any("agent_civilization/storage/" in p for p in changed) and "docs/governance/DATA_MODEL.md" not in s:
        return False
    if any(p.startswith(".github/workflows/") for p in changed) and "docs/governance/QUALITY.md" not in s:
        return False
    return True


@pytest.mark.parametrize("changed", V1_CASES)
def test_v2_is_at_least_as_strict_as_v1(changed):
    if not _v1(changed):
        assert cab.evaluate(changed), changed


# ---- end-to-end on real git repositories ------------------------------------

pytest_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for f in DOCS + ["scripts/tool.py", "server.py", "tests/test_s.py"]:
        _write(r, f, "base\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "pr")
    return r


def _run(repo: Path, base: str = "main"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    return subprocess.run([sys.executable, str(SCRIPT), base, "HEAD"], cwd=repo,
                          capture_output=True, text=True)


@pytest_git
def test_e2e_incomplete_batch_fails(repo):
    _write(repo, "scripts/tool.py", "changed\n")
    res = _run(repo)
    assert res.returncode == 1
    assert "::error::R0" in res.stdout and "::error::R1" in res.stdout


@pytest_git
def test_e2e_scripts_change_with_docs_but_no_evidence_fails(repo):
    # passes the v1 inline rules (scripts/ is not in v1 CODE_RE) but not v2
    for d in DOCS:
        _write(repo, d, "updated\n")
    _write(repo, "scripts/tool.py", "changed\n")
    res = _run(repo)
    assert res.returncode == 1 and "::error::R1" in res.stdout and "R0" not in res.stdout


@pytest_git
def test_e2e_complete_batch_passes(repo):
    for d in DOCS + [EV, "docs/governance/API.md"]:
        _write(repo, d, "updated\n")
    _write(repo, "server.py", "changed\n")
    _write(repo, "tests/test_s.py", "changed\n")
    res = _run(repo)
    assert res.returncode == 0, res.stdout
    assert "Atomic batch v2 passed" in res.stdout


@pytest_git
def test_e2e_bad_ref_is_error(repo):
    res = _run(repo, base="no-such-ref")
    assert res.returncode == 2


# ---- contract: workflow runs v2 without removing v1 ---------------------------

def test_workflow_keeps_v1_and_adds_v2():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "- name: Atomic batch check\n" in wf                     # v1 kept
    assert "CODE_RE='^(server\\.py|run\\.py|agent_civilization/|agos_kernel/|src/|web/|tests/)'" in wf
    assert re.search(r"- name: Atomic batch check v2 \(R-153\)\n\s+if: \$\{\{ !cancelled\(\) \}\}\n"
                     r"\s+run: python3 scripts/check_atomic_batch\.py origin/main HEAD", wf)
    assert "continue-on-error" not in wf
