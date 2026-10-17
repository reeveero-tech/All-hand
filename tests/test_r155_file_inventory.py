"""R-155: tests for scripts/build_file_inventory.py on REAL temporary git repos.

Each test builds a small repository with a snapshot commit, then checks that
the inventory has exactly one row per path of ``git ls-files`` at that SHA,
that git facts (blob, size, last change) are exact, that roles follow the
evidence rules, and that the self-check rejects DELETE decisions and
missing/extra rows.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_file_inventory.py"
TEMPLATE = ROOT / "docs/governance/roadmaps/revival-scale/templates/file_inventory_template.csv"
sys.path.insert(0, str(ROOT / "scripts"))
import build_file_inventory as bfi  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str, date: str = "2026-01-02T03:04:05+03:00") -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout.strip()


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


FILES = {
    "Dockerfile": 'FROM python:3.12\nCMD ["sh", "-c", "uvicorn app:app"]\n',
    "app.py": '"""The API app."""\nfrom pkg import core\n\n\ndef handler():\n    return core.run()\n',
    "pkg/__init__.py": "",
    "pkg/core.py": '"""Core logic."""\nimport importlib\nimport requests\n\n\ndef run():\n'
                   '    return [importlib.import_module(f"pkg.plugins.{n}") for n in ("a",)]\n',
    "pkg/plugins/__init__.py": "",
    "pkg/plugins/a.py": '"""Plugin A (loaded dynamically)."""\nX = 1\n',
    "pkg/orphan.py": '"""Nobody imports me."""\n\n\ndef lonely():\n    return 1\n',
    "pkg/only_tests.py": '"""Imported only by a test."""\nY = 2\n',
    "pkg/shim_0001.py": '"""Shim0001 — thin compatibility shim (D-049).\n\nre-exports."""\nfrom pkg.core import run  # noqa\n',
    "tests/test_core.py": "from pkg import core\nfrom pkg import only_tests\n\n\ndef test_x():\n    assert core\n",
    "tests/test_mentions.py": "# mentions pkg/orphan.py in fixture text only\nPATH = 'pkg/orphan.py'\n",
    "docs/README.md": "# Docs title\ntext\n",
    "docs/audit/logs/001_run.log": "# 001 run log\nok\n",
    "dir with space/ملف عربي.txt": "arabic name\n",
    "scripts/tool.py": '"""A CLI tool."""\nif __name__ == "__main__":\n    print(1)\n',
    "docs/governance/OPEN_QUESTIONS.md": "# Q\n\n### Q-002: fate of src/\n- open\n",
    "src/app.tsx": "import x from './lib';\n",
    "src/lib.ts": "export default 1;\n",
}


@pytest.fixture()
def repo(tmp_path: Path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for k, v in FILES.items():
        _w(r, k, v)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "snapshot base")
    # a later change to one file (last-change must point at this commit)
    _w(r, "pkg/orphan.py", FILES["pkg/orphan.py"] + "# touched\n")
    _git(r, "commit", "-qam", "touch orphan", date="2026-02-03T00:00:00+00:00")
    snap = _git(r, "rev-parse", "HEAD")
    # AFTER the snapshot: new file + working-tree edits must NOT appear
    _w(r, "added_later.py", "z = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "after snapshot")
    _w(r, "app.py", "# dirty working tree\n")
    _w(r, "untracked.py", "u = 1\n")
    return r, snap


def _rows(repo, snap, **kw):
    rows, summary = bfi.build(str(repo), snap, **kw)
    return {r["path"]: r for r in rows}, rows, summary


def test_one_row_per_tracked_path_at_snapshot_exact_paths(repo):
    r, snap = repo
    by, rows, summary = _rows(r, snap)
    ls = _git(r, "ls-tree", "-r", "--name-only", "--full-tree", "-z", snap).split("\0")
    assert sorted(by) == sorted(x for x in ls if x)
    assert len(rows) == summary["total_files"] == len(FILES)
    assert "dir with space/ملف عربي.txt" in by                 # exact path, spaces + Arabic
    assert "added_later.py" not in by and "untracked.py" not in by
    assert bfi.self_check(str(r), snap, rows) == []


def test_git_facts_blob_size_last_change(repo):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    for p in ("app.py", "dir with space/ملف عربي.txt"):
        assert by[p]["git_blob_sha"] == _git(r, "rev-parse", f"{snap}:{p}")
        assert by[p]["size_bytes"] == _git(r, "cat-file", "-s", f"{snap}:{p}")
    assert by["pkg/orphan.py"]["last_modified_utc"].startswith("2026-02-03T00:00:00Z")
    assert by["app.py"]["last_modified_utc"].startswith("2026-01-02T00:04:05Z")    # converted to UTC


def test_reads_snapshot_not_working_tree(repo):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    assert by["app.py"]["purpose"] == "The API app."           # working tree says "# dirty working tree"


def test_entrypoint_import_graph_and_dynamic_loading(repo):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    assert "deploy:Dockerfile (uvicorn app:app)" in by["app.py"]["entrypoint_or_imported_by"]
    assert by["app.py"]["inventory_status"] == "active"
    assert by["pkg/core.py"]["inventory_status"] == "active"
    assert "third-party: requests" in by["pkg/core.py"]["dependencies"]
    a = by["pkg/plugins/a.py"]
    assert a["inventory_status"] == "active"                     # only reachable via f-string import_module
    assert 'importlib.import_module(f"pkg.plugins.{…}") in pkg/core.py' in a["dynamic_registration_or_reference"]
    assert "public: handler" in by["app.py"]["public_contract"]


def test_absent_import_is_not_proof_of_non_use(repo):
    r, snap = repo
    by, _, summary = _rows(r, snap)
    o = by["pkg/orphan.py"]
    assert o["inventory_status"] == "unresolved" and o["decision_no_delete"] != "DELETE"
    assert "role unresolved" in o["gaps"] and o["next_action"] and o["confidence"] == "low"
    ot = by["pkg/only_tests.py"]
    assert ot["inventory_status"] == "unresolved" and "imported only by tests: tests/test_core.py" in ot["gaps"]
    assert {u["path"] for u in summary["unresolved"]} >= {"pkg/orphan.py", "pkg/only_tests.py"}


def test_path_mention_in_test_is_not_a_direct_test(repo):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    ct = by["pkg/orphan.py"]["current_tests"]
    assert ct.startswith("no direct test import") and "not proof of coverage" in ct
    assert by["pkg/core.py"]["current_tests"].startswith("direct 1: tests/test_core.py")


def test_categories_by_kind(repo):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    assert by["tests/test_core.py"]["inventory_status"] == "test-only"
    assert by["docs/audit/logs/001_run.log"]["inventory_status"] == "generated"
    assert by["docs/README.md"]["inventory_status"] == "docs-only"
    assert by["docs/README.md"]["purpose"] == "Docs title"
    s = by["pkg/shim_0001.py"]
    assert s["inventory_status"] == "legacy-compatible" and s["decision_no_delete"] == "ADAPTER"
    assert s["risk_level"] == "low" and "risk carried by the specialization base class" in s["gaps"]
    assert "shared" == by["scripts/tool.py"]["inventory_status"]            # CLI, not deployed
    assert "cli: if __name__" in by["scripts/tool.py"]["entrypoint_or_imported_by"]
    assert by["Dockerfile"]["risk_level"] == "high"


def test_open_owner_question_keeps_role_unresolved(repo, tmp_path):
    r, snap = repo
    by, _, _ = _rows(r, snap)
    assert by["src/app.tsx"]["inventory_status"] == "unresolved" and "Q-002" in by["src/app.tsx"]["gaps"]
    assert "src/lib.ts" in by["src/app.tsx"]["dependencies"]                # JS graph resolved
    # once Q-002 is closed the open-question rule no longer applies
    _w(r, "docs/governance/OPEN_QUESTIONS.md", "# Q\n\n### Q-002: fate of src/ — **مُغلَق**\n")
    _git(r, "commit", "-qam", "close Q-002")
    by2, _, _ = _rows(r, _git(r, "rev-parse", "HEAD"))
    assert "Q-002" not in by2["src/app.tsx"]["gaps"]


def test_no_delete_decision_ever_and_allowed_vocabularies(repo):
    r, snap = repo
    _, rows, _ = _rows(r, snap)
    assert {x["decision_no_delete"] for x in rows} <= set(bfi.DECISIONS)
    assert "DELETE" not in bfi.DECISIONS
    assert {x["inventory_status"] for x in rows} <= set(bfi.STATUSES)


@pytest.mark.parametrize("mutate,msg", [
    (lambda rows: rows.pop(), "row count"),
    (lambda rows: rows.append(dict(rows[0])), "row count"),
    (lambda rows: rows[0].__setitem__("decision_no_delete", "DELETE"), "not allowed (no DELETE)"),
    (lambda rows: rows[0].__setitem__("inventory_status", "dead"), "status 'dead' not allowed"),
    (lambda rows: rows[0].__setitem__("path", "renamed.py"), "path set differs"),
])
def test_self_check_rejects_broken_inventory(repo, mutate, msg):
    r, snap = repo
    _, rows, _ = _rows(r, snap)
    mutate(rows)
    assert any(msg in e for e in bfi.self_check(str(r), snap, rows))


def test_self_check_rejects_unresolved_without_reason(repo):
    r, snap = repo
    _, rows, _ = _rows(r, snap)
    row = next(x for x in rows if x["inventory_status"] == "unresolved")
    row["next_action"] = ""
    assert any("unresolved without reason/next action" in e for e in bfi.self_check(str(r), snap, rows))


def test_cli_deterministic_csv_matches_template_and_summary(repo, tmp_path):
    r, snap = repo
    outs = []
    for i in (1, 2):
        (tmp_path / f"run{i}").mkdir()
        out, md = tmp_path / f"run{i}" / "inv.csv", tmp_path / f"run{i}" / "sum.md"
        res = subprocess.run([sys.executable, str(SCRIPT), str(r), snap, str(out), "--summary-md", str(md),
                              "--review-date", "2026-10-17"], capture_output=True, text=True)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "self_check=PASS" in res.stdout
        outs.append((out.read_bytes(), md.read_text(encoding="utf-8")))
    assert outs[0] == outs[1]                                                 # byte-identical reruns
    header = outs[0][0].decode().splitlines()[0]
    assert header == TEMPLATE.read_text(encoding="utf-8").splitlines()[0] == ",".join(bfi.COLUMNS)
    with open(tmp_path / "run1" / "inv.csv", encoding="utf-8", newline="") as f:
        assert len(list(csv.DictReader(f))) == len(FILES)
    md = outs[0][1]
    assert "`pkg/orphan.py`" in md and "`pkg/only_tests.py`" in md and "لا يُعلن فهم كامل للمستودع" in md


def test_cli_bad_sha_is_error(repo, tmp_path):
    r, _ = repo
    res = subprocess.run([sys.executable, str(SCRIPT), str(r), "deadbeef", str(tmp_path / "x.csv")],
                         capture_output=True, text=True)
    assert res.returncode == 2 and "::error::inventory" in res.stdout
