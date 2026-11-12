"""R-203 (D-152): the reviewed deletion mechanism of ``scripts/check_no_delete.py``.

Real throwaway git repositories, the real script as a subprocess (like the
governance workflow). A deletion passes only when the path is in a
``docs/governance/deletions/DEL-NNN.list`` that is already merged (present at
the merge base) and whose decision in DECISIONS.md cites the list.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_no_delete.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

DECISIONS_OK = "# Decisions\n\n### D-900: delete old stuff\n- list: docs/governance/deletions/DEL-001.list\n\n### D-901: other\n- nothing\n"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env=env).stdout


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(repo: Path, msg: str = "change") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "main", "HEAD"], cwd=repo,
                          capture_output=True, text=True)


LIST_OK = "# DEL-001 — test\n# decision: D-900\n# item: R-203\nold/a.py\nold/b.py\nsrc/routes/sitemap[.]xml.ts\nملف قديم.md\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for rel in ("keep.py", "old/a.py", "old/b.py", "old/c.py", "src/routes/sitemap[.]xml.ts", "ملف قديم.md"):
        _w(r, rel, f"# {rel}\n" * 5)
    _w(r, "docs/governance/DECISIONS.md", DECISIONS_OK)
    _commit(r, "base")
    return r


def _merge_list(repo: Path, text: str = LIST_OK, decisions: str = DECISIONS_OK) -> None:
    """Simulate batch 1 already merged on main (the list is at the merge base)."""
    _w(repo, "docs/governance/deletions/DEL-001.list", text)
    _w(repo, "docs/governance/DECISIONS.md", decisions)
    _commit(repo, "batch 1: list + decision")


def _branch(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "pr")


def _rm(repo: Path, *rels: str) -> None:
    for rel in rels:
        (repo / rel).unlink()


# ---- red: what must be rejected ----------------------------------------------

def test_red_unlisted_deletion_rejected_even_with_a_merged_list(repo):
    _merge_list(repo)
    _branch(repo)
    _rm(repo, "old/c.py")  # not in DEL-001
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout
    assert "old/c.py deleted" in res.stdout and "not in a merged deletion list" in res.stdout


def test_red_list_added_in_the_same_pr_authorizes_nothing(repo):
    # the D-064 objection: a PR cannot authorize its own deletions
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-001.list", LIST_OK)
    _rm(repo, "old/a.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout
    assert "old/a.py deleted" in res.stdout
    assert "may not add or change a deletion list in the same diff" in res.stdout


def test_red_editing_a_merged_list_is_rejected(repo):
    _merge_list(repo)
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-001.list", LIST_OK + "old/c.py\n")
    _rm(repo, "old/c.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout
    assert "immutable" in res.stdout
    assert "old/c.py deleted" in res.stdout  # the edit did not authorize it


def test_red_editing_a_merged_list_without_deleting_is_rejected(repo):
    _merge_list(repo)
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-001.list", LIST_OK + "keep.py\n")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1 and "immutable" in res.stdout, res.stdout


def test_red_deleting_a_merged_list_is_rejected(repo):
    _merge_list(repo)
    _branch(repo)
    _rm(repo, "docs/governance/deletions/DEL-001.list")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout


@pytest.mark.parametrize("decisions, needle", [
    ("# Decisions\n\n### D-901: other\n- docs/governance/deletions/DEL-001.list\n", "decision D-900 not found"),
    ("# Decisions\n\n### D-900: delete old stuff\n- no citation here\n\n### D-901: x\n- DEL-001.list\n",
     "does not cite"),
])
def test_red_merged_list_without_a_matching_decision_authorizes_nothing(repo, decisions, needle):
    _merge_list(repo, decisions=decisions)
    _branch(repo)
    _rm(repo, "old/a.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout
    assert "authorizes nothing" in res.stdout and needle in res.stdout
    assert "old/a.py deleted" in res.stdout


@pytest.mark.parametrize("body, needle", [
    ("old/a.py\n", "missing '# decision: D-NNN' header"),
    ("# decision: D-900\nold/\n", "not an exact relative file path"),
    ("# decision: D-900\n../x.py\n", "not an exact relative file path"),
    ("# decision: D-900\n/abs.py\n", "not an exact relative file path"),
    ("# decision: D-900\nold/*.py\n", "not a tracked file"),
    ("# decision: D-900\nold/zzz.py\n", "not a tracked file"),
    ("# decision: D-900\nold/a.py\nold/a.py\n", "duplicate path"),
    ("# decision: D-900\ndocs/governance/DECISIONS.md\n", "cannot be listed"),
    ("# decision: D-900\n", "lists no path"),
])
def test_red_malformed_new_list_cannot_land(repo, body, needle):
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-001.list", body)
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1, res.stdout
    assert needle in res.stdout


def test_red_new_list_whose_decision_does_not_cite_it_cannot_land(repo):
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-002.list", "# decision: D-900\nold/c.py\n")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1 and "does not cite" in res.stdout, res.stdout


def test_red_other_files_in_the_deletions_directory_rejected(repo):
    _branch(repo)
    _w(repo, "docs/governance/deletions/allow.txt", "old/c.py\n")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1 and "only docs/governance/deletions/DEL-NNN.list" in res.stdout


def test_red_rename_of_unlisted_file_rejected(repo):
    _merge_list(repo)
    _branch(repo)
    _git(repo, "mv", "old/c.py", "new_c.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 1 and "renamed/moved to new_c.py" in res.stdout, res.stdout


# ---- green: what must pass ---------------------------------------------------

def test_green_readme_in_the_deletions_directory_is_allowed(repo):
    _branch(repo)
    _w(repo, "docs/governance/deletions/README.md", "# lists\n")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout

def test_green_valid_new_list_without_deletions_passes(repo):
    _branch(repo)
    _w(repo, "docs/governance/deletions/DEL-001.list", LIST_OK)
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout
    assert "0 tracked files deleted" in res.stdout


def test_green_listed_deletions_pass_after_the_list_is_merged(repo):
    _merge_list(repo)
    _branch(repo)
    _rm(repo, "old/a.py", "src/routes/sitemap[.]xml.ts", "ملف قديم.md")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout
    assert "authorized deletions (D-152 mechanism): 3 by list {'DEL-001': 3}" in res.stdout
    assert "3 deletion(s), all in merged deletion lists" in res.stdout


def test_green_listed_move_passes(repo):
    _merge_list(repo)
    _branch(repo)
    (repo / "kept").mkdir()
    _git(repo, "mv", "old/b.py", "kept/b.py")
    _commit(repo)
    res = _run(repo)
    assert res.returncode == 0, res.stdout


def test_green_main_moved_on_after_the_branch_point(repo):
    # merge-base, not the tip of main: a list merged after the branch point
    # does not count until the PR is updated with main
    _branch(repo)
    _rm(repo, "old/a.py")
    _commit(repo)
    _git(repo, "checkout", "-q", "main")
    _merge_list(repo)
    _git(repo, "checkout", "-q", "pr")
    assert _run(repo).returncode == 1
    _git(repo, "merge", "-q", "--no-edit", "main")
    res = _run(repo)
    assert res.returncode == 0, res.stdout


def test_no_environment_or_pr_text_override():
    src = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]
    for needle in ("GITHUB_EVENT_PATH", "PR_BODY", "os.environ", "getenv", "labels"):
        assert needle not in body, needle
