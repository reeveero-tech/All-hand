#!/usr/bin/env python3
"""R-154 (D-066): status vocabulary + evidence-sufficiency check for ROADMAP.

Usage: python scripts/check_status_evidence.py [REPO_ROOT]   (default: .)

Exit codes: 0 = consistent; 1 = violations (all listed); 2 = git/IO error.

Implements docs/governance/STATUS_DEFINITIONS.md §3:
  1 vocabulary (six statuses only; exactly one status line per item)
  2 required evidence fields per status (M8 items, R-151+), with formats
  3 done/accepted: merge_sha reachable from HEAD, subject
    "Merge pull request #<pr> from", second parent == tested_sha
  4 acceptance log really contains merge_sha, tested_sha, ci_run, "success";
    tests_log really contains the exact pytest summary
  5 skipped/untested non-empty ("none" forbidden when skipped/deselected > 0)
  6 accepted: accepted_by=owner and the decision heading exists
  7 blocked: blocker exists in OPEN_QUESTIONS/RISKS
  8 awaiting_merge/in_progress whose PR is already merged (in a commit other
    than HEAD itself) = contradiction
  9 PROJECT_STATE "M8 status" table == ROADMAP statuses
 10 order: no started M8 item after a still-planned earlier M8 item
 10b cross-program deps: a started item's declared dependencies
    ("- الاعتماديات" line) must be done/accepted — machine-enforced.
    For M9 items the line is mandatory and parsed strictly: exactly one
    line, tag matching the item, value = the exact 'لا شيء' or R-NNN
    tokens only; anything else is rejected, not ignored.
Program scoping (M8→M9 transition, D-102): rules 2-8 apply to every item
R-151+ regardless of program. Rules 9-10 are scoped per program: M8 =
R-151..R-184 (R-185/R-186 are governance entries, not roadmap items),
M9 = R-187+. Cross-program order is not enforced — M9 items document
their M8 dependencies per item instead. Each program with roadmap items
must have its own "## حالة بنود <program>" table in PROJECT_STATE.
Reads repository files and git only — never PR text or the GitHub API.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

STATUSES = ("planned", "in_progress", "blocked", "awaiting_merge", "done", "accepted")
M8_FIRST = 151
M8_LAST = 186  # R-185/R-186 are governance entries, not roadmap items
M9_FIRST = 187
PROGRAMS = (("M8", M8_FIRST, M8_LAST), ("M9", M9_FIRST, None))
FIELD_LINE = "- أدلة الحالة (R-154):"
DEPS_LINE_RE = re.compile(r"^- الاعتماديات \((R-\d+)\): (.+)$", re.M)
DEPS_TOKEN_RE = re.compile(r"^R-\d{3}[0-9a-z]*$")
STATE_TABLE_HEADING = "## حالة بنود M8"
REQUIRED = {
    "planned": (),
    "in_progress": ("branch",),
    "blocked": ("blocker", "reason"),
    "awaiting_merge": ("branch", "pr"),
    "done": ("pr", "tested_sha", "merge_sha", "ci_run", "ci_conclusion", "tests",
             "tests_log", "skipped", "untested", "evidence"),
}
REQUIRED["accepted"] = REQUIRED["done"] + ("accepted_by", "decision")
HEAD_RE = re.compile(r"^#{3,4} (R-(\d{3})(?:[a-z][a-z0-9]*)?)\b.*$", re.M)
STATUS_RE = re.compile(r"^- الحالة: \*\*(.+?)\*\*", re.M)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TESTS_RE = re.compile(r"^(\d+) passed, (\d+) skipped, (\d+) deselected$")
NONE_RE = re.compile(r"^(none|لا شيء|لا يوجد|-)\s*$", re.I)


class GitError(Exception):
    pass


def _git(root: str, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise GitError(r.stderr.strip())
    return r.stdout


def parse_items(road: str) -> list[dict]:
    heads = list(HEAD_RE.finditer(road))
    items = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(road)
        block = road[m.start():end]
        fields: dict[str, str] = {}
        lines = [ln for ln in block.splitlines() if ln.startswith(FIELD_LINE)]
        for ln in lines[:1]:
            for part in ln[len(FIELD_LINE):].split(" | "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
        deps_lines = DEPS_LINE_RE.findall(block)  # [(tag, value), ...]
        items.append({"id": m.group(1), "num": int(m.group(2)),
                      "statuses": STATUS_RE.findall(block), "fields": fields,
                      "field_lines": len(lines), "deps": [],
                      "deps_lines": deps_lines})
    return items


def _parse_deps(value: str) -> tuple[list[str], str | None]:
    """Strict deps-line value parse: the exact 'لا شيء' or R-NNN tokens only.
    Returns (deps, bad_token) — bad_token is None when valid, '' when empty,
    or the offending token otherwise."""
    val = value.strip()
    if val == "لا شيء":
        return [], None
    deps: list[str] = []
    for tok in re.split(r"[،,]", val):
        tok = tok.strip()
        if not tok or not DEPS_TOKEN_RE.match(tok):
            return [], tok
        deps.append(tok)
    return deps, None


def parse_state_table(state: str, heading: str) -> dict[str, str] | None:
    i = state.find(heading)
    if i < 0:
        return None
    j = state.find("\n## ", i + 5)
    sec = state[i: j if j > 0 else len(state)]
    return dict(re.findall(r"^\| (R-\d{3}[a-z0-9]*) \| `?(\w+)`? \|", sec, re.M))


def _program(num: int) -> str | None:
    for name, lo, hi in PROGRAMS:
        if num >= lo and (hi is None or num <= hi):
            return name
    return None


def _read(root: str, rel: str) -> str | None:
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def check(root: str = ".") -> list[str]:
    road = _read(root, "docs/governance/ROADMAP.md")
    state = _read(root, "docs/governance/PROJECT_STATE.md")
    if road is None or state is None:
        raise OSError("ROADMAP.md or PROJECT_STATE.md missing")
    decisions = _read(root, "docs/governance/DECISIONS.md") or ""
    blockers = (_read(root, "docs/governance/OPEN_QUESTIONS.md") or "") + \
        (_read(root, "docs/governance/RISKS.md") or "")
    items = parse_items(road)
    v: list[str] = []
    head = _git(root, "rev-parse", "HEAD").strip()
    merges: dict[str, str] = {}
    for line in _git(root, "log", "--format=%H %s", "HEAD").splitlines():
        sha, _, subj = line.partition(" ")
        m = re.match(r"Merge pull request #(\d+) from", subj)
        if m and m.group(1) not in merges:
            merges[m.group(1)] = sha

    seen: dict[str, int] = {}
    for it in items:
        seen[it["id"]] = seen.get(it["id"], 0) + 1
    for rid, n in seen.items():
        if n > 1:
            v.append(f"{rid}: duplicate item heading ({n}x)")

    for it in items:
        rid, sts = it["id"], it["statuses"]
        if len(sts) != 1:
            v.append(f"{rid}: expected exactly one '- الحالة:' line, found {len(sts)}")
            continue
        st = sts[0]
        it["status"] = st
        if st not in STATUSES:
            v.append(f"{rid}: invalid status '{st}' (allowed: {', '.join(STATUSES)};"
                     " production-ready is a separate readiness decision, not a status)")
            continue
        if it["num"] < M8_FIRST:
            continue  # legacy item: vocabulary only (STATUS_DEFINITIONS §3 limits)
        f = it["fields"]
        if it["field_lines"] > 1:
            v.append(f"{rid}: more than one '{FIELD_LINE}' line")
        for k in REQUIRED[st]:
            if not f.get(k):
                v.append(f"{rid} [{st}]: missing evidence field '{k}'")
        if "pr" in f and not re.fullmatch(r"#\d+", f["pr"]):
            v.append(f"{rid}: pr must look like #N, got '{f['pr']}'")
        if st == "blocked" and f.get("blocker"):
            b = f["blocker"]
            if not re.fullmatch(r"(Q|RISK)-\d{3}", b) or not re.search(
                    rf"^#{{2,3}} {re.escape(b)}\b", blockers, re.M):
                v.append(f"{rid}: blocker '{b}' not found as a heading in OPEN_QUESTIONS/RISKS")
        if st in ("awaiting_merge", "in_progress") and re.fullmatch(r"#\d+", f.get("pr", "")):
            n = f["pr"][1:]
            if n in merges and merges[n] != head:
                v.append(f"{rid}: CONTRADICTION — PR #{n} is already merged "
                         f"({merges[n][:7]}) but status is still {st}; record acceptance (done)")
        if st in ("done", "accepted") and all(f.get(k) for k in REQUIRED[st]):
            v += _check_done(root, rid, st, f, decisions)

    # 9: PROJECT_STATE table sync (per program, D-102)
    by_prog: dict[str, list[dict]] = {}
    for it in items:
        if it["num"] >= M8_FIRST:
            p = _program(it["num"])
            if p:
                by_prog.setdefault(p, []).append(it)
    for pname, _lo, _hi in PROGRAMS:
        its = by_prog.get(pname, [])
        table = parse_state_table(state, f"## حالة بنود {pname}")
        if its:
            if table is None:
                v.append(f"PROJECT_STATE: section '## حالة بنود {pname}' missing")
                continue
            road_st = {it["id"]: it.get("status") for it in its}
            for rid, st in road_st.items():
                if rid not in table:
                    v.append(f"PROJECT_STATE table: {rid} missing (ROADMAP={st})")
                elif table[rid] != st:
                    v.append(f"PROJECT_STATE table: {rid}={table[rid]} but ROADMAP={st}")
            for rid in table:
                if rid not in road_st:
                    v.append(f"PROJECT_STATE table: {rid} not an {pname} item in ROADMAP")
        elif table:
            # a program table whose rows reference no ROADMAP items (stale/
            # surplus) must be flagged, not silently ignored
            for rid in table:
                v.append(f"PROJECT_STATE table: {rid} not an {pname} item in ROADMAP")

    # 10: order (per program — cross-program order is not enforced; M9 items
    # document their M8 dependencies per item, machine-enforced in rule 10b)
    for its in by_prog.values():
        first_planned = None
        for it in sorted(its, key=lambda x: x["num"]):
            st = it.get("status")
            if st == "planned" and first_planned is None:
                first_planned = it["id"]
            elif st in ("in_progress", "awaiting_merge", "done", "accepted") and first_planned:
                v.append(f"{it['id']}: is {st} while earlier item {first_planned} is still planned")

    # 10b: cross-program dependencies are machine-enforced (D-102): a started
    # item requires every declared dependency ("- الاعتماديات (R-NNN):" line)
    # to be done/accepted. For M9 items the line is MANDATORY and parsed
    # STRICTLY: exactly one line, tag matching the containing item, and a
    # value that is either the exact 'لا شيء' or R-NNN tokens only — anything
    # else (prose, other prefixes, 4-digit misparses) is rejected, not ignored.
    st_by_id = {it["id"]: it.get("status") for it in items}
    for it in items:
        rid = it["id"]
        if it["num"] >= M9_FIRST:
            lines = it["deps_lines"]
            if len(lines) > 1:
                v.append(f"{rid}: more than one '- الاعتماديات' line")
            if not lines:
                v.append(f"{rid}: M9 items must declare dependencies via the "
                         "'- الاعتماديات (R-NNN):' line (explicit 'لا شيء' if none)")
            else:
                tag, val = lines[0]
                if tag != rid:
                    v.append(f"{rid}: deps line tag mismatch — tagged {tag} "
                             f"but sits inside {rid} (tag must match the item)")
                deps, bad = _parse_deps(val)
                if bad is None:
                    it["deps"] = deps
                else:
                    v.append(f"{rid}: deps line is empty/malformed — invalid "
                             f"token '{bad}'; the value must be the exact "
                             "'لا شيء' (بالضبط) or a list of R-NNN ids only")
        for dep in it.get("deps", []):
            if dep not in st_by_id:
                v.append(f"{rid}: dependency {dep} is not a ROADMAP item "
                         "(typo/nonexistent — rejected at any status)")
        if it.get("status") in ("in_progress", "awaiting_merge", "done", "accepted"):
            for dep in it.get("deps", []):
                dst = st_by_id.get(dep)
                if dst not in ("done", "accepted"):
                    v.append(f"{rid}: dependency {dep} is {dst or 'missing'}"
                             " — must be done/accepted before this item starts")
    return v


def _check_done(root, rid, st, f, decisions) -> list[str]:
    v = []
    pr, ts, ms, run = f["pr"][1:], f["tested_sha"], f["merge_sha"], f["ci_run"]
    if f["ci_conclusion"] != "success":
        v.append(f"{rid}: ci_conclusion must be 'success', got '{f['ci_conclusion']}'")
    if not re.fullmatch(r"\d+", run):
        v.append(f"{rid}: ci_run must be a numeric run id, got '{run}'")
    for k in ("tested_sha", "merge_sha"):
        if not SHA_RE.match(f[k]):
            v.append(f"{rid}: {k} must be a full 40-hex SHA, got '{f[k]}'")
    if SHA_RE.match(ms):
        try:
            _git(root, "merge-base", "--is-ancestor", ms, "HEAD")
            subj = _git(root, "log", "-1", "--format=%s", ms).strip()
            if not subj.startswith(f"Merge pull request #{pr} from"):
                v.append(f"{rid}: merge_sha {ms[:7]} subject is '{subj[:60]}', not a merge of PR #{pr}")
            p2 = _git(root, "rev-parse", f"{ms}^2").strip()
            if p2 != ts:
                v.append(f"{rid}: merge_sha^2 is {p2[:7]} but tested_sha is {ts[:7]}")
        except GitError as exc:
            v.append(f"{rid}: merge_sha {ms[:7]} not a commit reachable from HEAD ({exc})")
    paths = [p.strip() for p in f["evidence"].split(",") if p.strip()]
    for p in paths:
        if not p.startswith("docs/audit/") or _read(root, p) is None:
            v.append(f"{rid}: evidence path missing or outside docs/audit/: {p}")
    acc = _read(root, paths[0]) if paths else None
    if acc is not None:
        for needle, label in ((ms, "merge_sha"), (ts, "tested_sha"), (run, "ci_run"),
                              ("success", "'success'")):
            if needle not in acc:
                v.append(f"{rid}: acceptance log {paths[0]} does not contain {label}"
                         " (a file existing is not sufficient evidence)")
    tm = TESTS_RE.match(f["tests"])
    if not tm:
        v.append(f"{rid}: tests must be 'N passed, S skipped, D deselected', got '{f['tests']}'")
    tl = _read(root, f["tests_log"])
    if tl is None:
        v.append(f"{rid}: tests_log not found: {f['tests_log']}")
    elif f["tests"] not in tl:
        v.append(f"{rid}: tests_log {f['tests_log']} does not contain '{f['tests']}'")
    if tm and (int(tm.group(2)) or int(tm.group(3))) and NONE_RE.match(f["skipped"]):
        v.append(f"{rid}: skipped/deselected > 0 but 'skipped' says none — list them with reasons")
    if NONE_RE.match(f["untested"]):
        v.append(f"{rid}: 'untested' must state the untested limits explicitly (or why none)")
    if st == "accepted":
        if f["accepted_by"] != "owner":
            v.append(f"{rid}: accepted_by must be 'owner' (agents cannot self-accept)")
        if not re.search(rf"^## {re.escape(f['decision'])}\b", decisions, re.M):
            v.append(f"{rid}: decision {f['decision']} not found in DECISIONS.md")
    return v


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "."
    try:
        violations = check(root)
    except (GitError, OSError) as exc:
        print(f"::error::status-evidence check could not run: {exc}")
        return 2
    if not violations:
        print("Status evidence check passed (STATUS_DEFINITIONS §3, rules 1-10b).")
        return 0
    for msg in violations:
        print(f"::error::{msg}")
    print(f"::error::Status evidence check failed: {len(violations)} violation(s) (D-066).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
