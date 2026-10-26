"""R-187 (D-102): push-scope documentation check.

R-187 requires documentation to travel WITH every push — not just in the
PR aggregate diff. The existing gates (atomic batch v1/v2, governance-gate)
judge ``origin/main...HEAD``: a PR whose FIRST push carries docs but whose
SECOND push is code-only passes them, because the earlier docs are still in
the aggregate diff. This check closes that gap at the same-event scope:
``github.event.before..after`` for push events (branch creation: the branch's
own commits vs origin/main).

Honest limits (documented in QUALITY/log): CI runs AFTER the ref is updated,
so a red check cannot prevent the push from arriving — it makes the violation
loud (red run on the branch/main) and keeps merges blocked via the required
pull_request checks. CI prevents merges, not push arrival.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_push_docs.py"
WORKFLOW = ROOT / ".github" / "workflows" / "governance-gate.yml"
sys.path.insert(0, str(ROOT / "scripts"))
import check_push_docs as cpd  # noqa: E402
import verify_push_scope_head as vps  # noqa: E402

DOCS = ["docs/governance/ROADMAP.md", "docs/governance/PROJECT_STATE.md",
        "docs/governance/CHANGELOG.md"]
EV = "docs/audit/M8/logs/raw/999_x.log"
ZEROS = "0" * 40

def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(repo: Path, msg: str = "c") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.strip()


def _run(repo: Path, before: str, after: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), before, after], cwd=repo,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for f in DOCS + ["scripts/tool.py", "server.py", "tests/test_s.py"]:
        _write(r, f, "base\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    # make origin/main exist so branch-creation scope can resolve it
    _git(r, "update-ref", "refs/remotes/origin/main", "main")
    return r


# ---- e2e: same-event scope (the advisor-mandated cases) -------------------------

@pytest_git
def test_new_push_without_docs_inside_pr_fails(repo):
    """The key gap: push 1 = code+docs (aggregate has docs), push 2 = code
    only. PR-scope gates pass; the push-scope check must fail (R0)."""
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push1\n")
    _commit(repo, "push1: code + docs")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()
    _write(repo, "scripts/tool.py", "v2\n")
    second = _commit(repo, "push2: code only")
    res = _run(repo, first, second)
    assert res.returncode == 1
    assert "::error::R-187 R0" in res.stdout


@pytest_git
def test_push_with_docs_passes(repo):
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push1\n")
    first = _commit(repo, "push1")
    _write(repo, "scripts/tool.py", "v2\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push2\n")
    second = _commit(repo, "push2: code + docs")
    assert _run(repo, first, second).returncode == 0


@pytest_git
def test_branch_creation_code_only_fails(repo):
    """before = zeros (new branch): scope = the branch's own commits vs
    origin/main — code-only commits fail even though nothing else was pushed."""
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "scripts/tool.py", "new\n")
    head = _commit(repo, "code only")
    res = _run(repo, ZEROS, head)
    assert res.returncode == 1 and "::error::R-187 R0" in res.stdout


@pytest_git
def test_branch_creation_with_full_batch_passes(repo):
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "scripts/tool.py", "new\n")
    for d in DOCS + [EV]:
        _write(repo, d, "new\n")
    head = _commit(repo, "code + docs")
    assert _run(repo, ZEROS, head).returncode == 0


@pytest_git
def test_branch_pointing_at_main_passes(repo):
    """Branch creation with no new commits: empty scope, nothing to document."""
    _git(repo, "branch", "-q", "same")
    assert _run(repo, ZEROS, "same").returncode == 0


@pytest_git
def test_direct_push_to_main_code_only_fails(repo):
    _write(repo, "scripts/tool.py", "main-code\n")
    head = _commit(repo, "direct to main")
    before = subprocess.run(["git", "rev-parse", "HEAD^"], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    res = _run(repo, before, head)
    assert res.returncode == 1 and "::error::R-187 R0" in res.stdout


@pytest_git
def test_deleted_branch_is_not_a_failure(repo):
    """after = zeros (branch deletion): nothing to check, exit 0."""
    assert _run(repo, "main", ZEROS).returncode == 0


@pytest_git
def test_bad_before_ref_is_error(repo):
    assert _run(repo, "no-such-ref", "main").returncode == 2


# ---- unit: scope selection ------------------------------------------------------

@pytest_git
def test_scope_before_zeros_uses_origin_main(repo):
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "scripts/tool.py", "new\n")
    head = _commit(repo, "code only")
    changed = cpd.push_scope(ZEROS, head, cwd=str(repo))
    assert changed == ["scripts/tool.py"]


@pytest_git
def test_scope_uses_exact_push_range(repo):
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS:
        _write(repo, d, "push1\n")
    first = _commit(repo, "push1")
    _write(repo, "scripts/tool.py", "v2\n")
    second = _commit(repo, "push2")
    assert cpd.push_scope(first, second, cwd=str(repo)) == ["scripts/tool.py"]


def test_rules_shared_with_atomic_batch_v2():
    """The push-scope check reuses the SAME rule engine (R0..R6) — it adds
    a scope, not a second opinion on what needs docs."""
    changed = DOCS + ["scripts/tool.py"]
    assert [v[:2] for v in cpd.evaluate(changed)] == ["R1"]


# ---- contract: workflow wiring ---------------------------------------------------

def _job_body(wf: str, name: str) -> str:
    """Slice one job block: from `  name:` to the next top-level (2-space)
    line or end of file."""
    start = wf.index(f"  {name}:")
    m = re.compile(r"\n(?=  \S)").search(wf, start)
    return wf[start:m.start() if m else len(wf)]


def test_workflow_runs_push_scope_check_on_push_events():
    wf = WORKFLOW.read_text(encoding="utf-8")
    # push trigger on all branches (R-187: every push, not only PRs)
    assert re.search(r"push:\s*\n\s*branches:\s*\['\*\*'\]", wf)
    # the PR-scoped governance job must not run on push events (it reads
    # github.event.pull_request.number, which does not exist there)
    gov = _job_body(wf, "governance")
    assert "github.event_name == 'pull_request'" in gov
    # the push-scope job exists, guards itself to push events and calls the
    # script with the same-event refs
    body = _job_body(wf, "push-scope-docs")
    assert "github.event_name == 'push'" in body
    assert re.search(r"python3 scripts/check_push_docs\.py \"\$\{\{ github\.event\.before \}\}\""
                     r" \"\$\{\{ github\.event\.after \}\}\"", body)
    assert "continue-on-error" not in wf


def test_pr_gate_not_weakened():
    """The pull_request path keeps every existing check (v1, v2, R-152,
    R-154, R-186) — the change only ADDS a push path."""
    wf = WORKFLOW.read_text(encoding="utf-8")
    for step in ["No-delete check (R-152)", "Conflict-marker check",
                 "Atomic batch check\n", "Do-not-merge hold check (R-186)",
                 "Atomic batch check v2 (R-153)", "Status evidence check (R-154)"]:
        assert step in wf


# ---- red round 2 (advisor review): two-dot tree diff + merge binding ---------

def test_scope_is_tree_diff_not_merge_base_rollback(repo):
    """Rollback push (before=code+docs commit, after=its ancestor): the push
    DOES deliver tree changes (reverts them) — a merge-base diff would be
    empty and let the push pass unexamined. Tree diff is not empty."""
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push1\n")
    first = _commit(repo, "push1: code + docs")
    _git(repo, "reset", "-q", "--hard", "main")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert base != first
    changed = cpd.push_scope(first, base, cwd=str(repo))
    assert changed  # merge-base diff would be empty here


def test_scope_is_tree_diff_divergent_lineage(repo):
    """Force-push onto an unrelated lineage: scope must be the actual tree
    difference BEFORE..AFTER, not merge-base..AFTER (which would drag in
    changes already contained in BEFORE, or miss reverted ones)."""
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push1\n")
    first = _commit(repo, "push1")
    # a second, unrelated lineage off main with code only
    _git(repo, "checkout", "-q", "-B", "pr2", "main")
    _write(repo, "scripts/other.py", "v2\n")
    second = _commit(repo, "unrelated code-only lineage")
    changed = cpd.push_scope(first, second, cwd=str(repo))
    # the tree diff between the two heads: tool.py content, docs content,
    # other.py added — NOT just "changes since the fork point"
    assert "scripts/other.py" in changed
    assert "scripts/tool.py" in changed


def test_reproduction_aggregate_passes_push_scope_fails(repo):
    """The executed reproduction (not a narrative): on the SAME repo state,
    the OLD aggregate gate passes while the push-scope check rejects the
    docless push — proving the gap is real and closed."""
    _git(repo, "checkout", "-q", "-b", "pr")
    _write(repo, "scripts/tool.py", "v1\n")
    for d in DOCS + [EV]:
        _write(repo, d, "push1\n")
    first = _commit(repo, "push1: code + docs")
    _write(repo, "scripts/tool.py", "v2\n")
    second = _commit(repo, "push2: code only")
    # OLD gate: aggregate origin/main...HEAD (has push1 docs) — passes
    agg = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_atomic_batch.py"),
                          "origin/main", "HEAD"], cwd=repo, capture_output=True, text=True)
    # NEW check: the second push alone — rejected
    push = _run(repo, first, second)
    assert agg.returncode == 0, agg.stdout
    assert push.returncode == 1 and "::error::R-187 R0" in push.stdout


def test_empty_tree_fallback_is_direct_tree_diff(tmp_path):
    """No origin/main and no main at all (orphan repo): branch creation scope
    falls back to a direct diff against the empty tree (all files changed)."""
    r = tmp_path / "orphan"
    r.mkdir()
    _git(r, "init", "-q", "-b", "x")
    _write(r, "scripts/tool.py", "code\n")
    _write(r, "docs/governance/ROADMAP.md", "d\n")
    head = _commit(r, "orphan")
    changed = cpd.push_scope(ZEROS, head, cwd=str(r))
    assert set(changed) == {"scripts/tool.py", "docs/governance/ROADMAP.md"}


# ---- red round 2: merge binding (PR path verifies the head's push run) --------

def test_verdict_passes_on_green_push_run():
    run = {"id": 7, "status": "completed", "conclusion": "success"}
    jobs = [{"name": "push-scope-docs", "conclusion": "success"}]
    assert vps.bind_verdict(run, jobs) == (0, "")


def test_verdict_fails_on_red_push_run():
    run = {"id": 7, "status": "completed", "conclusion": "failure"}
    jobs = [{"name": "push-scope-docs", "conclusion": "failure"}]
    rc, why = vps.bind_verdict(run, jobs)
    assert rc == 1 and "push-scope-docs" in why


def test_verdict_fails_when_job_missing_from_run():
    run = {"id": 7, "status": "completed", "conclusion": "success"}
    jobs = [{"name": "governance", "conclusion": "success"}]
    rc, why = vps.bind_verdict(run, jobs)
    assert rc == 1 and "push-scope-docs" in why


def test_verdict_pending_run_is_not_a_pass():
    run = {"id": 7, "status": "in_progress", "conclusion": None}
    rc, why = vps.bind_verdict(run, jobs=[])
    assert rc == 3  # pending: caller must poll, not pass


def test_verdict_no_run_at_all_fails():
    # round-3 semantics: absence is a bounded WAIT (3) — the caller fails on
    # timeout; see test_main_fails_after_timeout_when_run_never_appears
    rc, why = vps.bind_verdict(None, [])
    assert rc == 3 and "waiting" in why


def test_workflow_pr_path_binds_head_to_push_run():
    """The PR path must REQUIRE the head's push-run verdict (not a skip):
    the job runs on both events; on pull_request it calls the binding script."""
    wf = WORKFLOW.read_text(encoding="utf-8")
    body = _job_body(wf, "push-scope-docs")
    assert "github.event_name == 'push'" in body or "GITHUB_EVENT_NAME" in body
    assert "verify_push_scope_head.py" in body
    assert "GH_TOKEN" in body


# ---- red round 3 (advisor review): branch-matched binding + wait semantics ---

def test_binding_matches_branch_not_sha_only(monkeypatch):
    """Same SHA pushed to another branch must not satisfy this PR: the
    verdict comes from the PR head BRANCH's own push run."""
    same_sha = "a" * 40
    runs = {"workflow_runs": [
        {"id": 11, "head_branch": "other-branch", "head_sha": same_sha,
         "status": "completed", "conclusion": "success"},
        {"id": 10, "head_branch": "pr-branch", "head_sha": same_sha,
         "status": "completed", "conclusion": "failure"},
    ]}
    monkeypatch.setattr(vps, "_api", lambda url, token: runs)
    run = vps.latest_push_run("r/x", same_sha, "pr-branch", token="t")
    assert run["id"] == 10  # the PR branch's run, not the newer other-branch one


def test_verdict_no_run_is_pending_not_immediate_fail():
    """A PR-run may query before the push run appears in the API: absence is
    a bounded WAIT (rc=3), never a pass; the caller fails on timeout."""
    rc, why = vps.bind_verdict(None, [])
    assert rc == 3 and "waiting" in why


def test_main_polls_missing_then_in_progress_then_success(monkeypatch):
    calls = {"n": 0}

    def fake_latest(repo, sha, branch, token):
        calls["n"] += 1
        return [{"id": 5, "head_branch": branch, "head_sha": sha,
                 "status": "completed", "conclusion": "success"}][0] \
            if calls["n"] >= 3 else (None if calls["n"] == 1 else
                                     {"id": 5, "status": "in_progress"})

    monkeypatch.setattr(vps, "latest_push_run", fake_latest)
    monkeypatch.setattr(vps, "run_jobs", lambda repo, run_id, token: [
        {"name": "push-scope-docs", "conclusion": "success"}])
    monkeypatch.setattr(vps, "_api", lambda *a, **k: {})
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(vps.time, "monotonic",
                        lambda: [0, 1, 2, 3, 1000][min(calls["n"], 4)])
    monkeypatch.setattr(vps.time, "sleep", lambda s: None)
    rc = vps.main(["x", "r/x", "a" * 40, "b"])
    assert rc == 0 and calls["n"] == 3


def test_main_fails_after_timeout_when_run_never_appears(monkeypatch):
    # deadline is set from the FIRST monotonic() call; later calls must grow
    # past it (a constant would never exceed deadline + timeout — a real
    # infinite loop the first version of this test had).
    clock = iter([0] + [10_000 + i for i in range(10)])
    monkeypatch.setattr(vps, "latest_push_run", lambda *a: None)
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(vps.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(vps.time, "sleep", lambda s: None)
    rc = vps.main(["x", "r/x", "a" * 40, "b"])
    assert rc == 1
