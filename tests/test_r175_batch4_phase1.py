"""R-175 phase 1 (D-128): the real phase-1 operation — 100 certified tools.

What these tests prove (each one REAL, no fakes):

* the executable surface is now exactly 100 handlers (20 frozen specialists
  + 80 stdlib core tools), every one registered and gate-checkable;
* a REAL smoke per tool: an actual ``tool_runtime.dispatch`` through the
  runtime gate with a real work item — the stdlib tools do real deterministic
  work, the network specialists make REAL live calls (example.com probe,
  JSON fetch, web search), modifier runs under a REAL approval, and
  test_runner executes code in the REAL sandboxed runner;
* the stdlib module's declared hermeticity matches its code (AST cross-check:
  no network/model/exec reach — the R-171 style check, for the new module);
* the phase-1 ceiling holds: after 100 certified tools the 101st is REFUSED
  (advancing to phase 2 is an owner decision — D-125);
* the operator runbook (scripts/certify_phase1.py) certifies all 100 with
  the honest evidence chain (smoke/contract/security markers at the
  governed front door) — run for real as a subprocess.

Honest limits: the live network smokes are integration evidence, not a
claim that the tools are production-healthy (that is the health checker's
job); certification proves the recorded evidence chain.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_civilization.core import tool_certification, tool_runtime
from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core import tools_stdlib

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "scripts" / "certify_phase1.py"


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", str(tmp_path / "p1.sqlite3"))
    # authenticated mode so the sandboxed code runner (test_runner) executes:
    monkeypatch.setenv("AGOS_API_KEY", "test:phase1")
    tool_runtime.reset_runtime_registry()
    yield
    tool_runtime.reset_runtime_registry()


def _run(coro):
    return asyncio.run(coro)


def _load_runbook() -> dict:
    """The runbook module (single source of truth for the smoke work items)."""
    spec = importlib.util.spec_from_file_location("certify_phase1", RUNBOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- the surface: 100 handlers ------------------------------------------------

def test_the_executable_surface_is_exactly_100_handlers():
    # 20 frozen specialists + 80 stdlib core tools (D-128):
    assert len(real_dispatcher._HANDLERS) == 100
    stdlib_specs = set(tools_stdlib.HANDLERS)
    assert len(stdlib_specs) == 80
    assert stdlib_specs <= set(real_dispatcher._HANDLERS)
    reg = tool_runtime.runtime_registry()
    records = {r.name: r for r in reg.search(limit=None)}
    assert set(records) == set(real_dispatcher._HANDLERS)
    for spec in real_dispatcher._HANDLERS:
        ok, reason = tool_runtime.check(spec)
        assert ok, (spec, reason)


def test_stdlib_records_declare_hermetic_metadata():
    for rec in tools_stdlib.stdlib_tool_records():
        assert rec.network_allowlist == [], rec.tool_id
        assert rec.side_effects == [], rec.tool_id
        assert rec.read_only is True, rec.tool_id
        assert rec.requires_approval is False, rec.tool_id
        assert rec.lifecycle_state == "registered"
        assert rec.test_status == "not_tested"
        # the ref resolves to the very handler the gate will run:
        from agent_civilization.core.tool_registry import resolve_implementation
        assert resolve_implementation(rec.implementation_ref) is \
            tools_stdlib.HANDLERS[rec.name]


def test_stdlib_declared_hermeticity_matches_the_code():
    """The R-171-style AST cross-check, for the stdlib module: the declared
    'no network, no model, no exec' must match what the code can reach."""
    src = (ROOT / "agent_civilization/core/tools_stdlib.py").read_text("utf-8")
    tree = ast.parse(src)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    # urllib.PARSE is pure string work (allowed); the network-capable
    # submodule urllib.request is what the ban means:
    banned = {"socket", "http", "requests", "ssl", "subprocess",
              "asyncio", "shutil", "os", "sys", "pathlib"}
    assert not (imports & banned), sorted(imports & banned)
    assert "urllib.request" not in src
    for name in ("open(", "eval(", "exec(", "__import__", "os.system"):
        assert name not in src, name


# -- a REAL smoke per tool (all 100) ------------------------------------------

def test_every_one_of_the_100_tools_smokes_for_real():
    """The real phase-1 smoke: every tool dispatched through the actual gate.
    The network specialists make real live calls; modifier runs under a real
    approval; test_runner executes in the real sandboxed runner."""
    mod = _load_runbook()
    reg = tool_runtime.runtime_registry()
    records = {r.name: r for r in reg.search(limit=None)}
    assert set(mod.SMOKE_WORK) == set(records) == set(real_dispatcher._HANDLERS)
    failures = []
    for spec in sorted(mod.SMOKE_WORK):
        result = _run(tool_runtime.dispatch(spec, dict(mod.SMOKE_WORK[spec])))
        if not (isinstance(result, dict) and result.get("ok") is True):
            failures.append((spec, str(result.get("error", ""))[:80]
                            if isinstance(result, dict) else type(result).__name__))
    assert failures == []


def test_malformed_smoke_input_is_refused_not_crashed():
    # the contract is honest: wrong input -> an ok=False refusal (never a
    # crash, never a silent pass) — proven on a sample of stdlib tools:
    for spec in ("text_stats", "base64_decode", "json_validate", "list_sort"):
        result = _run(tool_runtime.dispatch(spec, {"text": None}))
        assert isinstance(result, dict) and result["ok"] is False, spec
        assert result["error"], spec


# -- the phase-1 ceiling ------------------------------------------------------

def test_phase1_ceiling_refuses_the_101st_certified_tool():
    """100 certify; the 101st is REFUSED — advancing the phase is an owner
    decision (D-125), and the refusal happens before any mutation."""
    reg = tool_runtime.runtime_registry()
    # the LATEST version per tool_id (legacy rows are history, not the walk):
    latest: dict = {}
    for r in reg.search(limit=None):
        cur = latest.get(r.name)
        if cur is None or r.version >= cur.version:
            latest[r.name] = r
    items = [{"tool_id": r.tool_id, "version": r.version,
              "evidence": "smoke: real dispatch ok=True; contract: D-127 type "
                          "check passed; security: runtime gate enforcement"}
             for r in latest.values()]
    report = tool_certification.certify_batch(reg, items, actor="test:phase1")
    assert report["error_type"] is None, report
    assert len(report["certified"]) == 100
    # a probe 101st tool (a real record with its own implementation_ref):
    probe = tools_stdlib.stdlib_tool_records()[0].model_dump()
    probe.update({"tool_id": "agos.internal.probe101", "name": "probe101",
                  "implementation_ref": "agent_civilization.core.tools_stdlib:_ok"})
    reg.register(probe, actor="test:phase1")
    with pytest.raises(tool_certification.PhaseCeilingRefused) as ei:
        tool_certification.certify(reg, "agos.internal.probe101", "1.0.0",
                                   actor="test:phase1",
                                   evidence="smoke: x; contract: x; security: x")
    assert "owner decision" in str(ei.value)
    # the refusal left the probe untouched (never half-walked):
    probe_rec = reg.get("agos.internal.probe101", "1.0.0")
    assert probe_rec.lifecycle_state == "registered"
    assert probe_rec.test_status == "not_tested"


# -- the operator runbook: the real phase-1 operation -------------------------

def test_the_phase1_runbook_certifies_all_100_with_real_evidence():
    """The actual phase-1 operation, run for real: the runbook walks every
    seeded tool through a real dispatch + the governed evidence chain and
    certifies all 100 (phase=1; the ceiling is honored exactly)."""
    env = dict(os.environ)
    env.update({"AGOS_TOOL_REGISTRY_DB": str(ROOT / ".pytest_phase1.sqlite3"),
                "AGOS_API_KEY": "test:phase1",
                "PATH": os.environ["PATH"]})
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNBOOK)], capture_output=True, text=True,
            env=env, cwd=str(ROOT), timeout=300)
        assert proc.returncode == 0, proc.stderr[-500:]
        report = json.loads(proc.stdout)
        assert report["phase"] == 1
        assert report["tools_total"] == 100
        assert report["certified_count"] == 100
        assert report["not_certified"] == []
        counts = report["state_counts"]
        assert counts["lifecycle"]["certified"] == 100
        assert counts["unique_tools"] == 100
        # every certified tool carries the three evidence markers at the
        # governed front door (the walk recorded contract/integration/certified):
        assert counts["test_status"]["certified"] == 100
        assert "owner decision" in report["declaration"]
    finally:
        db = ROOT / ".pytest_phase1.sqlite3"
        if db.exists():
            db.unlink()
