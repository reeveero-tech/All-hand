"""Conflict-marker gate (log 295): real throwaway git repos, the real script
run as a subprocess (as the governance workflow does)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_conflict_markers.py"
WORKFLOW = ROOT / ".github" / "workflows" / "governance-gate.yml"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env=env).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "ROADMAP.md").write_text("# t\n\n> header 1\n", encoding="utf-8")
    (r / "old.log").write_text("<<<<<<< HEAD\nhistorical\n>>>>>>> x\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "pr")
    return r


def _commit_run(repo: Path) -> subprocess.CompletedProcess:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "change")
    return subprocess.run([sys.executable, str(SCRIPT), "main", "HEAD"], cwd=repo,
                          capture_output=True, text=True)


def test_clean_change_passes(repo):
    (repo / "ROADMAP.md").write_text("# t\n\n> header 2\n> **سابقاً**: header 1\n", encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 0, r.stdout


def test_exact_incident_shape_fails(repo):
    (repo / "ROADMAP.md").write_text(
        "# t\n\n<<<<<<< HEAD\n> لاحق 38\n=======\n> لاحق 39\n>>>>>>> 4cf497fb (Local load)\n> header 1\n",
        encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 1
    assert "ROADMAP.md:3: <<<<<<< HEAD" in r.stdout
    assert "ROADMAP.md:5: =======" in r.stdout
    assert "ROADMAP.md:7: >>>>>>> 4cf497fb" in r.stdout


@pytest.mark.parametrize("line", ["<<<<<<< HEAD", ">>>>>>> abc", "||||||| base", "<<<<<<<", ">>>>>>>"])
def test_each_marker_kind_fails(repo, line):
    (repo / "new.md").write_text(f"a\n{line}\nb\n", encoding="utf-8")
    assert _commit_run(repo).returncode == 1


@pytest.mark.parametrize("line", ["=======", "======= 9 failed, 187 passed =======",
                                  "<<<<<<<< eight", "x <<<<<<< HEAD", "<<<<<<<HEAD"])
def test_non_markers_pass(repo, line):
    (repo / "new.log").write_text(f"a\n{line}\nb\n", encoding="utf-8")
    assert _commit_run(repo).returncode == 0


def test_bare_separator_in_other_file_not_reported(repo):
    (repo / "new.md").write_text("<<<<<<< HEAD\n", encoding="utf-8")
    (repo / "pytest.log").write_text("x\n=======\n", encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 1 and "FAILED: 1 marker line(s)" in r.stdout
    assert "new.md:1: <<<<<<< HEAD" in r.stdout and "=======" not in r.stdout and "pytest.log" not in r.stdout


def test_preexisting_markers_not_rejudged(repo):
    (repo / "old.log").write_text("<<<<<<< HEAD\nhistorical\n>>>>>>> x\nappended\n", encoding="utf-8")
    assert _commit_run(repo).returncode == 0


def test_arabic_path_and_crlf(repo):
    (repo / "ملف.md").write_bytes("س\r\n<<<<<<< HEAD\r\nص\r\n".encode("utf-8"))
    r = _commit_run(repo)
    assert r.returncode == 1 and "ملف.md" in r.stdout


def test_quoted_path_with_quote_char(repo):
    (repo / 'we"ird name.md').write_text("<<<<<<< HEAD\n", encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 1 and 'we"ird name.md' in r.stdout


def test_quoted_path_with_quotepath_true(repo):
    # even if a caller's git config forces quoting, the script unquotes
    _git(repo, "config", "core.quotePath", "true")
    (repo / "تقرير.md").write_text("x\n>>>>>>> abc\n", encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 1 and "تقرير.md" in r.stdout


def test_path_with_space_reported_exactly(repo):
    (repo / "a b.md").write_text("<<<<<<< HEAD\n", encoding="utf-8")
    r = _commit_run(repo)
    assert r.returncode == 1 and "  a b.md:1: <<<<<<< HEAD" in r.stdout


def test_bad_ref_is_error_not_pass(repo):
    r = subprocess.run([sys.executable, str(SCRIPT), "no-such-ref", "HEAD"], cwd=repo,
                       capture_output=True, text=True)
    assert r.returncode == 2


def test_workflow_runs_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python3 scripts/check_conflict_markers.py origin/main HEAD" in text
