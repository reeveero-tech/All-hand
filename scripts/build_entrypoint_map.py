#!/usr/bin/env python3
"""R-156 (M8/P1): reproducible map of entry points, import graph, dynamic loading,
routes, runtime flags and subsystem contracts at a snapshot SHA.

Usage::

    python3 scripts/build_entrypoint_map.py <repo> <sha> <out.json> [--md out.md]

Reads every tracked file from git objects at ``<sha>`` (never the working
tree), reusing the parsers of ``scripts/build_file_inventory.py`` (R-155).

Every relation carries an ``evidence`` tag:

* ``auto:<method>`` — extracted mechanically (AST, regex on config, git);
  verified against the running code by ``tests/test_r156_entrypoint_map.py``.
* ``declared`` — a grouping chosen by a human (e.g. which modules form the
  "LLM providers" subsystem). Declared items are listed under
  ``needs_human_review`` and are never presented as automatic facts.

Nothing is called orphan/dead because it is missing from the static graph:
such files are listed in ``unanalyzable_or_unresolved`` with a reason and the
next action (R-156 acceptance: "لا assume orphan=dead").

Exit codes: 0 ok, 1 self-check failed (a config references a path that does
not exist at the snapshot, or an entry point target is missing), 2 git/usage.
"""
from __future__ import annotations

import argparse
import ast
import json
import posixpath
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_file_inventory as bfi  # noqa: E402  (R-155 parsers; not modified)

HTTP_METHODS = ("get", "post", "put", "delete", "patch", "websocket", "head", "options")
REPO_PATH_RE = re.compile(r"(?<![\w./-])((?:scripts|tests|web|docs|agent_civilization|agos_kernel|src)/[\w./\-\[\]*]+|"
                          r"requirements[\w.-]*\.(?:lock|txt|in)|server\.py|run\.py|pytest\.ini|package(?:-lock)?\.json)")

# Human-declared subsystem grouping (needs_human_review). Contents are extracted automatically.
SUBSYSTEMS = {
    "api": ["server.py", "agent_civilization/civilization_routes.py"],
    "agent-runtime-real": ["agent_civilization/agents/real/real_agents.py", "agent_civilization/core/agents/real_dispatcher.py",
                           "agent_civilization/core/agents/base_agent.py"],
    "agent-runtime-specialists": ["agent_civilization/agents/real/specialist_runtime.py",
                                  "agent_civilization/agents/specialists/_templates/_factory.py"],
    "orchestration-routing": ["agent_civilization/pipeline/orchestrator.py", "agent_civilization/routing/auto_router.py",
                              "agent_civilization/registry/oss_agents.py"],
    "storage": ["agent_civilization/storage/supabase_store.py", "agent_civilization/storage/__init__.py",
                "docs/sql/001_init.sql", "docs/sql/002_tenant_isolation.sql"],
    "llm-providers": ["agent_civilization/core/llm.py"],
    "tools-and-safety": ["agent_civilization/agents/external_adapter.py", "agent_civilization/core/safety.py",
                         "agent_civilization/core/pools.py", "agent_civilization/agents/executors/data_worker.py"],
    "memory-and-learning": ["agent_civilization/core/experience.py", "agos_kernel/agent_production/memory_system.py",
                            "agos_kernel/agent_production/learning_runtime.py",
                            "agos_kernel/agent_production/memory_store.py"],
    "queue": ["agos_kernel/infrastructure/redis_queue.py"],
    "observability": ["agos_kernel/infrastructure/metrics.py", "agos_kernel/infrastructure/health.py",
                      "agos_kernel/infrastructure/circuit_breaker.py", "agent_civilization/core/sse.py"],
    "frontend-web": ["web/index.html", "web/src/main.jsx", "web/src/api.js"],
    "frontend-root": ["src/router.tsx", "src/start.ts", "src/server.ts", "src/lib/api.ts"],
}


class Snapshot:
    def __init__(self, repo: str, sha: str):
        self.repo = repo
        self.sha = bfi.git(repo, "rev-parse", f"{sha}^{{commit}}").strip()
        tree = bfi.list_tree(repo, self.sha)
        self.paths = [t[0] for t in tree]
        self.tracked = set(self.paths)
        blobs = bfi.read_blobs(repo, [t[1] for t in tree])
        self.text = {p: blobs[b].decode("utf-8", errors="replace") for p, b, _ in tree}
        self.raw = {p: blobs[b] for p, b, _ in tree}
        self.mod2path = {bfi.module_name(p): p for p in self.paths if p.endswith(".py")}

    def resolve(self, name: str) -> Optional[str]:
        n = name
        while n:
            if n in self.mod2path:
                return self.mod2path[n]
            n = n.rpartition(".")[0]
        return None


# ------------------------------------------------------------------ python --

def _scope_kind(stack: List[ast.AST]) -> str:
    """'top' for module level; 'conditional' inside module-level if/try; 'lazy' inside a function."""
    if any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) for s in stack):
        return "lazy"
    if any(isinstance(s, (ast.If, ast.Try, getattr(ast, "TryStar", ast.Try), ast.With)) for s in stack):
        return "conditional"
    return "top"


def _walk(node: ast.AST, stack: List[ast.AST]):
    for child in ast.iter_child_nodes(node):
        yield child, stack
        yield from _walk(child, stack + [child])


def python_facts(snap: Snapshot, path: str) -> Dict:
    src = snap.raw[path]
    out = {"imports": [], "routes": [], "env": [], "urls": [], "dynamic": [], "tables": [], "main": False,
           "parse_error": ""}
    try:
        tree = ast.parse(src, filename=path)
    except (SyntaxError, ValueError) as e:
        out["parse_error"] = f"{type(e).__name__}: {e}"
        return out
    pkg = bfi.module_name(path) or ""
    base_pkg = pkg if path.endswith("__init__.py") else pkg.rpartition(".")[0]
    for node, stack in _walk(tree, []):
        kind = _scope_kind(stack)
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                if node.level:
                    parts = base_pkg.split(".") if base_pkg else []
                    parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                    mod = ".".join(parts + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                names = [f"{mod}.{a.name}" if mod and a.name != "*" else mod for a in node.names] + ([mod] if mod else [])
            for n in dict.fromkeys(names):
                t = snap.resolve(n) if n else None
                if t and t != path:
                    out["imports"].append({"target": t, "kind": kind, "line": line, "evidence": "auto:ast-import"})
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if fname == "import_module" and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.JoinedStr) and a0.values and isinstance(a0.values[0], ast.Constant):
                    pre = a0.values[0].value
                    suf = a0.values[-1].value if len(a0.values) > 2 and isinstance(a0.values[-1], ast.Constant) else ""
                    targets = sorted(p for m, p in snap.mod2path.items() if m and m.startswith(pre) and m.endswith(suf)
                                     and "." not in m[len(pre):] and m != pre.rstrip("."))
                    out["dynamic"].append({"pattern": f"{pre}{{…}}{suf}", "line": line, "targets": targets,
                                           "evidence": "auto:ast-fstring-import_module"})
                elif isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    t = snap.resolve(a0.value)
                    if t:
                        out["imports"].append({"target": t, "kind": "dynamic", "line": line,
                                               "evidence": "auto:ast-import_module-literal"})
            elif fname == "spec_from_file_location" and len(node.args) > 1:
                lits = [n.value for n in ast.walk(node.args[1]) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                cand = "/".join(lits)
                hits = [p for p in snap.paths if p == cand or p.endswith("/" + cand) or cand.endswith(p)]
                out["dynamic"].append({"pattern": f"spec_from_file_location({cand})", "line": line, "targets": hits,
                                       "evidence": "auto:ast-spec_from_file_location"})
            # env reads: os.environ.get("X"), os.getenv("X"), environ["X"] handled below, helper(_env*)("X")
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                    and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", node.args[0].value):
                owner = ast.unparse(fn)
                if owner in ("os.environ.get", "os.getenv", "environ.get", "getenv") or "env" in fname.lower():
                    default = ast.unparse(node.args[1]) if len(node.args) > 1 else None
                    out["env"].append({"name": node.args[0].value, "line": line, "default": default, "via": owner})
            # FastAPI routes registered imperatively: app.add_api_route("/x", fn, methods=[...]) / app.mount("/x", ...)
            if isinstance(fn, ast.Attribute) and fn.attr in ("add_api_route", "mount") and node.args \
                    and isinstance(node.args[0], ast.Constant):
                out["routes"].append({"method": "MOUNT" if fn.attr == "mount" else "ROUTE", "path": node.args[0].value,
                                      "handler": None, "line": line, "file": path,
                                      "scope": kind, "condition": _condition(stack), "deps": [],
                                      "evidence": f"auto:ast-{fn.attr}"})
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in ("os.environ", "environ") \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            out["env"].append({"name": node.slice.value, "line": line, "default": "(required)", "via": "os.environ[]"})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in HTTP_METHODS \
                        and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                    deps = sorted({ast.unparse(a.args[0]) for a in ast.walk(node.args) if isinstance(a, ast.Call)
                                   and ast.unparse(a.func) in ("Security", "Depends") and a.args})
                    out["routes"].append({"method": d.func.attr.upper(), "path": d.args[0].value, "handler": node.name,
                                          "line": node.lineno, "file": path, "scope": kind,
                                          "condition": _condition(stack), "deps": deps,
                                          "evidence": "auto:ast-decorator"})
        elif isinstance(node, ast.If) and not stack and "__main__" in ast.unparse(node.test):
            out["main"] = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if re.match(r"https?://[\w.-]+", v):
                out["urls"].append({"url": re.match(r"https?://[\w.\-:]+[\w/.\-]*", v).group(0)[:120], "line": line})
            m = re.match(r"^/rest/v1/(\w+)", v)
            if m:
                out["tables"].append({"table": m.group(1), "line": line, "evidence": "auto:rest-path-literal"})
    # indirect env reads (file must touch os.environ): a loop/comprehension over a module-level
    # container of env-like names — tuple/list of str, or dict whose values are tuples starting
    # with a str — whose body calls <obj>.get(var) / getenv(var) / obj[var] on the loop variable.
    ENVNAME = re.compile(r"[A-Z][A-Z0-9_]{2,}")
    tuples: Dict[str, List[str]] = {}
    if b"environ" in src:
        for n in tree.body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                tgt = n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1 else getattr(n, "target", None)
                if not isinstance(tgt, ast.Name) or n.value is None:
                    continue
                v, items = n.value, []
                if isinstance(v, (ast.Tuple, ast.List)):
                    items = [e.value for e in v.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                elif isinstance(v, ast.Dict):
                    for val in v.values:
                        if isinstance(val, (ast.Tuple, ast.List)) and val.elts and isinstance(val.elts[0], ast.Constant) \
                                and isinstance(val.elts[0].value, str):
                            items.append(val.elts[0].value)
                if items and all(ENVNAME.fullmatch(x) for x in items):
                    tuples[tgt.id] = items
    for node in ast.walk(tree) if tuples else ():
        gens = []
        if isinstance(node, ast.For):
            gens = [(node.iter, node.target)]
        elif isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
            gens = [(g.iter, g.target) for g in node.generators]
        for it, target in gens:
            base = it
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and it.func.attr in ("items", "values"):
                base = it.func.value
            if not (isinstance(base, ast.Name) and base.id in tuples):
                continue
            names = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
            reads = False
            for c in ast.walk(node):
                if isinstance(c, ast.Call) and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id in names:
                    f = c.func
                    if (isinstance(f, ast.Attribute) and f.attr == "get") or ast.unparse(f) in ("os.getenv", "getenv"):
                        reads = True
                elif isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Name) and c.slice.id in names:
                    reads = True
            if reads:
                for name in tuples[base.id]:
                    out["env"].append({"name": name, "line": node.lineno, "default": None,
                                       "via": f"indirect: loop over {base.id}"})
    out["imports"] = sorted({(i["target"], i["kind"], i["line"], i["evidence"]) for i in out["imports"]})
    out["imports"] = [{"target": a, "kind": b, "line": c, "evidence": d} for a, b, c, d in out["imports"]]
    return out


def _condition(stack: List[ast.AST]) -> Optional[str]:
    conds = []
    for s in stack:
        if isinstance(s, ast.If):
            conds.append("if " + ast.unparse(s.test)[:80])
        elif isinstance(s, ast.Try):
            conds.append("try (" + ", ".join(ast.unparse(h.type)[:40] for h in s.handlers if h.type) + ")")
        elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            conds.append(f"inside def {s.name}()")
    return "; ".join(conds) or None


# ------------------------------------------------------------------ configs --

def dockerfile_facts(snap: Snapshot, problems: List[str]) -> Dict:
    t = snap.text.get("Dockerfile", "")
    stages, copies, cmd = [], [], None
    for i, ln in enumerate(t.splitlines(), 1):
        m = re.match(r"^\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?", ln, re.I)
        if m:
            stages.append({"image": m.group(1), "name": m.group(2), "line": i})
        m = re.match(r"^\s*COPY\s+(?:--from=(\S+)\s+)?(.+)$", ln, re.I)
        if m:
            parts = m.group(2).split()
            srcs, dst = parts[:-1], parts[-1]
            for s in srcs:
                if s.startswith("--"):
                    continue
                exists = m.group(1) is not None or s in (".", "./") or s.rstrip("/") in snap.tracked or \
                    any(p.startswith(s.rstrip("/") + "/") for p in snap.paths) or \
                    any(re.fullmatch(s.replace(".", r"\.").replace("*", ".*"), p) for p in snap.paths)
                copies.append({"src": s, "dst": dst, "from_stage": m.group(1), "line": i, "exists_in_snapshot": exists})
                if not exists:
                    problems.append(f"Dockerfile:{i}: COPY source '{s}' not found at snapshot")
        m = re.match(r"^\s*CMD\s+(.+)$", ln)
        if m:
            cmd = {"raw": m.group(1), "line": i}
    target = None
    if cmd:
        m = re.search(r"uvicorn\s+([\w.]+):(\w+)", cmd["raw"])
        if m:
            target = {"module": m.group(1), "attr": m.group(2), "path": snap.mod2path.get(m.group(1))}
            if not target["path"]:
                problems.append(f"Dockerfile: CMD module {m.group(1)} not found")
    return {"stages": stages, "copies": copies, "cmd": cmd, "asgi_target": target, "evidence": "auto:dockerfile-parse"}


def render_facts(snap: Snapshot, problems: List[str]) -> Dict:
    t = snap.text.get("render.yaml", "")
    out = {}
    for key in ("buildCommand", "startCommand"):
        m = re.search(rf"^\s*{key}:\s*(.+)$", t, re.M)
        if m:
            cmdline = m.group(1).strip()
            refs = sorted(set(REPO_PATH_RE.findall(cmdline)))
            cds = re.findall(r"\bcd\s+([\w./-]+)", cmdline)
            for r in refs + cds:
                if r.rstrip("/") not in snap.tracked and not any(p.startswith(r.rstrip("/") + "/") for p in snap.paths):
                    problems.append(f"render.yaml {key}: path '{r}' not found at snapshot")
            out[key] = {"command": cmdline, "paths": refs, "cd": cds}
    m = re.search(r"uvicorn\s+([\w.]+):(\w+)", out.get("startCommand", {}).get("command", ""))
    if m:
        out["asgi_target"] = {"module": m.group(1), "attr": m.group(2), "path": snap.mod2path.get(m.group(1))}
    out["evidence"] = "auto:render-yaml-regex"
    return out


def workflow_facts(snap: Snapshot, problems: List[str]) -> List[Dict]:
    res = []
    for p in snap.paths:
        if not p.startswith(".github/workflows/"):
            continue
        t = snap.text[p]
        lines = t.splitlines()
        jobs, cur, in_jobs, in_run, run_indent = {}, None, False, False, 0
        for i, ln in enumerate(lines, 1):
            if re.match(r"^jobs:\s*$", ln):
                in_jobs = True
                continue
            if in_jobs and re.match(r"^\S", ln):
                in_jobs = False
            if not in_jobs:
                continue
            m = re.match(r"^  ([\w-]+):\s*$", ln)
            if m:
                cur = m.group(1)
                jobs[cur] = {"line": i, "runs": [], "working_directory": [], "needs": []}
                in_run = False
                continue
            if cur is None:
                continue
            m = re.match(r"^(\s+)(?:- )?run:\s*(.*)$", ln)
            if m:
                body = m.group(2).strip()
                if body in ("|", ">", "|-", ">-"):
                    in_run, run_indent = True, len(m.group(1)) + 2
                    continue
                jobs[cur]["runs"].append({"line": i, "cmd": body})
                in_run = False
                continue
            if in_run:
                if ln.strip() and (len(ln) - len(ln.lstrip())) < run_indent:
                    in_run = False
                elif ln.strip() and not ln.strip().startswith("#"):
                    jobs[cur]["runs"].append({"line": i, "cmd": ln.strip()})
                    continue
            m = re.match(r"^\s+working-directory:\s*(\S+)", ln)
            if m:
                jobs[cur]["working_directory"].append(m.group(1))
            m = re.match(r"^    needs:\s*(.+)$", ln)
            if m:
                jobs[cur]["needs"] = re.findall(r"[\w-]+", m.group(1))
        for j in jobs.values():
            refs = set()
            for r in j["runs"]:
                c = r["cmd"]
                if c.startswith("#"):
                    continue
                for x in REPO_PATH_RE.findall(c):
                    x = x.rstrip(".,;:)'\"")
                    if "*" in x or "$" in x:
                        continue
                    refs.add(x)
            j["paths"] = sorted(refs)
            for x in j["paths"]:
                base_ok = x.rstrip("/") in snap.tracked or any(q.startswith(x.rstrip("/") + "/") for q in snap.paths) \
                    or (x + ".py") in snap.tracked        # module-style reference (e.g. pkg/mod in a prefix list)
                # paths relative to a working-directory (e.g. web/) are also valid
                wd_ok = any((w.rstrip("/") + "/" + x) in snap.tracked for w in j["working_directory"])
                if not (base_ok or wd_ok):
                    gen = re.search(r"(dist|build|node_modules|\.log|\.json|coverage|\.xml)", x)
                    if not gen:
                        problems.append(f"{p}: job references '{x}' which is not tracked at snapshot")
        res.append({"file": p, "jobs": jobs, "evidence": "auto:workflow-line-parse"})
    return res


# ------------------------------------------------------------------- build --

def build_map(repo: str, sha: str) -> Tuple[Dict, List[str]]:
    snap = Snapshot(repo, sha)
    problems: List[str] = []
    py = {p: python_facts(snap, p) for p in snap.paths if p.endswith(".py")}

    # graph
    edges = []
    for p, f in py.items():
        for i in f["imports"]:
            edges.append({"from": p, "to": i["target"], "kind": i["kind"], "line": i["line"], "evidence": i["evidence"]})
            parts = (bfi.module_name(i["target"]) or "").split(".")
            for k in range(1, len(parts)):            # parent packages execute too
                pk = snap.mod2path.get(".".join(parts[:k]))
                if pk and pk != p:
                    edges.append({"from": p, "to": pk, "kind": i["kind"], "line": i["line"], "evidence": "auto:parent-package"})
        for d in f["dynamic"]:
            for t in d["targets"]:
                edges.append({"from": p, "to": t, "kind": "dynamic", "line": d["line"], "evidence": d["evidence"]})
    for p in snap.paths:
        if p.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
            internal, _ = bfi.js_imports(p, snap.text[p], snap.tracked)
            for t in sorted(internal):
                edges.append({"from": p, "to": t, "kind": "top", "line": 0, "evidence": "auto:js-import-regex"})
        if p.endswith(".html"):
            for s in re.findall(r"<script[^>]+src=['\"]([^'\"]+)['\"]", snap.text[p]):
                c = posixpath.normpath(posixpath.join(posixpath.dirname(p), s.lstrip("/")))
                if c in snap.tracked:
                    edges.append({"from": p, "to": c, "kind": "top", "line": 0, "evidence": "auto:html-script-src"})
    if "src/router.tsx" in snap.tracked:
        for p in snap.paths:
            if p.startswith("src/routes/") and p.endswith((".tsx", ".ts")):
                edges.append({"from": "src/router.tsx", "to": p, "kind": "dynamic", "line": 0,
                              "evidence": "convention:tanstack-file-route (needs_human_review)"})
    seen, uniq = set(), []
    for e in sorted(edges, key=lambda e: (e["from"], e["to"], e["kind"], e["line"], e["evidence"])):
        k = (e["from"], e["to"], e["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    edges = uniq
    adj: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    for e in edges:
        adj[e["kind"]][e["from"]].add(e["to"])

    def closure(starts, kinds):
        seen_, stack = set(), list(starts)
        while stack:
            n = stack.pop()
            if n in seen_:
                continue
            seen_.add(n)
            for k in kinds:
                stack.extend(adj[k].get(n, ()))
        return seen_

    docker = dockerfile_facts(snap, problems)
    render = render_facts(snap, problems)
    workflows = workflow_facts(snap, problems)

    # entry points
    entry = []
    tgt = (docker.get("asgi_target") or {}).get("path")
    if tgt:
        entry.append({"id": "deploy:docker-cmd", "kind": "server", "target": tgt,
                      "how": docker["cmd"]["raw"], "source": f"Dockerfile:{docker['cmd']['line']}", "evidence": "auto"})
    rt = (render.get("asgi_target") or {}).get("path")
    if rt:
        entry.append({"id": "deploy:render-start", "kind": "server", "target": rt,
                      "how": render["startCommand"]["command"], "source": "render.yaml", "evidence": "auto"})
    for p, f in sorted(py.items()):
        if f["main"] and not p.startswith("tests/"):
            entry.append({"id": f"cli:{p}", "kind": "cli", "target": p, "how": f"python3 {p}",
                          "source": f"{p} (if __name__ == '__main__')", "evidence": "auto"})
    for p in snap.paths:
        if p.startswith("scripts/") and p.endswith(".py") and not py[p]["main"]:
            entry.append({"id": f"script:{p}", "kind": "cli-toplevel", "target": p, "how": f"python3 {p}",
                          "source": "module-level script (no __main__ guard)", "evidence": "auto"})
        if p.startswith("scripts/") and p.endswith(".sh"):
            entry.append({"id": f"script:{p}", "kind": "shell", "target": p, "how": f"bash {p}",
                          "source": "shell script", "evidence": "auto"})
    ini = snap.text.get("pytest.ini", "")
    tp = re.search(r"^testpaths\s*=\s*(.+)$", ini, re.M)
    tests = sorted(p for p in snap.paths if re.match(r"tests/(?:.*/)?test_[^/]*\.py$", p))
    entry.append({"id": "tests:pytest", "kind": "tests", "target": "tests/", "how": "pytest",
                  "source": f"pytest.ini testpaths={tp.group(1).strip() if tp else '(default)'}",
                  "files": len(tests), "file_list": tests, "evidence": "auto"})
    webtests = sorted(p for p in snap.paths if p.startswith("web/tests/") and p.endswith(".test.mjs"))
    if webtests:
        entry.append({"id": "tests:web-node", "kind": "tests", "target": "web/tests/", "how": "npm test (node --test)",
                      "source": "web/package.json scripts.test", "files": len(webtests), "evidence": "auto"})
    for pj, kind in (("web/package.json", "frontend-web"), ("package.json", "frontend-root")):
        if pj in snap.tracked:
            scripts = json.loads(snap.text[pj]).get("scripts", {})
            entry.append({"id": f"build:{pj}", "kind": kind, "target": posixpath.dirname(pj) or ".",
                          "how": "; ".join(f"{k}={v}" for k, v in sorted(scripts.items())), "source": pj, "evidence": "auto"})
    if "web/index.html" in snap.tracked:
        entry.append({"id": "frontend:web/index.html", "kind": "frontend-web", "target": "web/index.html",
                      "how": "vite build (web) → web/dist, served by server.py when web/dist exists",
                      "source": "web/index.html + server.py WEB_DIST", "evidence": "auto"})
    for p in ("src/router.tsx", "src/start.ts", "src/server.ts"):
        if p in snap.tracked:
            entry.append({"id": f"frontend:{p}", "kind": "frontend-root", "target": p,
                          "how": "TanStack Start (vite.config.ts) — built+linted in CI; deployment target UNRESOLVED (Q-002)",
                          "source": "vite.config.ts / package.json", "evidence": "convention (needs_human_review)"})
    for w in workflows:
        for j, jd in sorted(w["jobs"].items()):
            entry.append({"id": f"ci:{w['file']}#{j}", "kind": "ci-job", "target": w["file"], "how": f"{len(jd['runs'])} run line(s)",
                          "paths": jd["paths"], "source": f"{w['file']}:{jd['line']}", "evidence": "auto"})
    for e in entry:
        if e["target"].rstrip("/") not in snap.tracked and not any(p.startswith(e["target"].rstrip("/") + "/") for p in snap.paths) \
                and e["target"] != ".":
            problems.append(f"entry point {e['id']} target '{e['target']}' missing")

    server = tgt or rt
    static_top = closure([server], ["top"]) if server else set()
    static_all = closure([server], ["top", "conditional", "lazy", "dynamic"]) if server else set()

    # runtime routes only: test files and audit scripts build throw-away apps
    routes = [r for p, f in py.items() if not p.startswith(("tests/", "docs/")) for r in f["routes"]]
    routes.sort(key=lambda r: (r["file"], r["line"], r["method"], r["path"]))
    env = defaultdict(list)
    for p, f in py.items():
        for e in f["env"]:
            env[e["name"]].append({"file": p, "line": e["line"], "default": e["default"]})
    env = {k: sorted(v, key=lambda x: (x["file"], x["line"])) for k, v in sorted(env.items())}

    subs = {}
    for name, files in SUBSYSTEMS.items():
        present = [f for f in files if f in snap.tracked]
        info = {"files": present, "missing_declared": [f for f in files if f not in snap.tracked],
                "grouping_evidence": "declared (needs_human_review)",
                "in_server_static_closure": {f: f in static_all for f in present},
                "in_server_top_level_closure": {f: f in static_top for f in present}}
        pyf = [f for f in present if f.endswith(".py")]
        info["routes"] = [f"{r['method']} {r['path']}" for r in routes if r["file"] in pyf]
        info["env_flags"] = sorted({e for e, uses in env.items() for u in uses if u["file"] in pyf})
        info["external_urls"] = sorted({u["url"] for f in pyf for u in py[f]["urls"]})
        tables = sorted({t["table"] for f in pyf for t in py[f]["tables"]})
        for f in present:
            if f.endswith(".sql"):
                tables += re.findall(r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)", snap.text[f])
        info["tables"] = sorted(set(tables))
        info["public_api"] = {f: [n for n in _public(snap, f)] for f in pyf}
        info["importers"] = {f: sorted({e["from"] for e in edges if e["to"] == f})[:12] for f in present}
        subs[name] = info

    runtime_paths = {
        "agent_impl": {
            "flag": "AGOS_AGENT_IMPL", "uses": env.get("AGOS_AGENT_IMPL", []),
            "values": _valid_impls(snap),
            "real": "create_civilization(impl='real') → RealSpecialistAgent → real_dispatcher.dispatch (default)",
            "specialists": "create_civilization(impl='specialists') → specialist_runtime.SpecialistBackedAgent → "
                           "importlib agent_civilization.agents.specialists.{spec} → _templates._factory → {spec}_base",
            "evidence": "auto:env-read + declared description (needs_human_review)",
        },
        "frontend": {
            "web": "served by server.py from web/dist when it exists (conditional SPA route + /assets mount)",
            "root_src": "built in CI only; external deployment UNRESOLVED — Q-002 (17 inventory rows stay unresolved)",
        },
        "queue": {
            "redis_queue_importers": sorted({e["from"] for e in edges if e["to"] == "agos_kernel/infrastructure/redis_queue.py"}),
            "in_server_static_closure": "agos_kernel/infrastructure/redis_queue.py" in static_all,
            "in_server_top_level_closure": "agos_kernel/infrastructure/redis_queue.py" in static_top,
            "note": "importing the module does not mean a Redis connection is opened — that depends on REDIS_URL at runtime",
        },
    }

    analyzed = set(py) | {p for p in snap.paths if p.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".html"))}
    unanalyzable = []
    for p in snap.paths:
        ft = bfi.file_type(p)
        if p in py and py[p]["parse_error"]:
            unanalyzable.append({"path": p, "reason": f"python parse error: {py[p]['parse_error']}",
                                 "next_action": "fix syntax or record as non-code"})
        elif ft == "image":
            unanalyzable.append({"path": p, "reason": "binary image — no code relations can be parsed",
                                 "next_action": "role from referencing docs (R-155 row); no action"})
        elif ft == "shell":
            unanalyzable.append({"path": p, "reason": "shell script — relations extracted by text only (no shell AST)",
                                 "next_action": "smoke/integration via CI job that runs it (see ci entries)"})
        elif ft == "sql":
            unanalyzable.append({"path": p, "reason": "SQL executed by an external database (PostgreSQL/Supabase); "
                                 "only CREATE TABLE names extracted", "next_action": "covered by CI job storage (live PostgREST)"})
        elif p.startswith("src/") and ft != "markdown":
            unanalyzable.append({"path": p, "reason": "static graph resolved, but deployment role cannot be determined "
                                 "from the repository (Q-002)", "next_action": "owner answers Q-002; keep KEEP + CI build"})
    cli_like = [e for e in entry if e["kind"] in ("cli", "cli-toplevel")]
    review = [
        {"item": "SUBSYSTEMS grouping (which files form api/storage/llm/tools/memory/queue)", "why": "declared by a human"},
        {"item": "TanStack file-based routes src/routes/* → src/router.tsx", "why": "framework convention, not an import"},
        {"item": "runtime_paths descriptions", "why": "prose summary of auto-extracted edges"},
        {"item": "external URLs in llm/tools", "why": "literal strings — whether they are called at runtime depends on flags"},
    ]
    m = {
        "snapshot_sha": snap.sha,
        "counts": {"tracked_files": len(snap.paths), "python_files": len(py),
                   "edges": len(edges), "edges_by_kind": dict(sorted(Counter(e["kind"] for e in edges).items())),
                   "entry_points": len(entry), "entry_points_by_kind": dict(sorted(Counter(e["kind"] for e in entry).items())),
                   "routes": len(routes), "env_flags": len(env), "server_static_top_closure": len(static_top),
                   "server_static_full_closure": len(static_all), "unanalyzable_or_unresolved": len(unanalyzable),
                   "cli_entry_points": len(cli_like), "analyzed_code_files": len(analyzed)},
        "entry_points": entry,
        "deploy": {"docker": docker, "render": render},
        "ci": workflows,
        "routes": routes,
        "env_flags": env,
        "subsystems": subs,
        "runtime_paths": runtime_paths,
        "server_closure": {"top_level": sorted(static_top), "all_kinds": sorted(static_all)},
        "edges": edges,
        "unanalyzable_or_unresolved": unanalyzable,
        "needs_human_review": review,
        "problems": sorted(problems),
    }
    return m, sorted(problems)


def _public(snap: Snapshot, path: str) -> List[str]:
    try:
        tree = ast.parse(snap.raw[path])
    except (SyntaxError, ValueError):
        return []
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not n.name.startswith("_")][:15]


def _valid_impls(snap: Snapshot) -> List[str]:
    for p in ("agent_civilization/agents/real/specialist_runtime.py", "agent_civilization/agents/real/real_agents.py"):
        m = re.search(r"VALID_IMPLS\s*=\s*\(([^)]*)\)", snap.text.get(p, ""))
        if m:
            return re.findall(r"['\"](\w+)['\"]", m.group(1))
    return []


# ------------------------------------------------------------------ report --

def render_md(m: Dict, json_name: str, cmd: str) -> str:
    c = m["counts"]
    L = [f"# خريطة المسارات ونقاط الدخول — اللقطة `{m['snapshot_sha'][:7]}` (R-156)", "",
         "> **مُولَّدة آلياً** من `scripts/build_entrypoint_map.py` — لا تُحرَّر يدوياً. إعادة الإنتاج:", ">", f"> `{cmd}`", "",
         f"- اللقطة: `{m['snapshot_sha']}` (مقروءة من كائنات git). البيانات الكاملة: [`{json_name}`]({json_name}).",
         f"- ملفات متعقبة {c['tracked_files']}؛ Python {c['python_files']}؛ ملفات شيفرة حُللت {c['analyzed_code_files']}؛ "
         f"حواف {c['edges']} ({'، '.join(f'{k}={v}' for k, v in c['edges_by_kind'].items())}).",
         f"- نقاط دخول {c['entry_points']} ({'، '.join(f'{k}={v}' for k, v in c['entry_points_by_kind'].items())})؛ "
         f"مسارات HTTP {c['routes']}؛ متغيرات بيئة {c['env_flags']}.",
         f"- إغلاق الخادم الساكن: {c['server_static_top_closure']} ملفاً على مستوى الوحدة، {c['server_static_full_closure']} "
         "مع الاستيرادات الكسولة/الشرطية/الديناميكية.",
         "- **تمييز الأدلة**: `auto:*` مستخرج آلياً ومتحقق منه بالتشغيل في `tests/test_r156_entrypoint_map.py`؛ "
         "`declared`/`convention` تجميع أو عرف يحتاج مراجعة بشرية (القسم الأخير). **لا تُدّعى تغطية دلالية كاملة.**",
         f"- مشكلات الفحص الذاتي: {len(m['problems'])}" + ("" if not m["problems"] else " — " + "؛ ".join(m["problems"])), "",
         "## المخطط", "", "```mermaid", "flowchart LR"]
    L += ['  docker["Dockerfile CMD"] --> server["server.py (FastAPI app)"]',
          '  render["render.yaml startCommand"] --> server',
          '  server --> routes["civilization_routes (registered in try/ImportError)"]',
          '  server --> real["agents.real (AGOS_AGENT_IMPL=real, default)"]',
          '  real -. "AGOS_AGENT_IMPL=specialists" .-> spec["specialist_runtime → importlib specialists.{spec} → _factory → {spec}_base"]',
          '  server --> pipe["routing + pipeline (orchestrator)"]',
          '  server --> store["storage (Supabase/PostgREST; docs/sql)"]',
          '  server --> llm["core.llm (OpenRouter / keyless public LLM)"]',
          '  server --> tools["external_adapter + safety + pools (network tools)"]',
          '  server --> mem["experience (memory) ; agos_kernel memory/learning"]',
          '  server --> obs["metrics / health / circuit_breaker / sse"]',
          '  webb["web/ vite build"] --> dist["web/dist"] -. "if WEB_DIST.exists()" .-> server',
          '  rootb["src/ TanStack build (CI only)"] -. "deployment ? Q-002" .-> unknown["unresolved"]',
          ('  server --> queue["agos_kernel redis_queue (module-level import via agos_kernel.infrastructure)"]'
           if m["runtime_paths"]["queue"]["in_server_top_level_closure"] else
           '  server -. "lazy/conditional" .-> queue["agos_kernel redis_queue"]'),
          '  ci["CI workflows"] --> tests["pytest tests/ + web node tests"]',
          '  ci --> gates["governance: no-delete, atomic v1/v2, status evidence"]', "```", "",
          "> المخطط ملخص بشري (`declared`) لحواف `auto:*` في JSON؛ المرجع هو JSON.", "",
          "## نقاط الدخول", "", "| المعرف | النوع | الهدف | الدليل |", "|---|---|---|---|"]
    for e in m["entry_points"]:
        L.append(f"| `{e['id']}` | {e['kind']} | `{e['target']}` | {e['evidence']} — {e['source']} |")
    L += ["", "## مسارات HTTP (من AST)", "", "| الطريقة | المسار | المعالج | الملف:السطر | النطاق/الشرط | التبعيات |", "|---|---|---|---|---|---|"]
    for r in m["routes"]:
        L.append(f"| {r['method']} | `{r['path']}` | {r['handler'] or '—'} | `{r['file']}:{r['line']}` | "
                 f"{r['scope']}{(' — ' + r['condition']) if r['condition'] else ''} | {', '.join(r['deps']) or '—'} |")
    L += ["", "## النشر والبناء", "", f"- Dockerfile: مراحل {[s['name'] or s['image'] for s in m['deploy']['docker']['stages']]}؛ "
          f"CMD `{(m['deploy']['docker']['cmd'] or {}).get('raw')}`؛ COPY: "
          + "، ".join(f"`{x['src']}`{'' if x['exists_in_snapshot'] else ' (مفقود!)'}" for x in m["deploy"]["docker"]["copies"]),
          f"- render.yaml: build `{m['deploy']['render'].get('buildCommand', {}).get('command')}`؛ start "
          f"`{m['deploy']['render'].get('startCommand', {}).get('command')}`", "", "## وظائف CI", ""]
    for w in m["ci"]:
        for j, jd in sorted(w["jobs"].items()):
            L.append(f"- `{w['file']}` ‏**{j}** (سطر {jd['line']}): مسارات مستخدمة: {', '.join(f'`{x}`' for x in jd['paths']) or '—'}")
    L += ["", "## الأنظمة الفرعية والعقود", ""]
    for n, s in m["subsystems"].items():
        L += [f"### {n}", "", f"- الملفات (تجميع `declared`): {', '.join(f'`{f}`' for f in s['files'])}",
              f"- في إغلاق الخادم الساكن: {', '.join(f'`{k}`={v}' for k, v in s['in_server_static_closure'].items())}"]
        if s["routes"]:
            L.append(f"- مسارات: {', '.join(s['routes'])}")
        if s["env_flags"]:
            L.append(f"- متغيرات بيئة: {', '.join(f'`{e}`' for e in s['env_flags'])}")
        if s["external_urls"]:
            L.append(f"- عناوين خارجية حرفية: {', '.join(f'`{u}`' for u in s['external_urls'][:10])}")
        if s["tables"]:
            L.append(f"- جداول: {', '.join(f'`{t}`' for t in s['tables'])}")
        api = {f: v for f, v in s["public_api"].items() if v}
        if api:
            L.append("- واجهة عامة: " + "؛ ".join(f"`{f}`: {', '.join(v)}" for f, v in api.items()))
        L.append("")
    rp = m["runtime_paths"]
    L += ["## مسارات التشغيل", "", f"- `AGOS_AGENT_IMPL` ∈ {rp['agent_impl']['values']}: **real** — {rp['agent_impl']['real']}؛ "
          f"**specialists** — {rp['agent_impl']['specialists']}.",
          f"- الواجهة: web — {rp['frontend']['web']}؛ src — {rp['frontend']['root_src']}.",
          f"- الطابور: مستوردو `redis_queue`: {rp['queue']['redis_queue_importers']}؛ في الإغلاق الكامل={rp['queue']['in_server_static_closure']}، "
          f"على مستوى الوحدة={rp['queue']['in_server_top_level_closure']}.", "",
          "## متغيرات البيئة (قراءات حرفية)", "", "| المتغير | المواضع |", "|---|---|"]
    for k, v in m["env_flags"].items():
        L.append(f"| `{k}` | " + "، ".join(f"`{u['file']}:{u['line']}`" for u in v[:4]) + (" …" if len(v) > 4 else "") + " |")
    L += ["", "## ما تعذر تحليله أو لم يُحسم", "", "| الملف | السبب | الإجراء التالي |", "|---|---|---|"]
    for u in m["unanalyzable_or_unresolved"]:
        L.append(f"| `{u['path']}` | {u['reason']} | {u['next_action']} |")
    L += ["", "## يحتاج مراجعة بشرية", ""] + [f"- {r['item']} — {r['why']}" for r in m["needs_human_review"]]
    return "\n".join(L).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("sha")
    ap.add_argument("out")
    ap.add_argument("--md")
    ap.add_argument("--command-note", default="")
    a = ap.parse_args(argv)
    try:
        m, problems = build_map(a.repo, a.sha)
    except (bfi.GitError, OSError, ValueError) as e:
        print(f"::error::entrypoint-map: {e}")
        return 2
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    if a.md:
        rel = posixpath.relpath(a.out, posixpath.dirname(a.md) or ".")
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(render_md(m, rel, a.command_note or "python3 scripts/build_entrypoint_map.py …"))
    for p in problems:
        print(f"::error::entrypoint-map self-check: {p}")
    c = m["counts"]
    print(f"snapshot={m['snapshot_sha']} entry_points={c['entry_points']} routes={c['routes']} edges={c['edges']} "
          f"server_closure={c['server_static_top_closure']}/{c['server_static_full_closure']} "
          f"unanalyzable={c['unanalyzable_or_unresolved']} problems={len(problems)} self_check={'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
