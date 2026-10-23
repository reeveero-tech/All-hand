"""R-158 (D-070): tests of the test-baseline generator and of its committed outputs.

The full baseline is NOT re-run here (10+ minutes, needs PostgreSQL/PostgREST
and Node): these tests prove the parsers, the file-to-group assignment, the
fingerprint rule and that the committed human report / CSVs are exactly what
the generator renders from the committed JSON.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_test_baseline", ROOT / "scripts/build_test_baseline.py")
btb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(btb)

BASE_SHA = "573793c1f36afd77f3b79247ab9e2d978a397b62"
OUT = ROOT / "docs/governance/roadmaps/revival-scale/baseline"
JSON_PATH = OUT / "TEST_BASELINE_573793c.json"
README = OUT / "README.md"


@pytest.fixture(scope="module")
def baseline():
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


# ------------------------------------------------------------------ parsers --
@pytest.mark.parametrize("line,expected", [
    ("==== 1056 passed, 4 skipped, 4 deselected in 132.72s (0:02:12) ====",
     {"passed": 1056, "skipped": 4, "deselected": 4}),
    ("===== 3 failed, 1053 passed, 4 skipped, 4 deselected in 149.15s (0:02:29) ======",
     {"failed": 3, "passed": 1053, "skipped": 4, "deselected": 4}),
    ("== 2 passed, 1 xfailed, 1 xpassed, 3 warnings in 0.5s ==", {"passed": 2, "xfailed": 1, "xpassed": 1}),
    ("== 1 error in 0.20s ==", {"errors": 1}),
    ("== 5 errors, 1 failed in 1.0s ==", {"errors": 5, "failed": 1}),
])
def test_pytest_summary_parser(line, expected):
    got = btb.parse_pytest_summary("noise\n4 passed earlier text\n" + line + "\n")
    want = {k: 0 for k in btb.COUNT_KEYS}
    want.update(expected)
    assert got == want


def test_pytest_summary_without_summary_line_is_all_zero():
    assert btb.parse_pytest_summary("ImportError while loading conftest\n") == {k: 0 for k in btb.COUNT_KEYS}


def test_pytest_failure_ids():
    out = "FAILED tests/a.py::t1 - AssertionError\nERROR tests/b.py::t2\nFAILED tests/a.py::t1\n"
    assert btb.parse_pytest_failures(out) == ["tests/a.py::t1", "tests/b.py::t2"]


def test_junit_per_file_separates_xfail_skip_failure():
    xml = """<testsuites><testsuite>
      <testcase classname="tests.test_a" name="ok"/>
      <testcase classname="tests.test_a.TestK" name="bad"><failure message="x"/></testcase>
      <testcase classname="tests.test_a" name="sk"><skipped type="pytest.skip" message="s"/></testcase>
      <testcase classname="tests.test_b" name="xf"><skipped type="pytest.xfail" message="x"/></testcase>
      <testcase classname="tests.test_b" name="er"><error message="e"/></testcase>
    </testsuite></testsuites>"""
    per = btb.parse_junit_per_file(xml)
    assert per["tests/test_a.py"] == {"passed": 1, "failed": 1, "skipped": 1, "xfailed": 0, "errors": 0}
    assert per["tests/test_b.py"] == {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 1, "errors": 1}


def test_tap_parser():
    tap = "TAP version 13\n# tests 31\n# pass 30\n# fail 1\n# cancelled 0\n# skipped 0\n# todo 0\n"
    c = btb.parse_tap(tap)
    assert (c["passed"], c["failed"], c["skipped"], c["xfailed"]) == (30, 1, 0, 0)


def test_markdown_link_checker_detects_broken_and_ignores_urls_anchors():
    tracked = {"docs/a.md", "docs/b.md", "server.py"}
    dirs = {"docs", ""}
    text = ("[ok](b.md) [up](../server.py) [dir](../docs/) [url](https://x.y/z) [anchor](#h) "
            "[frag](b.md#sec) [bad](missing.md) [bad2](../nope/x.py)")
    assert btb.md_broken_links("docs/a.md", text, tracked, dirs) == ["missing.md", "../nope/x.py"]


def test_schema_subset_check():
    schema = {"required": ["tool_id", "version"], "properties": {"tool_id": {"type": "string"},
                                                               "version": {"type": "string"}}}
    assert btb.check_schema_subset(schema, {"tool_id": "a", "version": "1"}) == []
    errs = btb.check_schema_subset(schema, {"tool_id": 5})
    assert "missing required key 'version'" in errs and "'tool_id' is not string" in errs


# ------------------------------------------------------------- assignment --
@pytest.mark.parametrize("path,group", [
    ("tests/test_api.py", "tests-python"),
    ("agent_civilization/agents/specialists/analyst/analyst_0015.py", "py-specialists"),
    ("agent_civilization/core/experience.py", "py-runtime"),
    ("server.py", "py-runtime"),
    ("docs/sql/001_init.sql", "sql-storage"),
    ("scripts/r042/kong_prefix_proxy.py", "sql-storage"),
    ("scripts/check_no_delete.py", "py-scripts"),
    ("docs/audit/M3/logs/gate_test_harness.sh", "shell"),
    ("web/src/api.js", "frontend-web"),
    ("src/lib/api.ts", "frontend-root"),
    ("package-lock.json", "frontend-root"),
    ("Dockerfile", "deploy-ci"),
    (".github/workflows/ci.yml", "deploy-ci"),
    ("requirements.lock", "deps-config"),
    ("docs/governance/roadmaps/revival-scale/templates/test_evidence_template.csv", "governance-data"),
    ("docs/audit/M6/scripts/r105_ast_compare.py", "evidence-scripts"),
    ("docs/audit/M8/logs/raw/087_r158_base_ci_run_573793c.json", "evidence-data"),
    ("docs/audit/M8/prep/pr219_v2/check_A.out", "evidence-data"),
    ("docs/audit/M8/logs/raw/069_r156_pytest.log", "evidence-logs"),
    ("docs/audit/M7/logs/raw/247_vis_chat_desktop_1280px.png", "binary-assets"),
    ("README.md", "docs"),
])
def test_assignment_examples(path, group):
    assert btb.assign(path) == group


def test_unknown_file_kind_is_unassigned_not_dropped():
    assert btb.assign("somewhere/new_kind.xyz") is None


def test_every_tracked_file_at_head_is_assigned():
    """New files of later batches must land in a group (or the generator fails its self-check)."""
    tracked = [p for p in _git("ls-files", "-z").split("\0") if p]
    missing = [p for p in tracked if btb.assign(p) is None]
    assert missing == []


def test_test_category_table_names_exist():
    present = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    listed = [n for names in btb.TEST_CATEGORY.values() for n in names]
    assert len(listed) == len(set(listed))
    assert set(listed) <= present


# --------------------------------------------------------- committed outputs --
def _has_commit(sha):
    return subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def test_baseline_is_for_the_base_sha_and_covers_every_file_exactly_once(baseline):
    """CI checks out with depth 1, so the snapshot commit may be absent (096).
    Then the strongest history-free check runs: the snapshot is a sorted,
    duplicate-free subset of the files tracked at HEAD (no tracked file is
    ever deleted/moved/renamed — R-152 gate). With the commit present (full
    clone, local runs), the file list must equal `git ls-tree` at the SHA."""
    assert baseline["schema"] == btb.SCHEMA and baseline["snapshot_sha"] == BASE_SHA
    paths = [f["path"] for f in baseline["files"]]
    assert paths == sorted(set(paths)) and baseline["tracked_files"] == len(paths)
    head = {p for p in _git("ls-files", "-z").split("\0") if p}
    assert set(paths) <= head
    if _has_commit(BASE_SHA):
        at_sha = sorted(p for p in _git("ls-tree", "-r", "-z", "--name-only", BASE_SHA).split("\0") if p)
        assert paths == at_sha
    assert baseline["self_check"] == {"problems": [], "result": "PASS"}
    assert all(f["status"] in ("pass", "fail", "integrity-only") for f in baseline["files"])
    assert sum(baseline["files_summary"].values()) == len(paths)


def test_fingerprint_recomputes_and_ignores_only_per_run_fields(baseline):
    assert btb.fingerprint(baseline) == baseline["fingerprint"]
    other = copy.deepcopy(baseline)
    other["raw_log"], other["checkout"] = "999_other.log", "fresh git worktree"
    for g in other["groups"]:
        for s in g["steps"]:
            if s["evidence_kind"] == "local-live-internet":
                s["counts"]["failed"] += 7
    assert btb.fingerprint(other) == baseline["fingerprint"]
    changed = copy.deepcopy(baseline)
    changed["groups"][0]["steps"][0]["counts"]["passed"] += 1
    assert btb.fingerprint(changed) != baseline["fingerprint"]


def test_human_report_and_csvs_are_rendered_from_the_json(baseline):
    assert (OUT / "TEST_BASELINE_573793c.md").read_text(encoding="utf-8") == btb.render_md(
        baseline, "TEST_BASELINE_573793c.json")
    assert (OUT / "TEST_BASELINE_GROUPS_573793c.csv").read_text(encoding="utf-8") == btb.groups_csv(baseline)
    assert (OUT / "TEST_BASELINE_FILES_573793c.csv").read_text(encoding="utf-8") == btb.files_csv(baseline["files"])


def test_groups_csv_keeps_the_template_columns_first(baseline):
    header = next(csv.reader(io.StringIO(btb.groups_csv(baseline))))
    template = (ROOT / "docs/governance/roadmaps/revival-scale/templates/test_evidence_template.csv").read_text(
        encoding="utf-8").splitlines()[0].split(",")
    assert header[:len(template)] == template


def test_known_base_results_are_recorded_not_hidden(baseline):
    steps = {(g["id"], s["raw_section"]): s for g in baseline["groups"] for s in g["steps"]}
    real = steps[("tests-python", "pytest:real")]
    assert real["exit_code"] == 0 and real["counts"]["passed"] == 1056 and real["counts"]["failed"] == 0
    assert (real["counts"]["skipped"], real["counts"]["deselected"]) == (4, 4)
    spc = steps[("tests-python", "pytest:specialists")]
    assert spc["exit_code"] == 1 and spc["counts"]["failed"] == 3 and spc["counts"]["passed"] == 1053
    assert all(f.startswith("tests/test_r149_security_qa_gaps.py::") for f in spc["failures"])
    assert steps[("sql-storage", "storage:stack")]["counts"]["passed"] == 4
    assert steps[("frontend-root", "frontend-root:tsc")]["exit_code"] != 0      # recorded gap G-158-2
    # no step's failures were turned into skips: skip counts only where pytest skips
    assert sum(s["counts"]["xfailed"] for s in steps.values()) == 0
    assert baseline["ci"]["head_sha"] == BASE_SHA and baseline["ci"]["conclusion"] == "success"


def test_batch_files_are_listed_outside_the_snapshot(baseline):
    text = README.read_text(encoding="utf-8")
    listed = [ln.strip().strip("`- ") for ln in text.split("<!-- batch-files -->")[1].split("<!-- /batch-files -->")[0]
              .splitlines() if ln.strip().startswith("- `")]
    assert listed, "batch file list missing"
    snapshot = {f["path"] for f in baseline["files"]}
    for p in listed:
        assert (ROOT / p).exists(), p
        assert p not in snapshot, p
