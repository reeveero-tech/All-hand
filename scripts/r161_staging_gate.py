#!/usr/bin/env python3
"""R-161b (G-161-5): fail-closed readiness gate for the manual live workflow.

Runs in the ``gate`` job of .github/workflows/live-integration.yml — a job with
NO environment and NO staging secrets (only the ephemeral read-only job token
to READ the environment's settings). The ``live`` job, which references the
staging environment, takes its environment NAME from this gate's output, so
it can only reference ``r161-staging`` after every check below passed:

  1. the run is on ``refs/heads/main``;
  2. the owner declared readiness: repository variable
     ``R161_STAGING_READY`` == ``owner-verified`` (a non-secret attestation —
     not proof by itself, hence 3 and 4);
  3. the environment ALREADY EXISTS (GET /environments/r161-staging -> 200;
     a GET never creates it) and has a ``required_reviewers`` rule with at
     least one reviewer;
  4. its deployment-branch policy is custom and allows exactly ``main``.

Anything else — including an API error, a missing token or an unexpected
answer — fails (exit 1) with the reason, and writes no output. Only stdlib.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ENVIRONMENT = "r161-staging"
READY_VALUE = "owner-verified"
MAIN_REF = "refs/heads/main"


def evaluate(ref: str, ready: str, env_status: int, env: Optional[Dict[str, Any]],
             policies: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Pure decision (unit-tested). Returns (ok, reasons-for-refusal)."""
    why: List[str] = []
    if ref != MAIN_REF:
        why.append(f"run is not on main (ref {ref or 'missing'})")
    if ready != READY_VALUE:
        why.append(f"repository variable R161_STAGING_READY is not '{READY_VALUE}' "
                   "(the owner has not declared the environment ready)")
    if env_status != 200 or not isinstance(env, dict):
        why.append(f"environment {ENVIRONMENT} does not exist or cannot be read (HTTP {env_status}); "
                   "it must be created by the owner first — this gate never creates it")
    else:
        rules = env.get("protection_rules") or []
        reviewers = [r for r in rules if isinstance(r, dict) and r.get("type") == "required_reviewers"]
        if not reviewers or not any(r.get("reviewers") for r in reviewers):
            why.append(f"environment {ENVIRONMENT} has no required reviewers")
        dbp = env.get("deployment_branch_policy")
        if not isinstance(dbp, dict) or dbp.get("custom_branch_policies") is not True:
            why.append(f"environment {ENVIRONMENT} is not restricted by a custom deployment-branch policy")
        else:
            names = sorted((p.get("name"), p.get("type", "branch"))
                           for p in ((policies or {}).get("branch_policies") or []) if isinstance(p, dict))
            if names != [("main", "branch")]:
                why.append(f"deployment-branch policy of {ENVIRONMENT} must allow exactly the branch main "
                           f"(found {len(names)} rule(s))")
    return (not why), why


def _get(url: str, token: str) -> Tuple[int, Optional[Dict[str, Any]]]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:              # noqa: S310 — fixed https host
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001 — network/JSON failure: fail closed
        return 0, None


def main() -> int:
    ref, ready = os.environ.get("GATE_REF", ""), os.environ.get("GATE_READY", "")
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    env_status, env, policies = 0, None, None
    if ref == MAIN_REF and ready == READY_VALUE and repo:       # do not even query otherwise
        base = f"{api}/repos/{repo}/environments/{ENVIRONMENT}"
        env_status, env = _get(base, token)
        if env_status == 200 and isinstance(env, dict) and \
                (env.get("deployment_branch_policy") or {}).get("custom_branch_policies") is True:
            _, policies = _get(base + "/deployment-branch-policies", token)
    ok, why = evaluate(ref, ready, env_status, env, policies)
    if not ok:
        for w in why:
            print(f"::error::R-161 staging gate refused: {w}")
        print("R-161 staging gate: CLOSED — the live job will not run (see docs/governance/OPERATIONS.md, R-161).")
        return 1
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"environment={ENVIRONMENT}\n")
    print(f"R-161 staging gate: OPEN — {ENVIRONMENT} exists, has required reviewers, deploys only from main; "
          "owner declared it ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
