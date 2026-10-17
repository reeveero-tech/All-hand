"""R-156: tests for scripts/build_entrypoint_map.py.

Two layers:

1. Generator unit tests on REAL temporary git repos (positive + negative):
   conditional/lazy/dynamic edges, imperative and decorator routes, broken
   Docker/Render/CI references make the self-check fail, determinism.
2. Map-vs-reality integration tests on THIS repository: the static map is
   compared with what the code actually does when executed —
   * routes registered on the real FastAPI app == routes in the map;
   * modules really loaded by ``import server`` ⊆ static closure, and every
     module-level static import is really loaded;
   * the dynamic ``import_module(f"...{spec}")`` expansion == specialists the
     runtime actually loads under AGOS_AGENT_IMPL=specialists;
   * map-driven HTTP smoke of every route (no 5xx), a real uvicorn boot of the
     Docker/Render ASGI target, ``run.py`` over stdin, ``--help``/import smoke
     of every guarded CLI, and pytest collection == test files in the map.
"""
from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_entrypoint_map.py"
sys.path.insert(0, str(ROOT / "scripts"))
import build_entrypoint_map as bem  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
FRAMEWORK_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
CLEAN_ENV_DROP = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY",
                  "AGOS_API_KEY", "AGOS_API_KEYS", "REDIS_URL", "AGOS_LLM_KEYLESS", "R042_LIVE", "AGOS_AGENT_IMPL")


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_DROP}
    env.setdefault("AGOS_ENV", "development")   # keyless local start is refused otherwise (tenancy.enforce_startup)
    env.update(extra)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout.strip()


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ============================================================ 1. unit tests ==

MINI = {
    "Dockerfile": "FROM node:22 AS web\nCOPY web/ ./\nFROM python:3.12\nCOPY requirements.lock ./\nCOPY . .\n"
                  'CMD ["sh", "-c", "uvicorn app:app --port 8000"]\n',
    "render.yaml": "services:\n  - type: web\n    buildCommand: pip install -r requirements.lock && cd web && npm ci\n"
                   "    startCommand: uvicorn app:app --host 0.0.0.0\n",
    "requirements.lock": "x==1\n",
    "web/index.html": '<script type="module" src="/src/main.jsx"></script>\n',
    "web/src/main.jsx": "import './x.js';\n",
    "web/src/x.js": "export default 1;\n",
    "web/package.json": '{"scripts": {"build": "vite build", "test": "node --test tests/*.test.mjs"}}\n',
    "pytest.ini": "[pytest]\ntestpaths = tests\n",
    "app.py": ("import os\nfrom pkg import core\nfrom fastapi import FastAPI, Security\napp = FastAPI()\n"
               "KEY = os.environ.get('APP_KEY', 'dev')\n\n\n@app.get('/health')\ndef health():\n    return {}\n\n\n"
               "@app.post('/secure')\ndef secure(_=Security(core.guard)):\n    return {}\n\n\n"
               "if os.path.exists('dist'):\n    app.mount('/assets', None)\n\n    @app.get('/{p:path}')\n"
               "    def spa(p):\n        return p\n\n\n"
               "def register(app2):\n    from pkg import lazy_mod\n\n    @app2.get('/late')\n    def late():\n"
               "        return lazy_mod.X\n\n\ntry:\n    from pkg import optional\nexcept ImportError:\n    optional = None\n"),
    "pkg/__init__.py": "",
    "pkg/core.py": ('import importlib\nURL = "https://api.example.org/v1/chat"\nTABLE = "/rest/v1/tasks"\n\n\n'
                    "def guard():\n    return 1\n\n\ndef load(n):\n    return importlib.import_module(f'pkg.plugins.{n}')\n"),
    "pkg/lazy_mod.py": ("import os\n_VARS = ('KEY_ONE', 'KEY_TWO')\n"
                        "SRC = next((v for v in _VARS if os.environ.get(v)), '')\nX = 1\n"),
    "pkg/optional.py": "Y = 1\n",
    "pkg/plugins/__init__.py": "",
    "pkg/plugins/a.py": "A = 1\n",
    "pkg/plugins/b.py": "B = 1\n",
    "scripts/tool.py": 'import argparse\nif __name__ == "__main__":\n    argparse.ArgumentParser().parse_args()\n',
    "tests/test_app.py": "from fastapi import FastAPI\napp = FastAPI()\n\n\ndef test_x():\n    @app.get('/boom')\n"
                         "    def boom():\n        return 1\n",
    ".github/workflows/ci.yml": ("name: CI\non:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
                                "    steps:\n      - run: |\n          pip install -r requirements.lock\n"
                                "          python scripts/tool.py --help\n          pytest tests/\n"
                                "  web:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm ci\n"
                                "        working-directory: web/\n"),
    "src/router.tsx": "export const r = 1;\n",
    "src/routes/index.tsx": "export default 1;\n",
}


@pytest.fixture()
def mini(tmp_path: Path):
    r = tmp_path / "mini"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for k, v in MINI.items():
        _w(r, k, v)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "snap")
    return r


def _map(repo: Path, sha: str = "HEAD"):
    return bem.build_map(str(repo), sha)


def test_entry_points_from_docker_render_ci_tests_frontends(mini):
    m, problems = _map(mini)
    assert problems == []
    ids = {e["id"]: e for e in m["entry_points"]}
    assert ids["deploy:docker-cmd"]["target"] == "app.py" and ids["deploy:render-start"]["target"] == "app.py"
    assert "cli:scripts/tool.py" in ids and ids["tests:pytest"]["files"] == 1
    assert "ci:.github/workflows/ci.yml#test" in ids and "ci:.github/workflows/ci.yml#web" in ids
    assert "frontend:web/index.html" in ids and ids["frontend:src/router.tsx"]["evidence"].startswith("convention")
    assert {"scripts/tool.py", "requirements.lock"} <= set(ids["ci:.github/workflows/ci.yml#test"]["paths"])


def test_routes_scope_condition_and_dependencies(mini):
    m, _ = _map(mini)
    r = {(x["method"], x["path"]): x for x in m["routes"]}
    assert r[("GET", "/health")]["scope"] == "top" and r[("GET", "/health")]["condition"] is None
    assert r[("POST", "/secure")]["deps"] == ["core.guard"]
    assert r[("GET", "/{p:path}")]["scope"] == "conditional" and "os.path.exists" in r[("GET", "/{p:path}")]["condition"]
    assert r[("MOUNT", "/assets")]["condition"].startswith("if ")
    assert r[("GET", "/late")]["scope"] == "lazy" and "inside def register()" in r[("GET", "/late")]["condition"]
    assert ("GET", "/boom") not in r                                   # test-only apps are not runtime routes


def test_edge_kinds_lazy_conditional_dynamic(mini):
    m, _ = _map(mini)
    k = {(e["from"], e["to"]): e["kind"] for e in m["edges"]}
    assert k[("app.py", "pkg/core.py")] == "top"
    assert k[("app.py", "pkg/lazy_mod.py")] == "lazy"
    assert k[("app.py", "pkg/optional.py")] == "conditional"
    assert k[("pkg/core.py", "pkg/plugins/a.py")] == "dynamic" and k[("pkg/core.py", "pkg/plugins/b.py")] == "dynamic"
    assert k[("web/index.html", "web/src/main.jsx")] == "top" and k[("web/src/main.jsx", "web/src/x.js")] == "top"
    sc = m["server_closure"]
    assert "pkg/lazy_mod.py" not in sc["top_level"] and "pkg/lazy_mod.py" in sc["all_kinds"]
    assert "pkg/plugins/b.py" in sc["all_kinds"]


def test_env_urls_tables_extracted(mini):
    m, _ = _map(mini)
    assert m["env_flags"]["APP_KEY"][0]["file"] == "app.py" and m["env_flags"]["APP_KEY"][0]["default"] == "'dev'"
    assert {"KEY_ONE", "KEY_TWO"} <= set(m["env_flags"])                      # read through a loop over a tuple
    assert m["env_flags"]["KEY_ONE"][0]["file"] == "pkg/lazy_mod.py"
    facts = bem.python_facts(bem.Snapshot(str(mini), "HEAD"), "pkg/core.py")
    assert facts["urls"][0]["url"].startswith("https://api.example.org") and facts["tables"][0]["table"] == "tasks"


def test_src_is_listed_unresolved_not_dead(mini):
    m, _ = _map(mini)
    u = {x["path"]: x for x in m["unanalyzable_or_unresolved"]}
    assert "Q-002" in u["src/router.tsx"]["reason"] and u["src/router.tsx"]["next_action"]
    assert not any("dead" in x["reason"].lower() or "orphan" in x["reason"].lower() for x in u.values())


@pytest.mark.parametrize("rel,text,msg", [
    ("Dockerfile", MINI["Dockerfile"].replace("COPY requirements.lock", "COPY requirements-missing.lock"),
     "COPY source 'requirements-missing.lock' not found"),
    ("Dockerfile", MINI["Dockerfile"].replace("uvicorn app:app", "uvicorn nowhere:app"), "CMD module nowhere not found"),
    ("render.yaml", MINI["render.yaml"].replace("cd web", "cd webx"), "path 'webx' not found"),
    (".github/workflows/ci.yml", MINI[".github/workflows/ci.yml"].replace("scripts/tool.py", "scripts/gone.py"),
     "job references 'scripts/gone.py'"),
])
def test_self_check_fails_on_broken_references(mini, tmp_path, rel, text, msg):
    _w(mini, rel, text)
    _git(mini, "commit", "-qam", "break")
    _, problems = _map(mini)
    assert any(msg in p for p in problems), problems
    res = subprocess.run([sys.executable, str(SCRIPT), str(mini), "HEAD", str(tmp_path / "m.json")],
                         capture_output=True, text=True)
    assert res.returncode == 1 and "self_check=FAIL" in res.stdout


def test_reads_snapshot_not_working_tree(mini):
    sha = _git(mini, "rev-parse", "HEAD")
    _w(mini, "Dockerfile", "CMD uvicorn nowhere:app\n")              # uncommitted
    m, problems = _map(mini, sha)
    assert problems == [] and m["deploy"]["docker"]["asgi_target"]["module"] == "app"


def test_cli_deterministic_and_bad_sha(mini, tmp_path):
    outs = []
    for i in (1, 2):
        d = tmp_path / f"r{i}"
        d.mkdir()
        res = subprocess.run([sys.executable, str(SCRIPT), str(mini), "HEAD", str(d / "m.json"), "--md", str(d / "m.md")],
                             capture_output=True, text=True)
        assert res.returncode == 0 and "self_check=PASS" in res.stdout
        outs.append(((d / "m.json").read_bytes(), (d / "m.md").read_bytes()))
    assert outs[0] == outs[1]
    assert "```mermaid" in outs[0][1].decode() and "يحتاج مراجعة بشرية" in outs[0][1].decode()
    bad = subprocess.run([sys.executable, str(SCRIPT), str(mini), "deadbeef", str(tmp_path / "x.json")],
                         capture_output=True, text=True)
    assert bad.returncode == 2


# ================================================= 2. map vs real behaviour ==

@pytest.fixture(scope="module")
def real_map():
    m, problems = bem.build_map(str(ROOT), "HEAD")
    return m, problems


def test_real_repo_self_check_passes(real_map):
    m, problems = real_map
    assert problems == []
    assert m["deploy"]["docker"]["asgi_target"]["path"] == "server.py" == m["deploy"]["render"]["asgi_target"]["path"]
    assert all(c["exists_in_snapshot"] for c in m["deploy"]["docker"]["copies"])


def _runtime_routes(extra_env=None):
    code = ("import json, server\nout=[]\nfor r in server.app.routes:\n"
            "    ms = sorted(getattr(r, 'methods', None) or ['MOUNT'])\n"
            "    out.append([ms, r.path])\nprint(json.dumps(out))\n")
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env=_env(**(extra_env or {})))
    assert res.returncode == 0, res.stderr[-2000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_map_routes_equal_routes_registered_on_real_app(real_map):
    m, _ = real_map
    rt = set()
    for ms, path in _runtime_routes():
        for meth in ms:
            if meth not in ("HEAD", "OPTIONS"):
                rt.add((meth, path))
    rt = {x for x in rt if x[1] not in FRAMEWORK_ROUTES}
    mp = {(r["method"], r["path"]) for r in m["routes"] if r["file"] in ("server.py", "agent_civilization/civilization_routes.py")}
    cond = {(r["method"], r["path"]) for r in m["routes"] if r["scope"] == "conditional"}
    web_dist = (ROOT / "web" / "dist").exists()
    expected = (mp - cond) | (cond if web_dist else set())
    assert rt == expected, {"runtime_only": sorted(rt - expected), "map_only": sorted(expected - rt)}


def _runtime_modules(code: str, extra_env=None):
    prog = (code + "\nimport sys, json, os\nroot=os.path.realpath(os.getcwd())\nout=set()\n"
            "for m in list(sys.modules.values()):\n    f=getattr(m,'__file__',None)\n"
            "    if f:\n        f=os.path.realpath(f)\n"
            "        if f.startswith(root+os.sep) and f.endswith('.py'):\n            out.add(os.path.relpath(f,root))\n"
            "print(json.dumps(sorted(out)))\n")
    res = subprocess.run([sys.executable, "-c", prog], cwd=ROOT, capture_output=True, text=True, timeout=180,
                         env=_env(**(extra_env or {})))
    assert res.returncode == 0, res.stderr[-2000:]
    return set(json.loads(res.stdout.strip().splitlines()[-1]))


def test_import_server_loads_exactly_what_the_static_map_predicts(real_map):
    m, _ = real_map
    loaded = _runtime_modules("import server")
    top, full = set(m["server_closure"]["top_level"]), set(m["server_closure"]["all_kinds"])
    top_py = {p for p in top if p.endswith(".py")}
    assert top_py <= loaded, f"module-level static imports not actually loaded: {sorted(top_py - loaded)}"
    assert loaded <= full, f"modules loaded at runtime but missing from the static map: {sorted(loaded - full)}"


def test_specialists_dynamic_expansion_matches_runtime(real_map):
    m, _ = real_map
    dyn = [e for e in m["edges"] if e["from"] == "agent_civilization/agents/real/specialist_runtime.py" and e["kind"] == "dynamic"]
    predicted = {e["to"] for e in dyn}
    loaded = _runtime_modules("from agent_civilization.agents.real import create_civilization\n"
                              "civ = create_civilization(agents_per_spec=1, impl='specialists')\n"
                              "assert len(civ) == 20, len(civ)")
    spec_pkgs = {p for p in loaded if re.fullmatch(r"agent_civilization/agents/specialists/[a-z][a-z_]*/__init__\.py", p)}
    assert spec_pkgs and spec_pkgs <= predicted and len(spec_pkgs) == 20
    bases = {p for p in loaded if p.endswith("_base.py")}
    fac = {e["to"] for e in m["edges"] if e["from"] == "agent_civilization/agents/specialists/_templates/_factory.py"
           and e["kind"] == "dynamic"}
    assert bases and bases <= fac


def test_map_driven_http_smoke_every_route_no_5xx(real_map, monkeypatch):
    m, _ = real_map
    for k in CLEAN_ENV_DROP:
        monkeypatch.delenv(k, raising=False)
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    done = []
    for r in m["routes"]:
        if r["file"] not in ("server.py", "agent_civilization/civilization_routes.py") or r["method"] == "MOUNT":
            continue
        if r["path"] == "/api/events/stream":
            continue              # infinite SSE stream: covered by tests/test_r144_sse* (bounded clients)
        if r["scope"] == "conditional" and not (ROOT / "web" / "dist").exists():
            continue
        path = re.sub(r"\{[^}]+\}", "r156-nonexistent", r["path"])
        meth = r["method"].lower()
        resp = getattr(c, meth)(path, json={}) if meth in ("post", "put", "patch") else getattr(c, meth)(path)
        assert resp.status_code < 500, (r["method"], path, resp.status_code, resp.text[:200])
        done.append((r["method"], r["path"], resp.status_code))
    assert len(done) >= 20


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_deploy_asgi_target_boots_with_real_uvicorn(real_map):
    m, _ = real_map
    t = m["deploy"]["docker"]["asgi_target"]
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", f"{t['module']}:{t['attr']}", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        body = None
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as r:
                    body = (r.status, r.read())
                break
            except OSError:
                time.sleep(0.5)
        assert body and body[0] == 200, "uvicorn did not serve /health/live"
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_run_py_cli_over_stdin():
    res = subprocess.run([sys.executable, "run.py"], cwd=ROOT, input="status\nplan build a small website\nquit\n",
                         capture_output=True, text=True, timeout=180, env=_env())
    assert res.returncode == 0, res.stderr[-1500:]
    assert "completed=0 failed=0" in res.stdout and "plan>" in res.stdout and "Goodbye!" in res.stdout


def test_every_guarded_cli_imports_cleanly_and_argparse_help_works(real_map):
    m, _ = real_map
    clis = [e["target"] for e in m["entry_points"] if e["kind"] == "cli" and e["target"] not in ("server.py", "run.py")]
    assert len(clis) >= 20
    for p in clis:
        src = (ROOT / p).read_text(encoding="utf-8")
        code = (f"import importlib.util as u\ns=u.spec_from_file_location('r156_cli', {str(ROOT / p)!r})\n"
                "m=u.module_from_spec(s)\nimport sys\nsys.modules['r156_cli']=m\ns.loader.exec_module(m)\n")
        res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120, env=_env())
        assert res.returncode == 0, (p, res.stderr[-800:])
        if "argparse" in src:
            h = subprocess.run([sys.executable, p, "--help"], cwd=ROOT, capture_output=True, text=True, timeout=120, env=_env())
            assert h.returncode == 0 and "usage" in h.stdout.lower(), (p, h.stderr[-500:])


def test_toplevel_scripts_compile(real_map, tmp_path):
    m, _ = real_map
    tops = [e["target"] for e in m["entry_points"] if e["kind"] == "cli-toplevel"]
    assert tops
    for p in tops:
        py_compile.compile(str(ROOT / p), doraise=True, cfile=str(tmp_path / "c.pyc"))


def test_pytest_collection_matches_test_entry_point(real_map):
    m, _ = real_map
    res = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--collect-only", "-qq", "-m", "", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True, timeout=300, env=_env())
    files = {ln.split("::")[0] for ln in res.stdout.splitlines() if "::" in ln}
    mapped = next(e for e in m["entry_points"] if e["id"] == "tests:pytest")
    assert len(mapped["file_list"]) == mapped["files"]
    in_head = set(subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD", "tests/"], cwd=ROOT, capture_output=True,
                                 text=True).stdout.split())
    # every mapped test file is really collected; anything else collected must be a file not yet committed
    assert set(mapped["file_list"]) == files & in_head, sorted(set(mapped["file_list"]) ^ (files & in_head))
    assert not (files - set(mapped["file_list"])) & in_head


ENV_TRACE = r'''
import os, sys, json, inspect
ROOT = os.path.realpath(os.getcwd())
seen = set()
def _caller_in_repo():
    # the IMMEDIATE caller outside os.py (os.getenv -> environ.get) must be a repo file
    fr = sys._getframe(2)
    while fr and (fr.f_code.co_filename in ("<string>", "<frozen os>", "<frozen _collections_abc>")
                  or os.path.basename(fr.f_code.co_filename) in ("os.py", "_collections_abc.py")):
        fr = fr.f_back
    f = fr.f_code.co_filename if fr else ""
    f = os.path.realpath(f) if os.path.isabs(f) else ""
    return f.startswith(ROOT + os.sep) and "/tests/" not in f
_cls = type(os.environ)
_get, _getitem, _contains = _cls.get, _cls.__getitem__, _cls.__contains__
busy = [False]
def get(self, k, d=None):
    if isinstance(k, str) and not busy[0] and _caller_in_repo(): seen.add(k)
    busy[0] = True
    try:
        return _get(self, k, d)
    finally:
        busy[0] = False
def getitem(self, k):
    if isinstance(k, str) and not busy[0] and _caller_in_repo(): seen.add(k)
    return _getitem(self, k)
_cls.get, _cls.__getitem__ = get, getitem
import server
from fastapi.testclient import TestClient
c = TestClient(server.app)
for p in ("/health", "/health/ready", "/api/system/status", "/api/agents", "/api/memory", "/metrics"):
    c.get(p)
c.post("/api/chat", json={"message": "hello"})
from agent_civilization.agents.real import create_civilization
create_civilization(agents_per_spec=1, impl="specialists")
print(json.dumps(sorted(x for x in seen if x.isupper())))
'''


def test_every_env_var_read_at_runtime_is_in_the_map(real_map):
    m, _ = real_map
    res = subprocess.run([sys.executable, "-c", ENV_TRACE], cwd=ROOT, capture_output=True, text=True, timeout=240, env=_env())
    assert res.returncode == 0, res.stderr[-2000:]
    read = set(json.loads(res.stdout.strip().splitlines()[-1]))
    assert len(read) >= 10
    missing = read - set(m["env_flags"])
    assert not missing, f"env vars read at runtime but absent from the map: {sorted(missing)}"
