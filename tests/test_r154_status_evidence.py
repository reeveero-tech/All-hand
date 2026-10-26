"""R-154 (D-066): positive and negative tests for scripts/check_status_evidence.py.

Every test builds a real git repository containing a genuine PR merge
(``git merge --no-ff`` with GitHub's "Merge pull request #N from" subject, so
the merge commit has two parents), writes governance files that reference
it, then mutates exactly one thing per negative case. The script is run both
in-process (``check``) and as a subprocess (exit codes).
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
SCRIPT = ROOT / "scripts" / "check_status_evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "governance-gate.yml"
sys.path.insert(0, str(ROOT / "scripts"))
import check_status_evidence as cse  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
TESTS = "12 passed, 1 skipped, 2 deselected"
M9_FIRST_ITEM = 187  # M8 items: R-151..R-184 (R-185/R-186 are governance
# entries, not roadmap items); M9 items start at R-187 (D-102)


def _prog(rid: str) -> str:
    return "M9" if int(rid[2:]) >= M9_FIRST_ITEM else "M8"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fields(**kw) -> str:
    return "- أدلة الحالة (R-154): " + " | ".join(f"{k}={v}" for k, v in kw.items())


class Repo:
    def __init__(self, path: Path):
        self.p = path
        self.items: dict[str, tuple[str, str]] = {}   # id -> (status, field line)
        self.table: dict[str, str] | None = None       # M8 table override
        self.m9_table: dict[str, str] | None = None     # M9 table override
        self.omit_m9_table = False

    def write(self) -> None:
        road = ["# ROADMAP", "", "### R-100 — legacy item", "- الحالة: **done**", ""]
        for rid, (st, fl) in self.items.items():
            block = [f"#### {rid} — item", f"- الحالة: **{st}**"] + ([fl] if fl else [])
            if _prog(rid) == "M9":
                block.append(f"- الاعتماديات ({rid}): لا شيء")  # M9 must declare deps (10b)
            road += block + [""]
        _w(self.p, "docs/governance/ROADMAP.md", "\n".join(road) + "\n")
        state = ["# STATE", ""]
        for pname, override, member in (("M8", self.table, _prog), ("M9", self.m9_table, _prog)):
            prog_items = {rid: v[0] for rid, v in self.items.items() if member(rid) == pname}
            if not prog_items or (pname == "M9" and self.omit_m9_table):
                continue
            table = override if override is not None else prog_items
            rows = "\n".join(f"| {k} | `{v}` |" for k, v in table.items())
            state += [f"## حالة بنود {pname} (مزامنة)", "", "| البند | الحالة |", "|---|---|", rows, ""]
        state += ["## other", ""]
        _w(self.p, "docs/governance/PROJECT_STATE.md", "\n".join(state) + "\n")

    def run(self):
        self.write()
        return cse.check(str(self.p))


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    p = tmp_path / "r"
    p.mkdir()
    _git(p, "init", "-q", "-b", "main")
    _w(p, "README.md", "base\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "base")
    _git(p, "checkout", "-q", "-b", "feature")
    _w(p, "feature.py", "x = 1\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "feature work")
    tested = _git(p, "rev-parse", "HEAD")
    _git(p, "checkout", "-q", "main")
    _git(p, "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #7 from org/feature")
    merge = _git(p, "rev-parse", "HEAD")
    _w(p, "later.txt", "after merge\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "later commit (next batch)")
    r = Repo(p)
    # R-170 batch 9: was "4242" — a 4-digit id occurs inside a random 40-hex
    # commit SHA ~0.1% of the time, so redacting ci_run left it "present" and
    # test_rule4_acceptance_log_must_contain_values[run] failed in CI (run
    # 40076480248). 16 digits: chance of occurring in two SHAs ~ 1e-18.
    r.tested, r.merge, r.run_id = tested, merge, "4242424242424242"
    _w(p, "docs/audit/acc.log", f"PR #7 MERGED merge={merge} head={tested}\nrun {r.run_id} conclusion=success\n")
    _w(p, "docs/audit/pytest.log", f"===== {TESTS} in 1.0s =====\n")
    _w(p, "docs/governance/DECISIONS.md", "## D-900: owner accepts R-151\n")
    _w(p, "docs/governance/OPEN_QUESTIONS.md", "## Q-900 — need a secret\n")
    _w(p, "docs/governance/RISKS.md", "### RISK-900: something\n")
    r.done = dict(pr="#7", tested_sha=tested, merge_sha=merge, ci_run=r.run_id,
                  ci_conclusion="success", tests=TESTS, tests_log="docs/audit/pytest.log",
                  skipped="1 skipped live test (needs secret, runs in job storage); 2 network",
                  untested="no load test", evidence="docs/audit/acc.log")
    r.items = {"R-151": ("done", _fields(**r.done)), "R-152": ("planned", "")}
    return r


def _with(r: Repo, **over) -> str:
    return _fields(**{**r.done, **over})


# ---- positive ---------------------------------------------------------------

def test_valid_done_and_planned_pass(repo):
    assert repo.run() == []


def test_valid_accepted_passes(repo):
    repo.items["R-151"] = ("accepted", _with(repo, accepted_by="owner", decision="D-900"))
    assert repo.run() == []


def test_valid_blocked_passes(repo):
    repo.items["R-152"] = ("blocked", _fields(blocker="Q-900", reason="needs owner secret"))
    repo.items["R-153"] = ("in_progress", _fields(branch="m8/r153-x"))  # blocked does not stop later items
    assert repo.run() == []


def test_awaiting_merge_with_unmerged_pr_passes(repo):
    repo.items["R-152"] = ("awaiting_merge", _fields(branch="m8/r152-x", pr="#8"))
    assert repo.run() == []


def test_awaiting_merge_on_its_own_merge_commit_is_grace(repo):
    _git(repo.p, "reset", "-q", "--hard", repo.merge)          # HEAD == merge of PR #7
    repo.items["R-151"] = ("awaiting_merge", _fields(branch="m8/r151-x", pr="#7"))
    _w(repo.p, "docs/audit/acc.log", "x\n")
    assert repo.run() == []


def test_legacy_item_needs_no_fields(repo):
    assert not [v for v in repo.run() if "R-100" in v]


# ---- negative: one rule each --------------------------------------------------

@pytest.mark.parametrize("bad", ["ready", "proposed", "production-ready", "done ✓", "مكتمل"])
def test_rule1_invalid_status(repo, bad):
    repo.items["R-152"] = (bad, "")
    assert any("invalid status" in v and "R-152" in v for v in repo.run())


def test_rule1_two_status_lines(repo):
    repo.items["R-152"] = ("planned", "- الحالة: **done**")
    assert any("exactly one" in v for v in repo.run())


@pytest.mark.parametrize("field", ["pr", "tested_sha", "merge_sha", "ci_run", "ci_conclusion",
                                   "tests", "tests_log", "skipped", "untested", "evidence"])
def test_rule2_each_done_field_required(repo, field):
    d = {k: v for k, v in repo.done.items() if k != field}
    repo.items["R-151"] = ("done", _fields(**d))
    assert any(f"missing evidence field '{field}'" in v for v in repo.run())


def test_rule2_done_without_any_field_line(repo):
    repo.items["R-151"] = ("done", "")
    assert len([v for v in repo.run() if "missing evidence field" in v]) == 10


@pytest.mark.parametrize("over,msg", [
    ({"pr": "165"}, "pr must look like #N"),
    ({"merge_sha": "a34dc77"}, "full 40-hex"),
    ({"ci_run": "latest"}, "numeric run id"),
    ({"ci_conclusion": "failure"}, "must be 'success'"),
    ({"tests": "all green"}, "tests must be"),
])
def test_rule2_malformed_fields(repo, over, msg):
    repo.items["R-151"] = ("done", _with(repo, **over))
    assert any(msg in v for v in repo.run())


def test_rule3_merge_of_another_pr(repo):
    repo.items["R-151"] = ("done", _with(repo, pr="#9"))
    assert any("not a merge of PR #9" in v for v in repo.run())


def test_rule3_tested_sha_not_second_parent(repo):
    base = _git(repo.p, "rev-parse", f"{repo.merge}^1")
    repo.items["R-151"] = ("done", _with(repo, tested_sha=base))
    _w(repo.p, "docs/audit/acc.log", f"{repo.merge} {base} {repo.run_id} success\n")
    assert any("merge_sha^2" in v for v in repo.run())


def test_rule3_merge_sha_not_in_history(repo):
    repo.items["R-151"] = ("done", _with(repo, merge_sha="f" * 40))
    assert any("not a commit reachable" in v for v in repo.run())


@pytest.mark.parametrize("drop", ["merge", "tested", "run", "success"])
def test_rule4_acceptance_log_must_contain_values(repo, drop):
    vals = {"merge": repo.merge, "tested": repo.tested, "run": repo.run_id, "success": "success"}
    vals[drop] = "redacted"
    _w(repo.p, "docs/audit/acc.log", " ".join(vals.values()) + "\n")
    assert any("does not contain" in v and "a file existing is not sufficient" in v
               for v in repo.run())


def test_rule4_existing_but_empty_evidence_file_rejected(repo):
    _w(repo.p, "docs/audit/acc.log", "")
    assert len([v for v in repo.run() if "acceptance log" in v]) == 4


def test_rule4_evidence_outside_docs_audit(repo):
    _w(repo.p, "notes/acc.log", "x")
    repo.items["R-151"] = ("done", _with(repo, evidence="notes/acc.log"))
    assert any("outside docs/audit/" in v for v in repo.run())


def test_rule4_tests_log_mismatch(repo):
    repo.items["R-151"] = ("done", _with(repo, tests="13 passed, 1 skipped, 2 deselected"))
    assert any("tests_log" in v and "does not contain" in v for v in repo.run())


@pytest.mark.parametrize("val", ["none", "لا شيء", "-"])
def test_rule5_skipped_none_rejected_when_counts_positive(repo, val):
    repo.items["R-151"] = ("done", _with(repo, skipped=val))
    assert any("skipped/deselected > 0" in v for v in repo.run())


def test_rule5_untested_must_be_explicit(repo):
    repo.items["R-151"] = ("done", _with(repo, untested="none"))
    assert any("'untested' must state" in v for v in repo.run())


def test_rule6_agent_cannot_self_accept(repo):
    repo.items["R-151"] = ("accepted", _with(repo, accepted_by="agent", decision="D-900"))
    assert any("accepted_by must be 'owner'" in v for v in repo.run())


def test_rule6_decision_must_exist(repo):
    repo.items["R-151"] = ("accepted", _with(repo, accepted_by="owner", decision="D-901"))
    assert any("D-901 not found" in v for v in repo.run())


def test_rule7_blocker_must_exist(repo):
    repo.items["R-152"] = ("blocked", _fields(blocker="Q-999", reason="x"))
    assert any("blocker 'Q-999' not found" in v for v in repo.run())


def test_rule8_merged_but_still_awaiting_merge(repo):
    # exactly the R-153 situation before R-154: PR merged, docs not updated
    repo.items["R-151"] = ("awaiting_merge", _fields(branch="m8/r151-x", pr="#7"))
    got = repo.run()
    assert any("CONTRADICTION" in v and "PR #7 is already merged" in v for v in got)


def test_rule9_state_table_mismatch(repo):
    repo.table = {"R-151": "awaiting_merge", "R-152": "planned"}
    assert any("PROJECT_STATE table: R-151=awaiting_merge but ROADMAP=done" in v for v in repo.run())


def test_rule9_state_table_missing_and_extra_rows(repo):
    repo.table = {"R-151": "done", "R-199": "planned"}
    got = repo.run()
    assert any("R-152 missing" in v for v in got) and any("R-199 not an M8 item" in v for v in got)


def test_rule9_state_table_section_absent(repo):
    repo.write()
    _w(repo.p, "docs/governance/PROJECT_STATE.md", "# STATE\n")
    assert any("section" in v and "missing" in v for v in cse.check(str(repo.p)))


def test_rule10_started_item_after_planned(repo):
    repo.items["R-151"] = ("planned", "")
    repo.items["R-152"] = ("in_progress", _fields(branch="m8/r152-x"))
    assert any("R-152: is in_progress while earlier item R-151 is still planned" in v
               for v in repo.run())


# ---- M9 program scoping (M8→M9 transition, batch R-187 / D-102) -----------------
# The checker scopes rules 9 (state-table sync) and 10 (ordering) per program:
# M8 = R-151..R-184, M9 = R-187+. Cross-program order is NOT enforced — M9 items
# document their M8 dependencies per item instead (e.g. R-188 depends on R-171).

def test_m9_started_while_earlier_m8_item_planned_is_not_order_violation(repo):
    repo.items["R-171"] = ("planned", "")
    repo.items["R-187"] = ("in_progress", _fields(branch="m9/name-fulfillment-plan"))
    assert repo.run() == []

def test_m9_ordering_still_enforced_within_m9(repo):
    repo.items["R-187"] = ("planned", "")
    repo.items["R-188"] = ("in_progress", _fields(branch="m9/r188-x"))
    assert any("R-188: is in_progress while earlier item R-187 is still planned" in v
               for v in repo.run())

def test_m9_state_table_section_required(repo):
    repo.items["R-187"] = ("planned", "")
    repo.omit_m9_table = True
    assert any("'## حالة بنود M9' missing" in v for v in repo.run())

def test_m9_state_table_mismatch(repo):
    repo.items["R-187"] = ("planned", "")
    repo.items["R-188"] = ("planned", "")
    repo.m9_table = {"R-187": "planned", "R-188": "in_progress"}
    assert any("PROJECT_STATE table: R-188=in_progress but ROADMAP=planned" in v
               for v in repo.run())

def test_m9_done_item_requires_full_evidence_like_m8(repo):
    repo.items["R-187"] = ("done", "")
    assert len([v for v in repo.run() if "missing evidence field" in v]) == 10


def test_m9_started_item_with_planned_dep_rejected(repo):
    # R-188 depends on R-171 (planned): cross-program dependencies are
    # machine-enforced, not prose — starting a dependent item while its
    # dependency is still planned is a violation (advisor review of D-102 batch).
    repo.items["R-171"] = ("planned", "")
    repo.items["R-187"] = ("in_progress", _fields(branch="m9/r187-x"))
    repo.items["R-188"] = ("in_progress", _fields(branch="m9/r188-x"))
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-188): لا شيء", "- الاعتماديات (R-188): R-171"))
    got = cse.check(str(repo.p))
    assert any("R-188: dependency R-171 is planned" in v for v in got)
    assert not any("R-187: dependency" in v for v in got)  # R-187 declares none


def test_m9_started_item_with_done_dep_passes(repo):
    repo.items["R-152"] = ("done", _fields(**repo.done))  # keep M8 ordering valid
    repo.items["R-163"] = ("done", _fields(**repo.done))
    repo.items["R-187"] = ("in_progress", _fields(branch="m9/r187-x"))
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): R-163"))
    assert cse.check(str(repo.p)) == []


def test_surplus_program_table_without_roadmap_items_rejected(repo):
    # A program table whose rows reference no ROADMAP items must be flagged —
    # the per-program loop may not silently ignore a stale table (advisor).
    repo.write()
    state = (repo.p / "docs/governance/PROJECT_STATE.md").read_text(encoding="utf-8")
    _w(repo.p, "docs/governance/PROJECT_STATE.md", state.replace(
        "## other",
        "## حالة بنود M9 (مزامنة)\n\n| البند | الحالة |\n|---|---|\n| R-187 | `planned` |\n\n## other"))
    assert any("R-187 not an M9 item in ROADMAP" in v for v in cse.check(str(repo.p)))


def test_m9_item_without_deps_line_rejected(repo):
    # M9 items MUST declare the deps line — deleting it (or never writing it)
    # may not silently pass as deps=[] (advisor: absence accepted silently =
    # open failure mode of 10b).
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء\n", ""))
    assert any("R-187: M9 items must declare dependencies" in v
               for v in cse.check(str(repo.p)))


def test_m9_deps_line_with_unrecognized_reference_rejected(repo):
    # Only R-NNN tokens are supported deps; SEC-07 (or any other prefix) in
    # the deps line may not be silently ignored — strict token parsing.
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): R-100، SEC-07"))
    assert any("R-187: deps line is empty/malformed — invalid token 'SEC-07'" in v
               for v in cse.check(str(repo.p)))


def test_m9_deps_line_empty_value_rejected(repo):
    # An empty/malformed deps value is not a declaration.
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): (انظر النص)"))
    assert any("R-187: deps line is empty/malformed" in v
               for v in cse.check(str(repo.p)))


def test_m9_deps_four_digit_id_not_misparsed(repo):
    # R-1510 must NOT be silently read as R-151 (full-token parsing).
    repo.items["R-151"] = ("planned", "")
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): R-1510"))
    got = cse.check(str(repo.p))
    assert any("R-1510" in v for v in got)
    assert not any("R-151:" in v for v in got)  # not misparsed as the real R-151


def test_m9_deps_unknown_prefix_token_rejected(repo):
    # TOOL-07 (or any non-R token) may not bypass a prefix blacklist — only
    # well-formed R-NNN tokens are accepted.
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): R-151، TOOL-07"))
    assert any("TOOL-07" in v and "R-187" in v for v in cse.check(str(repo.p)))


def test_m9_deps_explicit_none_must_be_exact(repo):
    # «لا شيء — نص غير صالح» is not the explicit none value; prefix match is
    # not enough — the value must be exactly 'لا شيء'.
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-187): لا شيء — نص"))
    assert any("R-187" in v and "لا شيء" in v and ("exactly" in v or "بالضبط" in v)
               for v in cse.check(str(repo.p)))


def test_m9_deps_line_tag_must_match_item(repo):
    # A deps line tagged (R-189) inside item R-190 is a mislabel — the tag must
    # match the containing item (this exact mislabel existed in the real
    # ROADMAP and was not caught).
    repo.items["R-187"] = ("planned", "")
    repo.write()
    _w(repo.p, "docs/governance/ROADMAP.md",
       (repo.p / "docs/governance/ROADMAP.md").read_text(encoding="utf-8").replace(
           "- الاعتماديات (R-187): لا شيء", "- الاعتماديات (R-189): لا شيء"))
    assert any("R-187" in v and "R-189" in v and "tag" in v.lower()
               for v in cse.check(str(repo.p)))


# ---- CLI exit codes -----------------------------------------------------------

def _cli(path):
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)


def test_cli_pass_and_fail_codes(repo):
    repo.write()
    assert _cli(repo.p).returncode == 0
    repo.items["R-152"] = ("ready", "")
    repo.write()
    res = _cli(repo.p)
    assert res.returncode == 1 and "::error::R-152: invalid status 'ready'" in res.stdout


def test_cli_not_a_repo_is_error(tmp_path):
    _w(tmp_path, "docs/governance/ROADMAP.md", "x")
    _w(tmp_path, "docs/governance/PROJECT_STATE.md", "x")
    assert _cli(tmp_path).returncode == 2


# ---- contract -----------------------------------------------------------------

def test_workflow_runs_status_check_after_full_checkout():
    wf = WORKFLOW.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in wf
    assert re.search(r"- name: Status evidence check \(R-154\)\n\s+if: \$\{\{ !cancelled\(\) \}\}\n"
                     r"\s+run: python3 scripts/check_status_evidence\.py \.", wf)
    for older in ("No-delete check (R-152)", "- name: Atomic batch check\n",
                  "Atomic batch check v2 (R-153)"):
        assert older in wf  # earlier gates kept
