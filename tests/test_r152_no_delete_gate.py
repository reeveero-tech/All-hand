"""R-152 (D-063/D-064): positive and negative tests for the no-delete gate.

Each test builds a real throwaway git repository (no mocks of git), creates
a base commit, applies a change on a branch and runs the actual script
``scripts/check_no_delete.py`` as a subprocess — exactly as the governance
workflow does — asserting on its exit code and messages.
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
SCRIPT = ROOT / "scripts" / "check_no_delete.py"
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
    (r / "keep.py").write_text("x = 1\n" * 20)
    (r / "pkg").mkdir()
    (r / "pkg" / "a.py").write_text("def a():\n    return 'a'\n" * 10)
    (r / "pkg" / "b.py").write_text("def b():\n    return 'b'\n" * 10)
    (r / "ملف عربي.md").write_text("محتوى\n" * 10, encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "pr")
    return r


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "change")


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "main", "HEAD"], cwd=repo,
                          capture_output=True, text=True)


# ---- positive cases: the gate must pass --------------------------------------

def test_empty_diff_passes(repo):
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout
    assert "0 tracked files deleted" in res.stdout


def test_add_and_modify_pass(repo):
    (repo / "keep.py").write_text("x = 2\n")
    (repo / "pkg" / "new.py").write_text("n = 1\n")
    (repo / "ملف جديد.md").write_text("جديد\n", encoding="utf-8")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout
    assert "'A': 2" in res.stdout and "'M': 1" in res.stdout


def test_copy_keeping_original_passes(repo):
    shutil.copy(repo / "pkg" / "a.py", repo / "pkg" / "a_copy.py")
    _commit(repo)
    assert _run(repo).returncode == 0


# ---- negative cases: the gate must fail --------------------------------------

def test_delete_file_fails(repo):
    (repo / "keep.py").unlink()
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "::error file=keep.py::deleted" in res.stdout


def test_rename_fails_and_is_labelled(repo):
    _git(repo, "mv", "pkg/a.py", "pkg/a_renamed.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "::error file=pkg/a.py::renamed/moved to pkg/a_renamed.py" in res.stdout


def test_move_to_other_directory_fails(repo):
    (repo / "other").mkdir()
    _git(repo, "mv", "pkg/b.py", "other/b.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "pkg/b.py" in res.stdout and "other/b.py" in res.stdout


def test_rename_with_heavy_edit_still_fails(repo):
    # Below git's rename-similarity threshold a rename is a plain D + A;
    # --no-renames detection must still catch the deleted old path.
    _git(repo, "mv", "pkg/a.py", "pkg/z.py")
    (repo / "pkg" / "z.py").write_text("completely different\n")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "::error file=pkg/a.py::" in res.stdout


def test_delete_directory_lists_every_file(repo):
    shutil.rmtree(repo / "pkg")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "pkg/a.py" in res.stdout and "pkg/b.py" in res.stdout
    assert "2 tracked file(s)" in res.stdout


def test_non_ascii_path_deletion_reported_exactly(repo):
    (repo / "ملف عربي.md").unlink()
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1
    assert "ملف عربي.md" in res.stdout


def test_delete_then_restore_in_later_commit_passes(repo):
    # The gate checks the PR's net diff (base...head), like the workflow.
    (repo / "keep.py").unlink()
    _commit(repo)
    _git(repo, "checkout", "main", "--", "keep.py")
    _commit(repo)
    assert _run(repo).returncode == 0


def test_bad_ref_is_an_error_not_a_pass(repo):
    res = subprocess.run([sys.executable, str(SCRIPT), "no-such-ref", "HEAD"], cwd=repo,
                         capture_output=True, text=True)
    assert res.returncode == 2
    assert "could not read git diff" in res.stdout


def test_no_in_repo_override_exists():
    # D-064: no allowlist file / label / PR-body keyword can bypass the gate.
    src = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    for needle in ("GITHUB_EVENT_PATH", "allowlist.json", "PR_BODY", "labels", "os.environ"):
        assert needle not in code.split('"""', 2)[-1], needle


# ---- contract: the governance workflow really runs the script ----------------

def test_governance_workflow_invokes_no_delete_check():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"python3? scripts/check_no_delete\.py origin/main HEAD", wf)
    # it must run in the required `governance` job, after a full-history checkout
    job = wf.split("jobs:", 1)[1]
    assert "governance:" in job and "fetch-depth: 0" in job
    # and must not be neutralised
    step = wf.split("check_no_delete.py", 1)[0].rsplit("- name:", 1)[1]
    assert "continue-on-error" not in step
    assert "|| true" not in wf.split("check_no_delete.py", 1)[1].splitlines()[0]
