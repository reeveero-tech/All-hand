#!/usr/bin/env python3
"""R-071 / D-164: the blocking full `npm audit` in web/, with ONE owner-approved,
dated exception.

Reads `npm audit --json` (stdin or a file) and fails (exit 1) when:
  * any high/critical advisory is present that is not in ALLOW;
  * an allowed advisory is past its expiry date (the exception ends by itself);
  * an allowed advisory hits a package outside its allowed list.
Packages that are vulnerable only THROUGH another package (``via`` holds a
package name, not an advisory) are judged by the advisories they lead to.

Not touched by this exception: the production-only audit step
(`npm audit --omit=dev --audit-level=high`) stays strict, and pip-audit too.

An allowed advisory that no longer appears is reported, so the exception is
removed as soon as an upstream fix lands.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

BLOCKING = {"high", "critical"}

# Owner decision 2026-11-18 (D-164): picomatch has no published fix
# (<= 4.0.7 affected; 4.0.7 is the latest release). Dev-only: vite and its
# globbing helpers at build time, on patterns written by us.
ALLOW = {
    "GHSA-fc5q-39xc-vjpf": {
        "expires": dt.date(2026, 12, 18),
        "packages": {"picomatch"},
        "why": "picomatch ReDoS via repeated extglobs; no upstream fix; dev-only (vite build)",
    },
}


def advisories(report: dict):
    """(ghsa_id, package, severity) for every advisory object in the report."""
    out = []
    for pkg, v in (report.get("vulnerabilities") or {}).items():
        for via in v.get("via") or []:
            if isinstance(via, dict):
                url = via.get("url") or ""
                gid = url.rstrip("/").rsplit("/", 1)[-1] if "GHSA-" in url else str(via.get("source"))
                out.append((gid, via.get("name") or pkg, (via.get("severity") or v.get("severity") or "").lower()))
    return out


def check(report: dict, today: dt.date | None = None):
    today = today or dt.date.today()
    errors, notes = [], []
    seen = set()
    for gid, pkg, sev in advisories(report):
        if sev not in BLOCKING:
            continue
        rule = ALLOW.get(gid)
        if rule is None:
            errors.append(f"{sev} advisory {gid} in {pkg} — not excepted")
            continue
        seen.add(gid)
        if today > rule["expires"]:
            errors.append(f"exception for {gid} expired on {rule['expires']} — fix, or the owner renews it")
        elif pkg not in rule["packages"]:
            errors.append(f"{gid} hit {pkg}, outside its exception ({sorted(rule['packages'])})")
        else:
            notes.append(f"excepted until {rule['expires']}: {gid} in {pkg} ({rule['why']})")
    for gid in sorted(set(ALLOW) - seen):
        notes.append(f"exception {gid} is no longer needed — remove it from scripts/check_npm_audit.py")
    return errors, notes


def main(argv):
    raw = open(argv[1], encoding="utf-8").read() if len(argv) > 1 else sys.stdin.read()
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"::error::npm audit output is not JSON: {e}")
        return 1
    if "vulnerabilities" not in report:
        print("::error::npm audit output has no 'vulnerabilities' (npm error?)")
        return 1
    errors, notes = check(report)
    for n in notes:
        print(f"::warning::{n}")
    for e in errors:
        print(f"::error::{e}")
    meta = (report.get("metadata") or {}).get("vulnerabilities") or {}
    print(f"npm audit (full): {meta} — {len(errors)} blocking, {len(notes)} note(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
