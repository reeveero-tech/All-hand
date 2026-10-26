#!/usr/bin/env python3
"""R-187 (D-102): bind the push-scope verdict to the delivery head (merge link).

On pull_request events the push-scope job cannot re-derive the push
boundaries (git does not store push events), and the PR path must not
invent a verdict of its own. So it does the only honest thing: it verifies
that the PR's HEAD SHA has a COMPLETED, SUCCESSFUL push-event run of the
push-scope check (the job "push-scope-docs" in governance-gate.yml) **on
the PR head's own branch** — the same SHA pushed elsewhere must not satisfy
this PR. Every PR head was necessarily pushed, so a run must exist; a red
(docless) push on the head keeps the merge blocked.

A run that is missing or still in progress is a BOUNDED WAIT (the PR run
may query before the push run appears in the API), never a pass; the
script fails only when the wait times out.

Usage: GH_TOKEN=... python3 scripts/verify_push_scope_head.py REPO HEAD_SHA HEAD_BRANCH

Exit codes: 0 = head's push-scope run is green; 1 = red or wait timed out;
            2 = API error (never treated as a pass); 3 = pending (internal
            poll signal — never returned to the caller as success).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

WORKFLOW = "governance-gate.yml"
JOB = "push-scope-docs"
POLL_SECONDS = 15
WAIT_TIMEOUT_SECONDS = 300


def _api(url: str, token: str) -> dict | list:
    res = subprocess.run(
        ["curl", "-fsS", "-H", f"Authorization: Bearer {token}",
         "-H", "Accept: application/vnd.github+json", url],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"API failed for {url}: {res.stderr.strip()}")
    return json.loads(res.stdout)


def latest_push_run(repo: str, sha: str, branch: str, token: str) -> dict | None:
    """Latest push-event run of this workflow for SHA **on the given branch**.

    The same SHA may exist on several branches (e.g. a branch created from
    the PR head); only the PR head branch's own run can satisfy this PR.
    """
    runs = _api(f"https://api.github.com/repos/{repo}/actions/workflows/"
               f"{WORKFLOW}/runs?head_sha={sha}&event=push&per_page=20", token)
    items = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    items = [r for r in items if r.get("head_branch") == branch]
    return max(items, key=lambda r: r["id"]) if items else None


def run_jobs(repo: str, run_id: int, token: str) -> list[dict]:
    return _api(f"https://api.github.com/repos/{repo}/actions/runs/"
                f"{run_id}/jobs?per_page=50", token).get("jobs", [])


def bind_verdict(run: dict | None, jobs: list[dict]) -> tuple[int, str]:
    """Decide from the head-branch push-run data alone. 3 = keep waiting."""
    if run is None:
        return 3, ("no push-scope run visible yet for the head SHA on its "
                   "branch — waiting (it may still be appearing in the API)")
    if run.get("status") != "completed":
        return 3, f"push-scope run {run['id']} still {run.get('status')} — waiting"
    job = next((j for j in jobs if j.get("name") == JOB), None)
    if job is None:
        return 1, f"job {JOB} missing from push run {run['id']}"
    if job.get("conclusion") != "success":
        return 1, (f"job {JOB} RED on the head SHA "
                   f"(run {run['id']}: {job.get('conclusion')}) — the docs "
                   f"must travel WITH the push")
    return 0, ""


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: verify_push_scope_head.py REPO HEAD_SHA HEAD_BRANCH",
              file=sys.stderr)
        return 2
    repo, sha, branch = argv[1], argv[2], argv[3]
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("::error::GH_TOKEN not set")
        return 2
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        try:
            run = latest_push_run(repo, sha, branch, token)
            jobs = run_jobs(repo, run["id"], token) if run else []
            rc, why = bind_verdict(run, jobs)
        except (RuntimeError, ValueError) as e:
            print(f"::error::{e}")
            return 2
        if rc == 0:
            print("R-187 merge binding: the head branch's push-scope run is green.")
            return 0
        if rc == 1:
            print(f"::error::R-187 merge binding: {why}")
            return 1
        # rc == 3: pending — a bounded wait, never a pass on its own
        if time.monotonic() >= deadline:
            print(f"::error::R-187 merge binding: timed out waiting for the "
                  f"head branch's push run ({why})")
            return 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
