"""R-203 batch 2 (D-152): DEL-001 is deleted and no live path depends on it.

Evidence class: local / in-process (git ls-files, AST of every tracked .py,
TestClient on the real app). Not a live deployment check — that is the Render
deploy recorded in the batch log.
"""
from __future__ import annotations

import ast
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIST = ROOT / "docs/governance/deletions/DEL-001.list"


def _listed():
    return [ln.strip() for ln in LIST.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {p for p in out.split("\0") if p}


def test_every_del_001_path_is_gone():
    listed = _listed()
    assert len(listed) == 1033
    assert not (set(listed) & _tracked())
    assert not [p for p in listed if (ROOT / p).exists()]


DELETED_MODULES = re.compile(
    r"^agos_kernel\.infrastructure\.(redis_queue|circuit_breaker)$"
    r"|^agent_civilization\.agents\.specialists\.\w+\.\w+_\d{4}$")


def test_no_tracked_python_imports_a_deleted_module():
    hits = []
    for p in sorted(_tracked()):
        if not p.endswith(".py") or not (ROOT / p).exists():
            continue
        tree = ast.parse((ROOT / p).read_text(encoding="utf-8"), filename=p)
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            hits += [f"{p}:{n.lineno} {m}" for m in mods if DELETED_MODULES.match(m)]
    assert hits == []


def test_health_has_no_redis_check_and_the_app_serves():
    from fastapi.testclient import TestClient

    import server
    from agos_kernel.infrastructure.health import create_default_registry

    assert set(create_default_registry()._checks) == {"api", "civilization", "storage"}
    with TestClient(server.app) as c:
        r = c.get("/health")
    assert r.status_code in (200, 503)
    assert "redis" not in r.text.lower()


def test_specialist_classes_still_generated_without_the_shims():
    import agent_civilization.agents.specialists as top
    ns = {}
    exec("from agent_civilization.agents.specialists import *", ns)
    assert len([n for n in ns if "Agent" in n and n[-1].isdigit()]) == 1000
    assert top.AnalystAgent0015.__name__ == "AnalystAgent0015"


def test_no_root_frontend_left_and_ci_does_not_build_it():
    for p in ("package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "src"):
        assert not (ROOT / p).exists(), p
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Root app" not in ci and "Root dependency audit" not in ci
