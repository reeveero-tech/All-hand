#!/usr/bin/env python3
"""R-158 (D-070): reproducible per-file test baseline at a known Git SHA.

Runs a fixed command set for every group of tracked files on a CLEAN checkout
of ``--sha`` (a fresh ``git worktree`` it creates, or ``--checkout`` = an
existing clean clone at that SHA), and writes:

* ``<out>/TEST_BASELINE_<sha7>.json``  machine output (deterministic: no
  timestamps / durations / temp paths; the only per-run field is ``raw_log``)
* ``<out>/TEST_BASELINE_<sha7>.md``    human report, rendered FROM the JSON
* ``<out>/TEST_BASELINE_GROUPS_<sha7>.csv`` one row per group step, columns of
  ``templates/test_evidence_template.csv`` + explicit counts
* ``<out>/TEST_BASELINE_FILES_<sha7>.csv``  one row per tracked file
* ``--raw-log``: every command's full output, in sections ``=== [<id>] ===``

Every tracked file is assigned to exactly one group (ordered rules, first
match wins); an unmatched file is a self-check failure, never silently
dropped. A group that cannot run here is reported ``not-run`` with the reason
(and, where it exists, the CI job of the SAME SHA) — never as passed.

Usage (see docs/governance/roadmaps/revival-scale/baseline/README.md):
  python3 scripts/build_test_baseline.py --repo . --sha <sha> --out <dir> \\
      --raw-log <file> [--checkout <clean clone>] [--node-bin DIR] \\
      [--pg-bin DIR --postgrest BIN] [--ci-json FILE] [--no-network]
"""
from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import io
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from importlib import metadata
from typing import Dict, List, Optional, Tuple

SCHEMA = "agos.test_baseline.v1"
TEMPLATE_COLUMNS = [
    "task_id", "test_id", "category", "target_component", "environment", "commit_sha",
    "tool_version", "command_or_config_path", "input_dataset_version", "duration_seconds",
    "accepted_count", "completed_count", "failed_count", "skipped_count", "deselected_count",
    "p50_ms", "p95_ms", "p99_ms", "throughput_per_second", "max_memory_mb", "max_cpu_percent",
    "cost_estimate", "result", "evidence_path", "known_limitations", "reviewer", "date_utc",
]
EXTRA_COLUMNS = ["group_id", "evidence_kind", "exit_code", "passed", "xfailed", "xpassed", "errors"]
COUNT_KEYS = ("passed", "failed", "skipped", "xfailed", "xpassed", "deselected", "errors")

# ------------------------------------------------------------------ groups --
# (id, title, category, rule regex list, rationale). Order matters: first match.
GROUPS: List[Dict] = [
    {"id": "tests-python", "title": "Python test suite (tests/)", "category": "unit/contract/integration/live",
     "rules": [r"^tests/.*\.py$"],
     "rationale": "pytest is the project's test runner (pytest.ini); the same suite CI's job 'test' runs, executed "
                  "twice: default runtime (real) and AGOS_AGENT_IMPL=specialists, because CI only runs 'real'."},
    {"id": "py-specialists", "title": "1,000 specialist modules + templates", "category": "compile/import/contract",
     "rules": [r"^agent_civilization/agents/specialists/.*\.py$"],
     "rationale": "Generated thin shims + 20 templates: compile + import every module (each is importable by name) "
                  "and exercise through the suite in specialists mode; per-file unit tests would test the generator twice."},
    {"id": "py-runtime", "title": "Python runtime packages + server", "category": "compile/import/lint/unit/integration",
     "rules": [r"^agent_civilization/.*\.py$", r"^agos_kernel/.*\.py$", r"^server\.py$", r"^run\.py$"],
     "rationale": "Live code: compile, import each module, ruff with CI's fatal rule set, and line coverage from the "
                  "suite (which file the tests actually execute)."},
    {"id": "sql-storage", "title": "SQL schema + live storage stack", "category": "schema/integration/live",
     "rules": [r"\.sql$", r"^scripts/r042/"],
     "rationale": "SQL is only meaningful applied to the engine Supabase runs: a fresh PostgreSQL + PostgREST "
                  "(scripts/r042/run_local_stack.sh, the script CI's job 'storage' runs) applies all 4 SQL files "
                  "with ON_ERROR_STOP=1 and runs tests/test_r042_live_storage.py over HTTP."},
    {"id": "py-scripts", "title": "CLI and probe scripts", "category": "compile/lint/smoke",
     "rules": [r"^scripts/.*\.py$"],
     "rationale": "Compile + ruff (fatal set; not in CI's lint scope) + coverage from the suite (governance/inventory/"
                  "map CLIs are exercised by tests/test_r152..r156). Probes needing a live server are not executed here."},
    {"id": "shell", "title": "Shell scripts", "category": "syntax",
     "rules": [r"\.sh$"],
     "rationale": "bash -n syntax check; run_local_stack.sh is additionally executed by sql-storage."},
    {"id": "frontend-web", "title": "web/ (deployed React UI)", "category": "build/unit/contract",
     "rules": [r"^web/"],
     "rationale": "npm ci --engine-strict, vite build, node --test (API contract + UI consent tests) — CI job 'build'."},
    {"id": "frontend-root", "title": "Root app src/ (Q-002: deployment undecided)", "category": "lint/build/type",
     "rules": [r"^src/", r"^public/", r"^(package\.json|package-lock\.json|tsconfig\.json|vite\.config\.ts|"
               r"eslint\.config\.js|components\.json)$"],
     "rationale": "npm ci, eslint --max-warnings=0, vite build (CI job 'build'), then tsc --noEmit (NOT in CI). Tested and built; "
                  "its deployment is NOT decided (Q-002)."},
    {"id": "deploy-ci", "title": "Docker, Render, CI workflows", "category": "config/static/ci",
     "rules": [r"^Dockerfile$", r"^\.dockerignore$", r"^render\.yaml$", r"^\.github/"],
     "rationale": "YAML parse + R-156 map self-check (ASGI targets, COPY sources, workflow script references exist). "
                  "Docker image build/run and the workflows themselves are proven only by CI on the same SHA."},
    {"id": "deps-config", "title": "Dependency locks and tool config", "category": "config",
     "rules": [r"^requirements[^/]*\.(in|lock|txt)$", r"^pytest\.ini$", r"^\.env\.example$", r"^\.gitignore$"],
     "rationale": "Locks must be fully hash-pinned (pip --require-hashes in CI); pytest.ini must parse; "
                  ".env.example must be KEY=VALUE lines. The installed set is what this baseline ran on."},
    {"id": "governance-data", "title": "Governance templates, specs, inventories, maps", "category": "schema",
     "rules": [r"^docs/governance/.*\.(csv|json)$"],
     "rationale": "Parse every CSV/JSON; CSV rows must match the header width; the tool-registry seed is checked "
                  "against the required keys/types of its schema; governance gates run on the snapshot."},
    {"id": "evidence-scripts", "title": "Historical audit scripts (docs/audit/*)", "category": "compile",
     "rules": [r"^docs/audit/.*\.py$"],
     "rationale": "One-off evidence scripts of past batches: compile only — they target past live environments."},
    {"id": "evidence-data", "title": "Audit evidence data (json/jsonl/txt/csv)", "category": "integrity",
     "rules": [r"^docs/audit/.*\.(json|jsonl|txt|csv)$"],
     "rationale": "Recorded evidence, not executable: JSON/JSONL must parse, text must be UTF-8."},
    {"id": "benchmark-data", "title": "Versioned benchmark datasets (benchmarks/)", "category": "integrity/schema",
     "rules": [r"^benchmarks/.*\.(json|jsonl)$"],
     "rationale": "R-169: JSON/JSONL must parse; tests/test_r169_benchmark.py regenerates the dataset byte-for-byte, "
                  "verifies the manifest hashes, the case schema, split disjointness and the freeze."},
    {"id": "evidence-logs", "title": "Raw audit logs (docs/audit/**/*.log)", "category": "integrity",
     "rules": [r"^docs/audit/.*\.log$"],
     "rationale": "Raw logs are evidence records (never edited, R-152): UTF-8 readability only; content is the evidence."},
    {"id": "binary-assets", "title": "Binary assets (screenshots)", "category": "integrity",
     "rules": [r"\.png$"],
     "rationale": "Images cannot be tested; PNG signature check proves the blob is an intact PNG."},
    {"id": "docs", "title": "Markdown documentation", "category": "links",
     "rules": [r"\.md$"],
     "rationale": "Relative links must resolve to a tracked path at the SHA (anchors/URLs not checked)."},
]


def assign(path: str) -> Optional[str]:
    for g in GROUPS:
        if any(re.search(r, path) for r in g["rules"]):
            return g["id"]
    return None


# test file -> category (TEST_MATRIX_AR.md: unit / contract / integration / live)
TEST_CATEGORY = {
    "live": ["test_r042_live_storage.py", "test_network_live.py"],
    "contract": ["test_web_api_contract.py", "test_r101_web_auth_contract.py", "test_storage_payloads.py",
                 "test_agents_per_spec.py", "test_specialists_package.py", "test_specialist_runtime.py",
                 "test_real_dispatch_audit.py", "test_r122_specialization_coverage.py",
                 "test_r157_contract_parity.py"],
    "integration": ["test_api.py", "test_bug01_external_without_agent.py", "test_bug02_chat_status_consistency.py",
                    "test_bug04_task_by_id_from_storage.py", "test_bug05_startup_agent_count.py",
                    "test_civilization_routes.py", "test_civilization_scale.py", "test_e2e_scenarios.py",
                    "test_routing_pipeline.py", "test_r082_storage_outcomes.py", "test_r083_metrics_middleware.py",
                    "test_r104_data_worker.py", "test_r106_chat_routing.py", "test_r125_self_developer_cycle.py",
                    "test_r131_keyless_admission.py", "test_r132_pools.py", "test_r149_security_qa_gaps.py",
                    "test_sec01_external_redirect.py", "test_sec02_tenant_isolation.py",
                    "test_sec03_production_startup.py", "test_sec04_sse_broadcast_bounds.py",
                    "test_sec05_dns_rebinding.py", "test_sec06_public_llm_consent.py", "test_security.py",
                    "test_network_isolation.py"],
}


def test_category(path: str) -> str:
    base = posixpath.basename(path)
    if base in ("__init__.py", "conftest.py"):
        return "support"
    for cat, names in TEST_CATEGORY.items():
        if base in names:
            return cat
    return "unit"


# ----------------------------------------------------------------- parsers --
_SUMMARY = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?|warnings?)\b")


def parse_pytest_summary(text: str) -> Dict[str, int]:
    """Counts from pytest's final summary line ('== 3 failed, 10 passed ... in 1.2s ==')."""
    counts = {k: 0 for k in COUNT_KEYS}
    lines = [ln for ln in text.splitlines() if re.search(r"\bin [\d.]+s\b", ln) and _SUMMARY.search(ln)]
    if not lines:
        return counts
    for n, kind in _SUMMARY.findall(lines[-1]):
        if kind.startswith("warning"):
            continue
        counts["errors" if kind.startswith("error") else kind] = int(n)
    return counts


def parse_pytest_failures(text: str) -> List[str]:
    return sorted({m.group(2) for m in re.finditer(r"^(FAILED|ERROR) (\S+)", text, re.M)})


def parse_junit_per_file(xml_text: str) -> Dict[str, Dict[str, int]]:
    per: Dict[str, Dict[str, int]] = {}
    root = ET.fromstring(xml_text)
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        parts = cls.split(".")
        mod = parts[:2] if len(parts) >= 2 else parts
        path = "/".join(mod) + ".py"
        c = per.setdefault(path, {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "errors": 0})
        child = {ch.tag: ch for ch in tc}
        if "failure" in child:
            c["failed"] += 1
        elif "error" in child:
            c["errors"] += 1
        elif "skipped" in child:
            if (child["skipped"].get("type") or "") == "pytest.xfail":
                c["xfailed"] += 1
            else:
                c["skipped"] += 1
        else:
            c["passed"] += 1
    return per


def parse_tap(text: str) -> Dict[str, int]:
    counts = {k: 0 for k in COUNT_KEYS}
    for key, out in (("pass", "passed"), ("fail", "failed"), ("skipped", "skipped"), ("todo", "xfailed")):
        m = re.search(rf"^# {key} (\d+)$", text, re.M)
        if m:
            counts[out] = int(m.group(1))
    return counts


_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def md_broken_links(path: str, text: str, tracked: set, tracked_dirs: set) -> List[str]:
    bad = []
    for target in _LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        t = target.split("#", 1)[0].split("?", 1)[0]
        if not t:
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), t)) if not t.startswith("/") else t.lstrip("/")
        if resolved not in tracked and resolved.rstrip("/") not in tracked_dirs:
            bad.append(target)
    return bad


def check_schema_subset(schema: Dict, doc: Dict) -> List[str]:
    """Required keys + top-level JSON types of a JSON-Schema (no external lib in the locks)."""
    types = {"string": str, "integer": int, "number": (int, float), "object": dict, "array": list, "boolean": bool}
    errs = [f"missing required key {k!r}" for k in schema.get("required", []) if k not in doc]
    for k, spec in (schema.get("properties") or {}).items():
        t = spec.get("type") if isinstance(spec, dict) else None
        if k in doc and isinstance(t, str) and t in types and not isinstance(doc[k], types[t]):
            errs.append(f"{k!r} is not {t}")
    return errs


# ----------------------------------------------------------------- running --
class Runner:
    def __init__(self, wt: str, raw_log, env: Dict[str, str]):
        self.wt, self.raw, self.env = wt, raw_log, env

    def run(self, sec: str, argv: List[str], cwd: str = "", extra_env: Optional[Dict[str, str]] = None,
            timeout: int = 1800) -> Tuple[int, str]:
        env = dict(self.env)
        env.update(extra_env or {})
        where = os.path.join(self.wt, cwd) if cwd else self.wt
        try:
            p = subprocess.run(argv, cwd=where, env=env, capture_output=True, text=True, timeout=timeout)
            code, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, out = 124, f"TIMEOUT after {timeout}s\n{e.stdout or ''}{e.stderr or ''}"
        shown_env = " ".join(f"{k}={v}" for k, v in sorted((extra_env or {}).items()))
        self.raw.write(f"\n=== [{sec}] $ {('(cd ' + cwd + ') ') if cwd else ''}{shown_env + ' ' if shown_env else ''}"
                       f"{' '.join(argv)}\n{out}\n=== [{sec}] exit={code}\n")
        self.raw.flush()
        return code, out


def step(sec, command, exit_code, counts=None, failures=None, kind="local-test", note=""):
    c = {k: 0 for k in COUNT_KEYS}
    c.update(counts or {})
    return {"raw_section": sec, "command": command, "exit_code": exit_code, "counts": c,
            "failures": failures or [], "evidence_kind": kind, "note": note,
            "result": "pass" if exit_code == 0 else ("not-run" if exit_code is None else "fail")}


def tool_versions(node_bin: Optional[str], pg_bin: Optional[str], postgrest: Optional[str]) -> Dict[str, str]:
    def out(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[0]
        except Exception as e:  # noqa: BLE001
            return f"unavailable ({type(e).__name__})"
    v = {"python": platform.python_version(), "implementation": platform.python_implementation(),
         "os": f"{platform.system()} {platform.machine()}"}
    for name in ("pytest", "pytest-cov", "pytest-asyncio", "coverage", "ruff", "fastapi", "starlette",
                 "pydantic", "httpx", "aiohttp", "PyYAML", "uvicorn"):
        try:
            v[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            v[name] = "not installed"
    nb = (lambda x: os.path.join(node_bin, x)) if node_bin else (lambda x: x)
    v["node"] = out([nb("node"), "--version"])
    v["npm"] = out([nb("npm"), "--version"])
    v["bash"] = out(["bash", "--version"])
    v["git"] = out(["git", "--version"])
    v["postgres"] = out([os.path.join(pg_bin, "postgres"), "--version"]) if pg_bin else "not provided"
    v["postgrest"] = out([postgrest, "--version"]) if postgrest else "not provided"
    return v


def installed_packages() -> List[str]:
    return sorted(f"{d.metadata['Name'].lower()}=={d.version}" for d in metadata.distributions())


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def build(args) -> Tuple[Dict, List[str]]:  # noqa: C901  (one linear pipeline)
    sha = git(args.repo, "rev-parse", args.sha).strip()
    problems: List[str] = []
    tmp = tempfile.mkdtemp(prefix="agos_r158_")
    if args.checkout:
        wt = os.path.abspath(args.checkout)
        how = "existing clean clone (--checkout)"
    else:
        wt = os.path.join(tmp, "wt")
        git(args.repo, "worktree", "add", "--detach", "-q", wt, sha)
        how = "fresh git worktree"
    head = git(wt, "rev-parse", "HEAD").strip()
    dirty = git(wt, "status", "--porcelain", "--untracked-files=all").strip()
    if head != sha:
        problems.append(f"checkout HEAD {head} != {sha}")
    if dirty:
        problems.append("checkout not clean: " + dirty.splitlines()[0])
    tracked = git(wt, "ls-files", "-z").split("\0")
    tracked = sorted(t for t in tracked if t)
    tset = set(tracked)
    tdirs = {posixpath.dirname(t) for t in tracked}
    for t in list(tdirs):
        while t:
            t = posixpath.dirname(t)
            tdirs.add(t)

    env = {"PATH": ((args.node_bin + os.pathsep) if args.node_bin else "") + os.environ.get("PATH", ""),
           "HOME": os.environ.get("HOME", tmp), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
           "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "npm_config_update_notifier": "false",
           "npm_config_fund": "false", "npm_config_audit": "false", "CI": "1", "NO_COLOR": "1"}
    raw = open(args.raw_log, "w", encoding="utf-8")
    gen = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
    raw.write(f"# R-158 test baseline raw log — snapshot {sha} ({how}); generator scripts/build_test_baseline.py "
              f"sha256={gen}\n")
    R = Runner(wt, raw, env)
    py = sys.executable
    files = {p: {"path": p, "group": assign(p) or "UNASSIGNED", "checks": [], "status": "", "detail": ""}
             for p in tracked}
    unassigned = [p for p, f in files.items() if f["group"] == "UNASSIGNED"]
    for p in unassigned:
        problems.append(f"unassigned tracked file: {p}")
    groups = {g["id"]: dict(g, steps=[], gaps=[], files=0) for g in GROUPS}
    for f in files.values():
        if f["group"] in groups:
            groups[f["group"]]["files"] += 1

    def mark(path, check, ok, detail=""):
        f = files[path]
        f["checks"].append(f"{check}={'ok' if ok else 'FAIL'}")
        if not ok:
            f["detail"] = (f["detail"] + "; " if f["detail"] else "") + f"{check}: {detail}"[:300]

    # --- compile every tracked .py (one process, per-file verdict) ---------
    pys = [p for p in tracked if p.endswith(".py")]
    code = ("import json,sys\nres={}\nfor p in json.load(sys.stdin):\n"
            "    try:\n        compile(open(p,'rb').read(), p, 'exec', dont_inherit=True); res[p]='ok'\n"
            "    except Exception as e:\n        res[p]=type(e).__name__+': '+str(e).splitlines()[0][:200]\n"
            "print(json.dumps(res))\n")
    p = subprocess.run([py, "-c", code], cwd=wt, env=env, input=json.dumps(pys), capture_output=True, text=True)
    comp = json.loads(p.stdout or "{}")
    bad = sorted(k for k, v in comp.items() if v != "ok")
    raw.write(f"\n=== [compile] builtin compile(source, path, 'exec') on {len(pys)} tracked .py files (no .pyc written)\n"
              + "".join(f"FAIL {k}: {comp[k]}\n" for k in bad) + f"compiled_ok={len(pys) - len(bad)} failed={len(bad)}\n"
              f"=== [compile] exit={1 if bad else 0}\n")
    for k, v in comp.items():
        mark(k, "compile", v == "ok", v)
    by_group: Dict[str, List[str]] = {}
    for k in pys:
        by_group.setdefault(files[k]["group"], []).append(k)
    for gid, members in by_group.items():
        nbad = sum(1 for m in members if comp.get(m) != "ok")
        groups[gid]["steps"].append(step("compile", f"compile(source, path, 'exec') ({len(members)} files of this group)",
                                         1 if nbad else 0, {"passed": len(members) - nbad, "failed": nbad},
                                         [m for m in members if comp.get(m) != "ok"]))

    # --- import every runtime module --------------------------------------
    mods = []
    for k in pys:
        if files[k]["group"] in ("py-runtime", "py-specialists"):
            m = k[:-3].replace("/", ".")
            mods.append((k, m[:-9] if m.endswith(".__init__") else m))
    code = ("import json,sys,importlib,os\nsys.path.insert(0,os.getcwd())\nres={}\n"
            "for path,mod in json.load(sys.stdin):\n    try:\n        importlib.import_module(mod); res[path]='ok'\n"
            "    except BaseException as e:\n        res[path]=type(e).__name__+': '+str(e).splitlines()[0][:200] if str(e) else type(e).__name__\n"
            "sys.stdout.write('\\n@@JSON@@'+json.dumps(res))\n")
    p = subprocess.run([py, "-c", code], cwd=wt, env=dict(env, AGOS_ENV="test", AGENTS_PER_SPEC="1"),
                       input=json.dumps(mods), capture_output=True, text=True, timeout=900)
    imp = json.loads(p.stdout.split("@@JSON@@")[-1] or "{}") if "@@JSON@@" in p.stdout else {}
    if not imp:
        problems.append("import step produced no result")
    raw.write(f"\n=== [import] AGOS_ENV=test AGENTS_PER_SPEC=1 importlib.import_module on {len(mods)} modules\n"
              + "".join(f"FAIL {k}: {v}\n" for k, v in sorted(imp.items()) if v != "ok")
              + f"imported_ok={sum(v == 'ok' for v in imp.values())} failed={sum(v != 'ok' for v in imp.values())}\n"
              f"=== [import] exit={0 if all(v == 'ok' for v in imp.values()) else 1}\n")
    for gid in ("py-runtime", "py-specialists"):
        members = [k for k, _ in mods if files[k]["group"] == gid]
        for k in members:
            mark(k, "import", imp.get(k) == "ok", imp.get(k, "missing"))
        nbad = sum(1 for k in members if imp.get(k) != "ok")
        groups[gid]["steps"].append(step("import", "importlib.import_module per module (AGOS_ENV=test, AGENTS_PER_SPEC=1)",
                                         1 if nbad else 0, {"passed": len(members) - nbad, "failed": nbad},
                                         [k for k in members if imp.get(k) != "ok"]))

    # --- ruff (fatal set) ---------------------------------------------------
    for gid, targets, note in (("py-runtime", ["server.py", "agent_civilization", "agos_kernel", "tests"], "CI scope (job quality)"),
                               ("py-scripts", ["scripts", "run.py", "docs/audit"], "outside CI's lint scope")):
        argv = ["ruff", "check", "--select", "F,E722", "--output-format", "json", "--no-cache", *targets]
        c, out = R.run(f"ruff:{gid}", argv)
        try:
            diags = json.loads(out[out.index("["):]) if "[" in out else []
        except ValueError:
            diags = []
        failing = sorted({d["filename"].replace(wt + "/", "") for d in diags})
        for fp in failing:
            if fp in files:
                mark(fp, "ruff", False, "; ".join(f"{d['code']} l{d['location']['row']}" for d in diags
                                                  if d["filename"].replace(wt + "/", "") == fp)[:200])
        groups[gid]["steps"].append(step(f"ruff:{gid}", " ".join(argv[:-len(targets)] + targets), c,
                                         {"failed": len(diags)}, failing, note=f"{note}; failed = diagnostics"))

    # --- pytest: real + specialists (+ coverage on real) --------------------
    cov_json = os.path.join(tmp, "cov.json")
    per_mode = {}
    for mode, extra in (("real", {}), ("specialists", {"AGOS_AGENT_IMPL": "specialists"})):
        junit = os.path.join(tmp, f"junit_{mode}.xml")
        argv = [py, "-m", "pytest", "tests/", "-q", "-rfE", "-p", "no:cacheprovider", f"--junitxml={junit}",
                "-o", "junit_family=xunit1"]
        if mode == "real":
            argv += ["--cov=.", f"--cov-report=json:{cov_json}", "--cov-report="]
        c, out = R.run(f"pytest:{mode}", argv, extra_env=extra, timeout=2400)
        counts = parse_pytest_summary(out)
        fails = parse_pytest_failures(out)
        per_mode[mode] = parse_junit_per_file(open(junit, encoding="utf-8").read()) if os.path.exists(junit) else {}
        shown = " ".join(a if not a.startswith(("--junitxml", "--cov-report=json")) else a.split("=")[0] + "=<tmp>"
                         for a in argv[1:])
        groups["tests-python"]["steps"].append(step(
            f"pytest:{mode}", ("AGOS_AGENT_IMPL=specialists " if extra else "") + "python " + shown, c, counts, fails,
            note="default runtime (CI job 'test' runs this)" if mode == "real" else
                 "NOT run by CI at this SHA — local only"))
    for path, f in files.items():
        if f["group"] == "tests-python" and path in per_mode.get("real", {}):
            for mode in ("real", "specialists"):
                pc = per_mode[mode].get(path)
                if pc:
                    ok = pc["failed"] == 0 and pc["errors"] == 0
                    mark(path, f"pytest[{mode}] {pc['passed']}p/{pc['failed']}f/{pc['skipped']}s/{pc['xfailed']}x", ok,
                         f"{pc['failed']} failed in {mode}")
    cov = {}
    if os.path.exists(cov_json):
        for fp, d in json.load(open(cov_json, encoding="utf-8")).get("files", {}).items():
            cov[fp.replace(wt + "/", "")] = d["summary"]["percent_covered"]
    for path, f in files.items():
        if path.endswith(".py") and f["group"] in ("py-runtime", "py-specialists", "py-scripts", "sql-storage"):
            f["coverage_real"] = round(cov[path], 1) if path in cov else 0.0
            f["checks"].append(f"coverage[real]={f['coverage_real']}%")

    # --- network (external) ---------------------------------------------------
    if args.no_network:
        groups["tests-python"]["steps"].append(step("pytest:network", "python -m pytest tests/ -m network", None,
                                                    kind="not-run", note="--no-network"))
    else:
        argv = [py, "-m", "pytest", "tests/", "-m", "network", "-q", "-rfE", "-p", "no:cacheprovider"]
        c, out = R.run("pytest:network", argv, timeout=600)
        groups["tests-python"]["steps"].append(step("pytest:network", "python -m pytest tests/ -m network -q -rfE",
                                                    c, parse_pytest_summary(out), parse_pytest_failures(out),
                                                    kind="local-live-internet",
                                                    note="real internet at run time: EXTERNAL — excluded from the fingerprint"))

    # --- storage live stack ----------------------------------------------------
    if args.pg_bin and args.postgrest:
        pgdata = os.path.join(tmp, "pgdata")
        port = str(args.pg_port)
        pg = lambda x: os.path.join(args.pg_bin, x)  # noqa: E731
        R.run("storage:initdb", [pg("initdb"), "-D", pgdata, "-U", "postgres", "--auth=trust", "-E", "UTF8"])
        R.run("storage:start", [pg("pg_ctl"), "-D", pgdata, "-o", f"-p {port} -k {tmp} -c listen_addresses=127.0.0.1",
                                "-l", os.path.join(tmp, "pg.log"), "-w", "start"])
        R.run("storage:createdb", [pg("psql"), "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-c", "create database agos"])
        senv = {"ADMIN_URI": f"postgresql://postgres@127.0.0.1:{port}/agos",
                "AUTH_URI": f"postgresql://authenticator:authenticator@127.0.0.1:{port}/agos",
                "POSTGREST_BIN": args.postgrest, "PGRST_PORT": str(args.pg_port + 1),
                "PATH": args.pg_bin + os.pathsep + env["PATH"]}
        c, out = R.run("storage:stack", ["bash", "scripts/r042/run_local_stack.sh"], extra_env=senv, timeout=600)
        R.run("storage:stop", [pg("pg_ctl"), "-D", pgdata, "-m", "fast", "-w", "stop"])
        groups["sql-storage"]["steps"].append(step(
            "storage:stack", "bash scripts/r042/run_local_stack.sh (fresh PostgreSQL + PostgREST v12.2.3)", c,
            parse_pytest_summary(out), parse_pytest_failures(out), kind="local-operational",
            note="applies supabase_roles.sql, docs/sql/001_init.sql, 002_tenant_isolation.sql, grants_after_schema.sql "
                 "with ON_ERROR_STOP=1, then tests/test_r042_live_storage.py over HTTP"))
        for path, f in files.items():
            if f["group"] == "sql-storage":
                mark(path, "storage-stack", c == 0, "stack failed")
    else:
        groups["sql-storage"]["steps"].append(step("storage:stack", "bash scripts/r042/run_local_stack.sh", None,
                                                   kind="not-run", note="no --pg-bin/--postgrest"))

    # --- shell ------------------------------------------------------------------
    for path in [t for t in tracked if t.endswith(".sh")]:
        c, _ = R.run(f"bash-n:{path}", ["bash", "-n", path])
        mark(path, "bash-n", c == 0, "syntax")
        gid = files[path]["group"]
        groups[gid]["steps"].append(step(f"bash-n:{path}", f"bash -n {path}", c, {"passed": int(c == 0), "failed": int(c != 0)}))

    # --- frontends ---------------------------------------------------------------
    def npm_steps(gid, cwd, steps_):
        ok_all = True
        for name, argv, parser in steps_:
            c, out = R.run(f"{gid}:{name}", argv, cwd=cwd, timeout=900)
            counts = parser(out) if parser else {"passed": int(c == 0), "failed": int(c != 0)}
            if parser and c != 0 and not counts["failed"]:
                counts["failed"] = 1
            groups[gid]["steps"].append(step(f"{gid}:{name}", ("(cd web) " if cwd else "") + " ".join(argv), c, counts))
            ok_all = ok_all and c == 0
        for path, f in files.items():
            if f["group"] == gid:
                mark(path, "npm-steps", ok_all, "a step failed")
    npm_steps("frontend-web", "web", [("ci", ["npm", "ci", "--engine-strict"], None),
                                      ("build", ["npm", "run", "build"], None),
                                      ("test", ["npm", "test"], parse_tap)])
    npm_steps("frontend-root", "", [("ci", ["npm", "ci", "--engine-strict"], None),
                                    ("eslint", ["npx", "eslint", ".", "--max-warnings=0"], None),
                                    ("build", ["npm", "run", "build"], None),
                                    # after build: the TanStack router plugin generates src/routeTree.gen.ts
                                    ("tsc", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"], None)])

    # --- deploy / CI static -----------------------------------------------------------
    for path in [t for t in tracked if t.endswith((".yml", ".yaml"))]:
        try:
            import yaml
            yaml.safe_load(open(os.path.join(wt, path), encoding="utf-8"))
            mark(path, "yaml", True)
        except Exception as e:  # noqa: BLE001
            mark(path, "yaml", False, type(e).__name__)
    mapout = os.path.join(tmp, "map.json")
    c, out = R.run("deploy:map-self-check", [py, "scripts/build_entrypoint_map.py", wt, sha, mapout])
    groups["deploy-ci"]["steps"].append(step("deploy:map-self-check",
                                             "python scripts/build_entrypoint_map.py <checkout> <sha> <tmp>", c,
                                             {"passed": int(c == 0), "failed": int(c != 0)},
                                             note="R-156 self-check: Docker/Render ASGI target, COPY sources, workflow "
                                                  "script references, routes vs runtime"))
    for path, f in files.items():
        if f["group"] == "deploy-ci":
            mark(path, "map-self-check", c == 0, "self-check failed")
    ci = json.load(open(args.ci_json, encoding="utf-8")) if args.ci_json else None
    if ci:
        if ci.get("headSha") != sha:
            problems.append(f"--ci-json is for {ci.get('headSha')}, not {sha}")
        jobs = {j["name"]: j["conclusion"] for j in ci.get("jobs", [])}
        for gid, job, what in (("deploy-ci", "docker", "docker build + container /health smoke"),
                               ("deploy-ci", "quality", "workflow ran: ruff fatal set"),
                               ("sql-storage", "storage", "PostgreSQL 17 service + PostgREST, same script"),
                               ("tests-python", "test", "pytest on Python 3.11 (+cov) and 3.12, runtime real"),
                               ("tests-python", "network", "pytest -m network"),
                               ("frontend-web", "build", "web + root builds, npm test, eslint"),
                               ("frontend-root", "build", "root eslint + build (no tsc step in CI)"),
                               ("deps-config", "audit", "pip-audit / npm audit on the locks")):
            concl = jobs.get(job)
            groups[gid]["steps"].append(step(f"ci:{job}", f"GitHub Actions run {ci.get('databaseId')} job {job}",
                                             0 if concl == "success" else (None if concl is None else 1),
                                             kind="ci", note=f"{what}; conclusion={concl}"))
    for gid, why in (("deploy-ci", "Render deployment not exercised (no deploy credentials); render.yaml only parsed "
                                   "and self-checked"),):
        groups[gid]["gaps"].append(why)

    # --- deps / config -------------------------------------------------------------
    for path in [t for t in tracked if files[t]["group"] == "deps-config"]:
        text = open(os.path.join(wt, path), encoding="utf-8").read()
        if path.endswith(".lock"):
            reqs = re.findall(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s\\]+)", text, re.M)
            blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
            unhashed = [b.split("==")[0] for b in blocks if "==" in b and "--hash=sha256:" not in b]
            mark(path, f"lock {len(reqs)} pins", bool(reqs) and not unhashed, f"unhashed: {unhashed[:5]}")
        elif path.endswith((".in", ".txt")):
            lines = [ln.split("#")[0].strip() for ln in text.splitlines()]
            badl = [ln for ln in lines if ln and not re.match(r"^(-[rc] \S+|[A-Za-z0-9_.\-\[\],]+\s*([<>=!~]=?[^;\s]+(,[<>=!~]=?[^;\s]+)*)?(\s*;.*)?)$", ln)]
            mark(path, "requirements-syntax", not badl, f"bad: {badl[:3]}")
        elif path == "pytest.ini":
            cp = configparser.ConfigParser()
            cp.read_string(text)
            mark(path, "ini", cp.has_section("pytest"), "no [pytest]")
        elif path == ".env.example":
            badl = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
                    and not re.match(r"^[A-Z_][A-Z0-9_]*=", ln)]
            mark(path, "env-syntax", not badl, f"bad: {badl[:3]}")
        else:
            mark(path, "text-utf8", True)
    npass = sum(1 for t in tracked if files[t]["group"] == "deps-config" and "FAIL" not in " ".join(files[t]["checks"]))
    ntot = groups["deps-config"]["files"]
    groups["deps-config"]["steps"].append(step("config:parse", "lock hash-pinning / requirement syntax / ini / env",
                                               0 if npass == ntot else 1, {"passed": npass, "failed": ntot - npass}))

    # --- data files: json/jsonl/csv ----------------------------------------------------
    for gid in ("governance-data", "evidence-data", "benchmark-data"):
        ok = bad_ = 0
        for path in [t for t in tracked if files[t]["group"] == gid]:
            fp = os.path.join(wt, path)
            try:
                if path.endswith(".json"):
                    json.load(open(fp, encoding="utf-8"))
                elif path.endswith(".jsonl"):
                    for ln in open(fp, encoding="utf-8"):
                        if ln.strip():
                            json.loads(ln)
                elif path.endswith(".csv"):
                    rows = list(csv.reader(io.StringIO(open(fp, encoding="utf-8").read())))
                    widths = {len(r) for r in rows if r}
                    if len(widths) > 1:
                        raise ValueError(f"ragged CSV widths {sorted(widths)}")
                else:
                    open(fp, encoding="utf-8").read()
                mark(path, "parse", True)
                ok += 1
            except Exception as e:  # noqa: BLE001
                mark(path, "parse", False, f"{type(e).__name__}: {str(e)[:120]}")
                bad_ += 1
        groups[gid]["steps"].append(step("data:parse", "json.load / json lines / csv width / utf-8",
                                         1 if bad_ else 0, {"passed": ok, "failed": bad_},
                                         [t for t in tracked if files[t]["group"] == gid and "FAIL" in " ".join(files[t]["checks"])]))
    seed = "docs/governance/roadmaps/revival-scale/templates/tool_registry_seed.json"
    schema = "docs/governance/roadmaps/revival-scale/specs/tool_registry_contract.schema.json"
    if seed in tset and schema in tset:
        errs = check_schema_subset(json.load(open(os.path.join(wt, schema), encoding="utf-8")),
                                   json.load(open(os.path.join(wt, seed), encoding="utf-8")))
        mark(seed, "schema-subset", not errs, "; ".join(errs))
        groups["governance-data"]["steps"].append(step("data:seed-vs-schema", f"required keys/types of {schema} on {seed}",
                                                       1 if errs else 0, {"passed": int(not errs), "failed": len(errs)},
                                                       errs))
    for name, argv in (("gate:status-evidence", [py, "scripts/check_status_evidence.py", "."]),
                       ("gate:no-delete", [py, "scripts/check_no_delete.py", f"{sha}^1", sha]),
                       ("gate:atomic-v2", [py, "scripts/check_atomic_batch.py", f"{sha}^1", sha])):
        c, _ = R.run(name, argv)
        groups["governance-data"]["steps"].append(step(name, " ".join(["python"] + argv[1:]).replace(sha, "<sha>"), c,
                                                       {"passed": int(c == 0), "failed": int(c != 0)},
                                                       note="gate re-run on the snapshot (merge commit vs first parent)"))

    # --- integrity: logs / text / png -----------------------------------------------------
    for gid in ("evidence-logs", "binary-assets"):
        ok = bad_ = 0
        for path in [t for t in tracked if files[t]["group"] == gid]:
            data = open(os.path.join(wt, path), "rb").read()
            if gid == "binary-assets":
                good, why = data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG signature"
            else:
                try:
                    data.decode("utf-8")
                    good, why = True, ""
                except UnicodeDecodeError as e:
                    good, why = False, f"utf-8 error at byte {e.start}"
            mark(path, "integrity", good, why)
            ok, bad_ = ok + good, bad_ + (not good)
        groups[gid]["steps"].append(step("integrity", "PNG signature" if gid == "binary-assets" else "UTF-8 decode",
                                         1 if bad_ else 0, {"passed": ok, "failed": bad_},
                                         [t for t in tracked if files[t]["group"] == gid and "FAIL" in " ".join(files[t]["checks"])]))

    # --- markdown links ------------------------------------------------------------------
    ok = bad_ = 0
    broken_all = []
    for path in [t for t in tracked if files[t]["group"] == "docs"]:
        bl = md_broken_links(path, open(os.path.join(wt, path), encoding="utf-8").read(), tset, tdirs)
        mark(path, "links", not bl, f"{len(bl)} broken: {bl[:3]}")
        ok, bad_ = ok + (not bl), bad_ + bool(bl)
        broken_all += [f"{path} -> {b}" for b in bl]
    groups["docs"]["steps"].append(step("docs:links", "relative markdown links resolve to tracked paths",
                                        1 if bad_ else 0, {"passed": ok, "failed": bad_}, broken_all))
    raw.write("\n=== [docs:links]\n" + "".join(b + "\n" for b in broken_all) + f"=== [docs:links] exit={1 if bad_ else 0}\n")

    # --- file statuses + group results -------------------------------------------------------
    for f in files.values():
        if f["group"] == "UNASSIGNED":
            f["status"] = "unassigned"
        elif any("FAIL" in c for c in f["checks"]):
            f["status"] = "fail"
        elif f["group"] in ("evidence-logs", "evidence-data", "binary-assets"):
            f["status"] = "integrity-only"
        elif not f["checks"]:
            f["status"] = "no-check"
            problems.append(f"no check applied: {f['path']}")
        else:
            f["status"] = "pass"
        f["checks"] = sorted(set(f["checks"]))
    for g in groups.values():
        res = [s["result"] for s in g["steps"] if s["evidence_kind"] not in ("ci", "local-live-internet")]
        g["result"] = "fail" if "fail" in res else ("pass" if "pass" in res else "not-run")
        g["file_status"] = {}
        for f in files.values():
            if f["group"] == g["id"]:
                g["file_status"][f["status"]] = g["file_status"].get(f["status"], 0) + 1
        g.pop("rules_compiled", None)
    tests_by_cat: Dict[str, Dict[str, Dict[str, int]]] = {}
    for mode, per in per_mode.items():
        for path, pc in per.items():
            cat = test_category(path)
            agg = tests_by_cat.setdefault(cat, {}).setdefault(mode, {"files": 0, "passed": 0, "failed": 0,
                                                                     "skipped": 0, "xfailed": 0, "errors": 0})
            agg["files"] += 1
            for k in ("passed", "failed", "skipped", "xfailed", "errors"):
                agg[k] += pc[k]
    out = {
        "schema": SCHEMA, "task": "R-158", "snapshot_sha": sha, "tracked_files": len(tracked),
        "checkout": how, "raw_log": os.path.basename(args.raw_log),
        "environment": {"tools": tool_versions(args.node_bin, args.pg_bin, args.postgrest),
                        "python_packages": installed_packages(),
                        "env_fixed": {k: env[k] for k in ("LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "CI")}},
        "groups": [{k: g[k] for k in ("id", "title", "category", "rules", "rationale", "files", "file_status",
                                      "result", "steps", "gaps")} for g in groups.values()],
        "tests_by_category": tests_by_cat,
        "ci": ({"run": ci.get("databaseId"), "head_sha": ci.get("headSha"), "conclusion": ci.get("conclusion"),
                "jobs": {j["name"]: j["conclusion"] for j in ci.get("jobs", [])}} if ci else None),
        "files_summary": {s: sum(1 for f in files.values() if f["status"] == s)
                          for s in sorted({f["status"] for f in files.values()})},
        "self_check": {"problems": problems, "result": "FAIL" if problems else "PASS"},
    }
    out["files"] = [files[p] for p in tracked]
    out["fingerprint"] = fingerprint(out)
    raw.write(f"\n# fingerprint(deterministic part)={out['fingerprint']}\n")
    raw.close()
    if not args.checkout:
        subprocess.run(["git", "-C", args.repo, "worktree", "remove", "--force", wt], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out, problems


def deterministic_view(b: Dict) -> Dict:
    """What the fingerprint covers: everything except per-run fields (raw_log, checkout mode) and
    EXTERNAL steps (real internet), whose outcome depends on the network at run time."""
    d = json.loads(json.dumps(b))
    for k in ("raw_log", "checkout", "fingerprint"):
        d.pop(k, None)
    for g in d.get("groups", []):
        g["steps"] = [s for s in g["steps"] if s["evidence_kind"] != "local-live-internet"]
    return d


def fingerprint(b: Dict) -> str:
    return hashlib.sha256(json.dumps(deterministic_view(b), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------ outputs --
def files_csv(rows: List[Dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["path", "group", "category", "status", "checks", "coverage_real_pct", "detail"])
    cats = {g["id"]: g["category"] for g in GROUPS}
    for r in rows:
        cat = test_category(r["path"]) if r["group"] == "tests-python" else cats.get(r["group"], "")
        w.writerow([r["path"], r["group"], cat, r["status"], " | ".join(r["checks"]),
                    "" if "coverage_real" not in r else r["coverage_real"], r["detail"]])
    return buf.getvalue()


def groups_csv(b: Dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TEMPLATE_COLUMNS + EXTRA_COLUMNS)
    tv = b["environment"]["tools"]
    for g in b["groups"]:
        for s in g["steps"]:
            c = s["counts"]
            tool = (f"python {tv['python']} pytest {tv['pytest']}" if s["raw_section"].startswith("pytest")
                    else f"node {tv['node']} npm {tv['npm']}" if s["raw_section"].startswith("frontend")
                    else f"ruff {tv['ruff']}" if s["raw_section"].startswith("ruff")
                    else f"{tv['postgres']} / {tv['postgrest']}" if s["raw_section"].startswith("storage")
                    else "GitHub Actions" if s["evidence_kind"] == "ci" else f"python {tv['python']}")
            row = {"task_id": "R-158", "test_id": f"{g['id']}/{s['raw_section']}", "category": g["category"],
                   "target_component": g["title"], "environment": s["evidence_kind"], "commit_sha": b["snapshot_sha"],
                   "tool_version": tool, "command_or_config_path": s["command"], "input_dataset_version": "",
                   "duration_seconds": "", "accepted_count": "", "completed_count": "",
                   "failed_count": c["failed"], "skipped_count": c["skipped"], "deselected_count": c["deselected"],
                   "result": s["result"],
                   "evidence_path": (f"{b['raw_log']}#[{s['raw_section']}]" if s["evidence_kind"] != "ci"
                                     else f"GitHub Actions run {b['ci']['run'] if b.get('ci') else '?'}"),
                   "known_limitations": s["note"], "reviewer": "", "date_utc": "",
                   "group_id": g["id"], "evidence_kind": s["evidence_kind"],
                   "exit_code": "" if s["exit_code"] is None else s["exit_code"], "passed": c["passed"],
                   "xfailed": c["xfailed"], "xpassed": c["xpassed"], "errors": c["errors"]}
            w.writerow([row.get(k, "") for k in TEMPLATE_COLUMNS + EXTRA_COLUMNS])
    return buf.getvalue()


def render_md(b: Dict, json_name: str) -> str:
    # R-158: render from the key-sorted form the JSON is stored in, so the
    # report is identical whether it is rendered in-run or from the file.
    b = json.loads(json.dumps(b, sort_keys=True))
    L = [f"# خط أساس اختبارات الملفات — R-158 عند `{b['snapshot_sha']}`", "",
         f"> مولَّد من [`{json_name}`]({json_name}) بـ`scripts/build_test_baseline.py` — لا يُحرر يدوياً. "
         f"البصمة (الجزء الحتمي): `{b['fingerprint']}`.", "",
         f"- الملفات المتعقبة: **{b['tracked_files']}** — كل ملف في مجموعة واحدة "
         f"({', '.join(f'{k}={v}' for k, v in b['files_summary'].items())}).",
         f"- الفحص الذاتي: **{b['self_check']['result']}**" + (f" — {'; '.join(b['self_check']['problems'])}"
                                                               if b['self_check']['problems'] else ""),
         f"- السجل الخام لهذا التشغيل: `{b['raw_log']}` (أقسام `=== [<id>] ===`).", "",
         "## البيئة", "", "| الأداة | الإصدار |", "|---|---|"]
    L += [f"| {k} | `{v}` |" for k, v in b["environment"]["tools"].items()]
    L += ["", f"حزم Python المثبتة: {len(b['environment']['python_packages'])} (القائمة الكاملة في JSON). "
          f"متغيرات ثابتة: {', '.join(f'`{k}={v}`' for k, v in b['environment']['env_fixed'].items())}.", "",
          "## المجموعات", "",
          "| المجموعة | الفئة | الملفات | حالة الملفات | النتيجة المحلية |", "|---|---|---|---|---|"]
    for g in b["groups"]:
        L.append(f"| `{g['id']}` — {g['title']} | {g['category']} | {g['files']} | "
                 f"{', '.join(f'{k}={v}' for k, v in sorted(g['file_status'].items()))} | **{g['result']}** |")
    L += ["", "## الخطوات والأوامر", "",
          "| المجموعة / الخطوة | نوع الدليل | الأمر | exit | pass | fail | skip | xfail | xpass | deselected | errors |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in b["groups"]:
        for s in g["steps"]:
            c = s["counts"]
            cmd = s["command"].replace("|", "\\|")
            L.append(f"| `{g['id']}` / `{s['raw_section']}` | {s['evidence_kind']} | `{cmd}` | "
                     f"{'—' if s['exit_code'] is None else s['exit_code']} | {c['passed']} | {c['failed']} | "
                     f"{c['skipped']} | {c['xfailed']} | {c['xpassed']} | {c['deselected']} | {c['errors']} |")
    L += ["", "## سبب ملاءمة كل نوع تحقق", ""]
    L += [f"- `{g['id']}`: {g['rationale']}" for g in b["groups"]]
    L += ["", "## الإخفاقات والفجوات المسجلة", ""]
    any_fail = False
    for g in b["groups"]:
        for s in g["steps"]:
            if s["result"] == "fail" or s["failures"]:
                any_fail = True
                L.append(f"- `{g['id']}` / `{s['raw_section']}` ({s['evidence_kind']}, exit {s['exit_code']}): "
                         + ", ".join(f"`{x}`" for x in s["failures"][:20]) + (" …" if len(s["failures"]) > 20 else ""))
        for gap in g["gaps"]:
            L.append(f"- `{g['id']}` (فجوة): {gap}")
    if not any_fail:
        L.append("- لا شيء.")
    L += ["", "## الاختبارات حسب الفئة (TEST_MATRIX_AR.md)", "",
          "| الفئة | الوضع | ملفات | pass | fail | skip | xfail | errors |", "|---|---|---|---|---|---|---|---|"]
    for cat in sorted(b["tests_by_category"]):
        for mode in sorted(b["tests_by_category"][cat]):
            a = b["tests_by_category"][cat][mode]
            L.append(f"| {cat} | {mode} | {a['files']} | {a['passed']} | {a['failed']} | {a['skipped']} | "
                     f"{a['xfailed']} | {a['errors']} |")
    if b.get("ci"):
        L += ["", "## CI على SHA نفسه", "", f"تشغيل {b['ci']['run']} عند `{b['ci']['head_sha']}`: "
              f"**{b['ci']['conclusion']}** — " + ", ".join(f"{k}={v}" for k, v in b["ci"]["jobs"].items()) + "."]
    L += ["", "## حدود", "",
          "- `local-test` = أُعيد تشغيله في هذا التشغيل؛ `local-operational` = خدمات حقيقية محلية (PostgreSQL/PostgREST)؛ "
          "`local-live-internet` = الإنترنت الحقيقي وقت التشغيل (خارج البصمة)؛ `ci` = نتيجة GitHub Actions على SHA نفسه "
          "(لم يُعِد هذا التشغيل إنتاجها)؛ `not-run` = لم يُنفذ.",
          "- خط الأساس لا يثبت جاهزية إنتاجية ولا أهداف السعة (10,000 وكيل / 1,000,000 مهمة / 10,000 أداة).", ""]
    return "\n".join(L)


def write_outputs(b: Dict, out_dir: str) -> Dict[str, str]:
    s7 = b["snapshot_sha"][:7]
    os.makedirs(out_dir, exist_ok=True)
    names = {"json": f"TEST_BASELINE_{s7}.json", "md": f"TEST_BASELINE_{s7}.md",
             "groups": f"TEST_BASELINE_GROUPS_{s7}.csv", "files": f"TEST_BASELINE_FILES_{s7}.csv"}
    paths = {k: os.path.join(out_dir, v) for k, v in names.items()}
    with open(paths["json"], "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    with open(paths["md"], "w", encoding="utf-8") as f:
        f.write(render_md(b, names["json"]))
    with open(paths["groups"], "w", encoding="utf-8") as f:
        f.write(groups_csv(b))
    with open(paths["files"], "w", encoding="utf-8") as f:
        f.write(files_csv(b["files"]))
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--sha")
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-log")
    ap.add_argument("--render-from", help="re-render the .md/.csv outputs from an existing baseline JSON "
                                          "(no command is run; the JSON is not changed)")
    ap.add_argument("--checkout")
    ap.add_argument("--node-bin")
    ap.add_argument("--pg-bin")
    ap.add_argument("--postgrest")
    ap.add_argument("--pg-port", type=int, default=55500)
    ap.add_argument("--ci-json")
    ap.add_argument("--no-network", action="store_true")
    a = ap.parse_args(argv)
    if a.render_from:
        with open(a.render_from, encoding="utf-8") as f:
            b = json.load(f)
        if b.get("fingerprint") != fingerprint(b):
            print("::error::test-baseline: stored fingerprint does not match the JSON")
            return 2
        paths = write_outputs(b, a.out)
        print(f"re-rendered from {a.render_from} fingerprint={b['fingerprint']} out={paths['json']}")
        return 0
    if not (a.sha and a.raw_log):
        ap.error("--sha and --raw-log are required (unless --render-from)")
    try:
        b, problems = build(a)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"::error::test-baseline: {type(e).__name__}: {e}")
        return 2
    paths = write_outputs(b, a.out)
    for p in problems:
        print(f"::error::test-baseline self-check: {p}")
    print(f"snapshot={b['snapshot_sha']} files={b['tracked_files']} status={b['files_summary']} "
          f"groups={ {g['id']: g['result'] for g in b['groups']} } fingerprint={b['fingerprint']} "
          f"self_check={b['self_check']['result']} out={paths['json']}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
