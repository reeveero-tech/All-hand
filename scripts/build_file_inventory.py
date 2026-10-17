#!/usr/bin/env python3
"""R-155 (M8/P1): deterministic inventory of EVERY tracked file at a snapshot SHA.

Usage::

    python3 scripts/build_file_inventory.py <repo> <sha> <out.csv> \
        [--coverage cov_files.json] [--review-date YYYY-MM-DD] [--summary out.json]

Source of truth for the path list is ``git ls-files -z`` evaluated AT ``<sha>``
(via ``git ls-tree -r -z --full-tree <sha>``, which is what ``git ls-files``
returns for a clean checkout of that commit). Contents, blob SHAs and sizes
are read from git objects, never from the working tree, so the result is the
same on any machine and does not include files added after the snapshot.

Evidence used per row (all static or recorded, none inferred from age alone):
  * git: blob sha, size, last commit touching the path (author date, UTC);
  * Python AST import graph (absolute + relative imports, imports inside
    functions) resolved to tracked modules;
  * dynamic loading patterns found in source: ``importlib.import_module(f"pkg.{x}")``
    and ``f"pkg.{x}_suffix"`` (expanded to every matching module),
    ``spec_from_file_location(..., "path.py")``, and shim globs in tests;
  * runtime entry points parsed from Dockerfile CMD / render.yaml startCommand
    (``uvicorn <module>:app``), CI workflow ``run:`` lines, package.json
    scripts, HTML ``<script src>``, TanStack file-based routes;
  * JS/TS import graph (relative and ``@/`` alias);
  * textual references (exact path or unique basename) from other files;
  * optional per-file coverage of the pytest suite (execution evidence).

Absence of an import is NEVER treated as proof of non-use: such files get
``inventory_status=unresolved`` with a reason and a next action. No row ever
gets a DELETE decision (D-064 / R-152).

Exit codes: 0 ok, 1 self-check failed (row count != ls-files count, DELETE
decision present, duplicate paths), 2 usage/git error.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import posixpath
import re
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

COLUMNS = [
    "path", "file_type", "last_modified_utc", "git_blob_sha", "size_bytes", "owner_component",
    "purpose", "entrypoint_or_imported_by", "dynamic_registration_or_reference", "public_contract",
    "dependencies", "current_tests", "last_evidence_sha", "test_categories", "risk_level",
    "inventory_status", "decision_no_delete", "known_duplicate_or_overlap", "compatibility_adapter",
    "gaps", "next_action", "confidence", "reviewed_by", "review_date_utc",
]
STATUSES = ("active", "shared", "legacy-compatible", "generated", "test-only", "docs-only", "unresolved")
DECISIONS = ("KEEP", "UPGRADE", "INTEGRATE", "ADAPTER", "DOCUMENT")
# Roles that static evidence cannot settle because they depend on an OPEN owner question.
# Only applied while the question is open in docs/governance/OPEN_QUESTIONS.md at the snapshot.
OPEN_ROLE_QUESTIONS = {
    "src/": ("Q-002",
             "root TanStack Start app is built+linted by CI but NOT deployed by Dockerfile/render.yaml (they build web/); "
             "vite.config.ts targets nitro/cloudflare and .lovable/ exists, so an external deployment cannot be ruled in or out "
             "from the repository — fate is open question Q-002",
             "owner answers Q-002 (is src/ deployed anywhere, e.g. Lovable/Cloudflare?) then R-158 records active vs legacy; "
             "until then KEEP + keep CI build green"),
}
REVIEWER = "Builder Agent — automated static+git+coverage analysis (scripts/build_file_inventory.py)"
MAXLIST = 5
STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}


class GitError(Exception):
    pass


def git(repo: str, *args: str, binary: bool = False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- git layer --

def list_tree(repo: str, sha: str) -> List[Tuple[str, str, int]]:
    """(path, blob_sha, size) for every tracked blob at sha, sorted by path bytes."""
    out = git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", sha, binary=True)
    rows = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        parts = meta.split()
        if parts[1] != b"blob":          # submodules (commit) are not files
            continue
        size = int(parts[3]) if parts[3] != b"-" else 0
        rows.append((path.decode("utf-8"), parts[2].decode(), size))
    rows.sort(key=lambda r: r[0].encode("utf-8"))
    return rows


def read_blobs(repo: str, shas: Iterable[str]) -> Dict[str, bytes]:
    uniq = sorted(set(shas))
    p = subprocess.run(["git", "-C", repo, "cat-file", "--batch"], input=("\n".join(uniq) + "\n").encode(),
                       capture_output=True)
    if p.returncode != 0:
        raise GitError(p.stderr.decode(errors="replace"))
    data, out, i = p.stdout, {}, 0
    for s in uniq:
        nl = data.index(b"\n", i)
        header = data[i:nl].split()
        size = int(header[2])
        out[s] = data[nl + 1: nl + 1 + size]
        i = nl + 1 + size + 1
    return out


def last_changes(repo: str, sha: str, paths: Set[str]) -> Dict[str, Tuple[str, str]]:
    """path -> (commit_sha, author date UTC) of the last commit at/before sha touching it."""
    out = git(repo, "-c", "core.quotepath=off", "log", "--format=%x00%H %aI", "--name-only",
              "--no-renames", "-z", sha, binary=True).decode("utf-8", errors="replace")
    res: Dict[str, Tuple[str, str]] = {}
    commit = date = None
    for tok in out.split("\0"):
        tok = tok.strip("\n")
        if not tok:
            continue
        m = re.match(r"^([0-9a-f]{40}) (\S+)$", tok)
        if m:
            commit, date = m.group(1), m.group(2)
            continue
        for p in tok.split("\n"):
            if p and p in paths and p not in res:
                res[p] = (commit, _utc(date))
        if len(res) == len(paths):
            break
    return res


def _utc(iso: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- classifiers --

def file_type(path: str) -> str:
    b = posixpath.basename(path)
    if b in ("Dockerfile", ".dockerignore"):
        return "docker"
    if b == "render.yaml":
        return "render-config"
    if path.startswith(".github/workflows/"):
        return "ci-workflow"
    if re.match(r"requirements.*\.(txt|in|lock)$", b) or b in ("package.json", "package-lock.json"):
        return "dependency-manifest" if not b.endswith(("lock", "-lock.json")) else "dependency-lock"
    ext = b.rsplit(".", 1)[-1].lower() if "." in b else ""
    return {"py": "python", "md": "markdown", "log": "log", "json": "json", "jsonl": "jsonl",
            "ts": "typescript", "tsx": "tsx", "jsx": "jsx", "js": "javascript", "mjs": "javascript",
            "css": "css", "html": "html", "sql": "sql", "sh": "shell", "csv": "csv", "txt": "text",
            "png": "image", "yml": "yaml", "yaml": "yaml", "ini": "ini", "example": "env-template",
            "gitignore": "git-config", "lock": "dependency-lock"}.get(ext, ext or "other")


def component(path: str) -> str:
    p = path.split("/")
    if path == "server.py":
        return "api-server"
    if path == "run.py":
        return "cli-runner"
    if p[0] == "agent_civilization":
        if path.startswith("agent_civilization/agents/specialists/_templates/"):
            return "agent_civilization/specialists-templates"
        if path.startswith("agent_civilization/agents/specialists/"):
            return "agent_civilization/specialists"
        sub = "/".join(p[1:3]) if len(p) > 3 and p[1] in ("agents", "core") else (p[1] if len(p) > 2 else "")
        return "agent_civilization" + (f"/{sub.removesuffix('.py')}" if sub else "")
    if p[0] == "agos_kernel":
        return f"agos_kernel/{p[1]}" if len(p) > 2 else "agos_kernel"
    if p[0] == "tests":
        return "tests"
    if p[0] == "scripts":
        return "scripts/" + (p[1] if len(p) > 2 else "tools")
    if p[0] == "web":
        return "frontend-web (deployed)"
    if p[0] in ("src", "public") or path in ("vite.config.ts", "tsconfig.json", "eslint.config.js",
                                              "components.json"):
        return "frontend-root (TanStack Start)"
    if p[0] == ".github":
        return "ci"
    if p[0] == "docs":
        if p[1] == "audit":
            return f"docs/audit/{p[2]}" if len(p) > 3 else "docs/audit"
        if p[1] == "governance":
            return "docs/governance/revival-scale-package" if "roadmaps/revival-scale" in path else "docs/governance"
        return f"docs/{p[1]}"
    if posixpath.basename(path) in ("Dockerfile", ".dockerignore", "render.yaml", ".env.example"):
        return "deployment"
    if file_type(path).startswith("dependency"):
        return "dependencies"
    if p[0] == ".lovable":
        return "frontend-root (TanStack Start)"
    return "repo-root"


def module_name(path: str) -> Optional[str]:
    if not path.endswith(".py"):
        return None
    m = path[:-3].replace("/", ".")
    return m[: -len(".__init__")] if m.endswith(".__init__") else m


# ------------------------------------------------------------- python layer --

class PyInfo:
    def __init__(self):
        self.doc = ""
        self.imports: Set[str] = set()      # absolute dotted names (maybe attribute-qualified)
        self.public: List[str] = []
        self.routes: List[str] = []
        self.main = False
        self.dyn_prefixes: List[Tuple[str, str]] = []   # (prefix, suffix) of f-string import_module
        self.spec_files: List[str] = []
        self.parse_error = ""


def parse_py(path: str, src: bytes) -> PyInfo:
    info = PyInfo()
    try:
        tree = ast.parse(src, filename=path)
    except (SyntaxError, ValueError) as e:
        info.parse_error = f"{type(e).__name__}: {e}"
        return info
    info.doc = (ast.get_docstring(tree) or "").strip()
    pkg = module_name(path) or ""
    is_pkg = path.endswith("__init__.py")
    base_pkg = pkg if is_pkg else pkg.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                info.imports.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = base_pkg.split(".") if base_pkg else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                mod = ".".join(parts + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod:
                info.imports.add(mod)
            for a in node.names:
                if a.name != "*":
                    info.imports.add(f"{mod}.{a.name}" if mod else a.name)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name == "import_module" and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    info.imports.add(a0.value)
                elif isinstance(a0, ast.JoinedStr) and a0.values and isinstance(a0.values[0], ast.Constant):
                    pre = a0.values[0].value
                    suf = a0.values[-1].value if len(a0.values) > 2 and isinstance(a0.values[-1], ast.Constant) else ""
                    info.dyn_prefixes.append((pre, suf))
            elif name == "spec_from_file_location" and len(node.args) > 1:
                a1 = node.args[1]
                if isinstance(a1, ast.Constant) and isinstance(a1.value, str):
                    info.spec_files.append(a1.value)
                elif isinstance(a1, ast.BinOp):   # REPO / "scripts" / "x.py"
                    lits = [n.value for n in ast.walk(a1) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                    if lits:
                        info.spec_files.append("/".join(lits))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                info.public.append(node.name)
            for d in getattr(node, "decorator_list", []):
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and \
                        d.func.attr in ("get", "post", "put", "delete", "patch", "websocket") and d.args and \
                        isinstance(d.args[0], ast.Constant):
                    info.routes.append(f"{d.func.attr.upper()} {d.args[0].value}")
        elif isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
            info.main = True
    return info


# ------------------------------------------------------------------ JS layer --

JS_IMPORT = re.compile(r"""(?:import\s+(?:[^'"]*?\s+from\s+)?|import\(\s*|require\(\s*|export\s+[^'"]*?\s+from\s+)['"]([^'"]+)['"]""")
JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".css")


def js_imports(path: str, text: str, tracked: Set[str]) -> Tuple[Set[str], Set[str]]:
    internal, external = set(), set()
    for spec in JS_IMPORT.findall(text):
        spec = spec.split("?", 1)[0]          # vite query suffixes, e.g. "../styles.css?url"
        if spec.startswith((".", "@/", "/")):
            if spec.startswith("@/"):
                base = "src/" + spec[2:]
            elif spec.startswith("/"):
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec.lstrip("/")))
            else:
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
            for cand in [base] + [base + e for e in JS_EXT] + [base + "/index" + e for e in JS_EXT]:
                if cand in tracked:
                    internal.add(cand)
                    break
        else:
            external.add("/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0])
    return internal, external


# ------------------------------------------------------------------ builder --

def build(repo: str, sha: str, coverage: Optional[Dict[str, float]] = None,
          review_date: str = "") -> Tuple[List[Dict[str, str]], Dict]:
    full_sha = git(repo, "rev-parse", f"{sha}^{{commit}}").strip()
    tree = list_tree(repo, full_sha)
    paths = [t[0] for t in tree]
    tracked = set(paths)
    blobs = read_blobs(repo, [t[1] for t in tree])
    text = {p: blobs[b].decode("utf-8", errors="replace") for p, b, _ in tree}
    last = last_changes(repo, full_sha, tracked)
    coverage = coverage or {}

    # ---- python modules
    mod2path = {module_name(p): p for p in paths if p.endswith(".py")}
    py = {p: parse_py(p, blobs[b]) for p, b, _ in tree if p.endswith(".py")}

    def resolve(name: str) -> Optional[str]:
        n = name
        while n:
            if n in mod2path:
                return mod2path[n]
            n = n.rpartition(".")[0]
        return None

    edges: Dict[str, Set[str]] = defaultdict(set)       # importer -> imported paths
    dyn_by: Dict[str, Set[str]] = defaultdict(set)       # target -> "rule (source)"
    third: Dict[str, Set[str]] = defaultdict(set)
    for p, info in py.items():
        for name in info.imports:
            t = resolve(name)
            if t and t != p:
                edges[p].add(t)
                # importing a.b.c also executes the packages a/__init__, a/b/__init__
                parts = (module_name(t) or "").split(".")
                for k in range(1, len(parts)):
                    pk = mod2path.get(".".join(parts[:k]))
                    if pk and pk != p:
                        edges[p].add(pk)
            elif not t:
                top = name.split(".")[0]
                if top and top not in STDLIB and top not in {m.split(".")[0] for m in mod2path if m}:
                    third[p].add(top)
        for pre, suf in info.dyn_prefixes:
            for m, tp in mod2path.items():
                if m and m.startswith(pre) and m.endswith(suf) and "." not in m[len(pre):] and m != pre.rstrip("."):
                    edges[p].add(tp)
                    dyn_by[tp].add(f"importlib.import_module(f\"{pre}{{…}}{suf}\") in {p}")
        for sf in info.spec_files:
            for tp in paths:
                if tp == sf or tp.endswith("/" + sf):
                    edges[p].add(tp)
                    dyn_by[tp].add(f"importlib spec_from_file_location in {p}")
    # shim globs in tests (e.g. root.glob("*/*.py") over specialists) — static evidence from test text
    for p in paths:
        if p.startswith("tests/") and p.endswith(".py"):
            t = text[p]
            if "specialists" in t and re.search(r"glob\(\s*['\"]\*/\*\.py['\"]\s*\)", t):
                for tp in paths:
                    if re.match(r"agent_civilization/agents/specialists/[a-z_]+/[a-z_]+_\d{4}\.py$", tp):
                        dyn_by[tp].add(f"glob('*/*.py') + import_module in {p}")
                        edges[p].add(tp)

    # ---- JS graph
    js_ext: Dict[str, Set[str]] = {}
    for p in paths:
        if p.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs")):
            i, e = js_imports(p, text[p], tracked)
            edges[p] |= i
            js_ext[p] = e
    for p in paths:
        if p.endswith(".html"):
            for s in re.findall(r"<script[^>]+src=['\"]([^'\"]+)['\"]", text[p]):
                c = posixpath.normpath(posixpath.join(posixpath.dirname(p), s.lstrip("/")))
                if c in tracked:
                    edges[p].add(c)
                    dyn_by[c].add(f"<script src> in {p}")
    for p in paths:
        if p.startswith("src/routes/") and p.endswith((".tsx", ".ts")):
            dyn_by[p].add("TanStack Start file-based route (src/routes/*, registered by the router plugin)")
            edges["src/router.tsx"].add(p) if "src/router.tsx" in tracked else None

    importers: Dict[str, Set[str]] = defaultdict(set)
    for a, bs in edges.items():
        for b in bs:
            importers[b].add(a)

    base_count = Counter(posixpath.basename(p) for p in paths)
    # ---- entry points (parsed, not assumed)
    entry: Dict[str, Set[str]] = defaultdict(set)
    for cfg in ("Dockerfile", "render.yaml"):
        for m in re.findall(r"uvicorn\s+([\w.]+):app", text.get(cfg, "")):
            tp = mod2path.get(m)
            if tp:
                entry[tp].add(f"deploy:{cfg} (uvicorn {m}:app)")
    workflow_text = "\n".join(text[p] for p in paths if p.startswith(".github/workflows/"))
    for p in paths:
        if (p.startswith("scripts/") or p.startswith("docs/")) and p.endswith((".py", ".sh")) and p in workflow_text:
            entry[p].add("ci:.github/workflows run step")
    for p in paths:
        if p.startswith("scripts/") and p.endswith((".py", ".sh", ".sql")):
            for q in paths:
                if q != p and q.startswith("scripts/") and q.endswith(".sh") and posixpath.basename(p) in text[q]:
                    if q in entry or q in workflow_text:
                        entry[p].add(f"ci-chain:{q}")
    for p in paths:
        b = posixpath.basename(p)
        if p.startswith("tests/") and re.match(r"test_.*\.py$", b):
            entry[p].add("pytest discovery (pytest.ini testpaths)")
        if p == "tests/conftest.py":
            entry[p].add("pytest conftest (auto-loaded)")
        if p.startswith("web/tests/") and b.endswith(".test.mjs"):
            entry[p].add("web/package.json test: node --test tests/*.test.mjs")
        if p.endswith(".py") and py[p].main and p not in entry and not p.startswith("tests/"):
            entry[p].add("cli: if __name__ == '__main__'")
    # documented manual invocation: `python scripts/x.py …`, `bash x.sh`, or a backticked
    # command in docs/evidence (e.g. "`scripts/run_r075_roadmap_audit.py A..B R-1`")
    for p in paths:
        if p.endswith((".py", ".sh")) and (p.startswith("scripts/") or p.startswith("docs/audit/")) and p not in entry:
            inv = re.compile(r"(?:python3?\s+|bash\s+|sh\s+|`)\.?/?" + re.escape(p) + r"(?:[`\s]|$)")
            users = sorted(q for q in paths if q != p and file_type(q) in ("markdown", "log", "shell", "ci-workflow")
                           and inv.search(text[q]))
            if users:
                entry[p].add(f"documented manual invocation in {len(users)} file(s): {', '.join(users[:3])}")
    # extension-less / platform-consumed configuration
    wf = {p: text[p] for p in paths if p.startswith(".github/workflows/")}
    for p in paths:
        if p.startswith(".github/workflows/"):
            ev, inside = [], False
            for ln in text[p].splitlines():
                if re.match(r"^['\"]?on['\"]?:", ln):
                    inside = True
                    continue
                if inside:
                    if ln and not ln[0].isspace():
                        break
                    m = re.match(r"^  (\w+):", ln)
                    if m:
                        ev.append(m.group(1))
            ev = sorted(set(ev))
            entry[p].add("GitHub Actions trigger: " + (", ".join(ev) or "see file"))
    if "Dockerfile" in tracked:
        users = sorted(q for q, t in wf.items() if re.search(r"docker (?:build|buildx)|Dockerfile|docker/build-push-action", t))
        if users:
            entry["Dockerfile"].add("ci: docker job builds the image (" + ", ".join(users) + ")")
        if "render.yaml" in tracked:
            entry["Dockerfile"].add("deploy image definition (CMD uvicorn)")
    if ".dockerignore" in tracked and "Dockerfile" in tracked:
        entry[".dockerignore"].add("docker build context filter (read by docker build of Dockerfile)")
    if "render.yaml" in tracked:
        entry["render.yaml"].add("Render platform blueprint (external consumer: Render deploy)")
    if "pytest.ini" in tracked:
        entry["pytest.ini"].add("pytest configuration (read by every pytest run in CI)")
    for p in paths:
        b = posixpath.basename(p)
        if file_type(p).startswith("dependency") or b in ("vite.config.ts", "tsconfig.json", "eslint.config.js",
                                                           "components.json", "vite.config.js", ".gitignore", ".env.example"):
            # ambiguous basename (root vs web/): a root file must appear NOT preceded by "dir/";
            # a nested file must appear with its full path
            needle = re.compile(re.escape(b) if base_count[b] == 1 else
                                (r"(?<![\w/.-])" + re.escape(p) if "/" not in p else re.escape(p)))
            users = sorted(q for q, t in wf.items() if needle.search(t)) + sorted(
                q for q in ("Dockerfile", "render.yaml") if q in tracked and needle.search(text[q]))
            if base_count[b] > 1 and not users:
                users = ["(basename shared with another tracked file; attribution by name ambiguous — see R-156)"]
            tool = {"package.json": "npm", "package-lock.json": "npm ci", "vite.config.ts": "vite (root build)",
                    "vite.config.js": "vite (web build)", "tsconfig.json": "tsc/vite", "eslint.config.js": "eslint (root lint)",
                    "components.json": "shadcn/ui CLI", ".gitignore": "git", ".env.example": "operators (copy to .env)"}.get(b, "pip / pip-compile")
            entry[p].add(f"consumed by {tool}" + (f" in {', '.join(users)}" if users else ""))
    if "web/index.html" in tracked:
        entry["web/index.html"].add("vite build (web/package.json build; render.yaml buildCommand; Dockerfile web stage)")
    for p in ("src/router.tsx", "src/start.ts", "src/server.ts"):
        if p in tracked:
            entry[p].add("TanStack Start entry (vite.config.ts / root package.json build)")

    def closure(starts: Iterable[str]) -> Set[str]:
        seen, stack = set(), list(starts)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(edges.get(n, ()))
        return seen

    deploy_roots = [p for p, e in entry.items() if any(x.startswith("deploy:") for x in e)]
    web_roots = ["web/index.html"] if "web/index.html" in tracked else []
    root_fe = [p for p in ("src/router.tsx", "src/start.ts", "src/server.ts") if p in tracked]
    cli_roots = [p for p, e in entry.items() if any(x.startswith(("cli:", "ci:", "ci-chain:", "documented manual")) for x in e)]
    test_roots = [p for p, e in entry.items() if any(x.startswith(("pytest", "web/package.json test")) for x in e)]
    R_deploy, R_web, R_rootfe = closure(deploy_roots), closure(web_roots), closure(root_fe)
    R_cli, R_test = closure(cli_roots), closure(test_roots)

    # ---- textual references (exact path or unique basename)
    token_re = re.compile(r"[\w\-\[\].@/]+\.(?:py|md|sh|sql|json|jsonl|log|ts|tsx|jsx|js|mjs|css|html|yml|yaml|lock|in|txt|csv|ini|png)\b")
    refs_by: Dict[str, Set[str]] = defaultdict(set)
    for q in paths:
        if file_type(q) in ("image",):
            continue
        for tok in set(token_re.findall(text[q])):
            tok = tok.lstrip("./")
            hit = tok if tok in tracked else None
            if not hit:
                b = posixpath.basename(tok)
                if base_count.get(b) == 1:
                    hit = next(p for p in paths if posixpath.basename(p) == b) if "/" not in tok or \
                        any(p.endswith("/" + tok) for p in paths) else None
            if hit and hit != q:
                refs_by[hit].add(q)

    # ---- duplicates: identical blobs; normalized fingerprints of generated shims
    by_blob = defaultdict(list)
    for p, b, _ in tree:
        by_blob[b].append(p)
    norm = defaultdict(list)
    for p in paths:
        if p.endswith(".py") and "/specialists/" in p and re.search(r"_\d{4}\.py$", p):
            norm[hashlib.sha256(re.sub(r"\d+", "#", re.sub(r"[A-Z][a-z]+(?:[A-Z][a-z]+)*Agent", "XAgent",
                                text[p])).encode()).hexdigest()].append(p)
    norm_of = {p: k for k, ps in norm.items() for p in ps}

    rows: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []
    for p, blob, size in tree:
        ft, comp = file_type(p), component(p)
        info = py.get(p)
        t = text[p]
        imp_by = sorted(importers.get(p, ()))
        ent = sorted(entry.get(p, ()))
        dyn = sorted(dyn_by.get(p, ()))
        refs = sorted(refs_by.get(p, ()))
        code_refs = [r for r in refs if not r.startswith("docs/") and not r.endswith((".md", ".log"))]
        doc_refs = [r for r in refs if r not in code_refs]
        # direct = the test imports/loads the file (AST/JS graph, importlib, glob loader);
        # path_mentions = the test text contains the path (subprocess call OR fixture data) — weaker
        tests_direct = sorted(x for x in imp_by if x.startswith(("tests/", "web/tests/")))
        test_mentions = sorted(x for x in refs if x.startswith(("tests/", "web/tests/")) and x.endswith((".py", ".mjs"))
                               and x not in tests_direct and x != p)
        cov = coverage.get(p)
        gaps, nxt = [], []

        # purpose
        if info and info.doc:
            purpose = info.doc.splitlines()[0][:220]
        elif ft == "markdown":
            h = next((ln.lstrip("# ").strip() for ln in t.splitlines() if ln.startswith("#")), "")
            purpose = h[:220] or "Markdown document"
        elif ft == "log":
            first = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
            purpose = ("Evidence log: " + first.lstrip("# "))[:220]
        else:
            purpose = DEFAULT_PURPOSE.get(posixpath.basename(p)) or _purpose_by_rule(p, ft, t)
        if info and info.parse_error:
            gaps.append(f"python parse error: {info.parse_error}")

        # public contract
        contract = ""
        if info:
            contract = "; ".join(([f"routes: {', '.join(info.routes[:8])}{' …' if len(info.routes) > 8 else ''} ({len(info.routes)})"]
                                  if info.routes else []) +
                                 ([f"public: {', '.join(info.public[:6])}{' …' if len(info.public) > 6 else ''}"] if info.public else []))
        elif p.startswith("docs/sql/"):
            contract = "DB schema: " + ", ".join(sorted(set(re.findall(r"(?i)create\s+(?:table|policy|function|view)\s+(?:if\s+not\s+exists\s+)?([\w.]+)", t)))[:8])
        elif ft == "ci-workflow":
            contract = "jobs: " + ", ".join(re.findall(r"^  ([\w-]+):\s*$", t, re.M))
        elif p.endswith("package.json"):
            try:
                contract = "scripts: " + ", ".join(sorted(json.loads(t).get("scripts", {})))
            except ValueError:
                pass

        deps = sorted(third.get(p, set()) | js_ext.get(p, set()))
        internal_deps = sorted(edges.get(p, set()))

        # status
        is_shim = bool(info and info.doc and re.search(r"compatibility shim", info.doc.splitlines()[0], re.I))
        reasons = []
        if p.startswith("tests/") or p.startswith("web/tests/"):
            status = "test-only"
        elif ft in ("log", "jsonl") or (p.startswith("docs/audit/") and ft in ("json", "csv", "text", "image")):
            status = "generated"
        elif ft == "dependency-lock":
            status = "generated"
        elif ft == "markdown" or p.startswith("docs/governance/roadmaps/revival-scale/"):
            status = "docs-only"
        elif is_shim:
            status = "legacy-compatible"
        elif p in R_deploy or p in R_web:
            status = "active"
        elif ent and any(e.startswith(("deploy:", "ci:", "ci-chain:")) for e in ent):
            status = "active"
        elif ft in ("docker", "render-config", "ci-workflow", "dependency-manifest", "env-template", "git-config", "ini") \
                or p in ("vite.config.ts", "tsconfig.json", "eslint.config.js", "components.json", "web/vite.config.js",
                         "public/robots.txt", "requirements-audit.in", "requirements-dev.in"):
            status = "active"
            reasons.append("configuration consumed by build/CI/deploy tooling")
        elif p in R_rootfe or p.startswith("src/"):
            status = "shared"
            reasons.append("root TanStack Start app: built and linted by CI (jobs build/quality) but NOT the deployed frontend (render.yaml/Dockerfile build web/)")
        elif p in R_cli:
            status = "shared"
            reasons.append("operational/verification CLI or its import closure; not part of the deployed server import graph"
                           + ("; manual tool — not run by CI" if not any(e.startswith(("ci:", "ci-chain:")) for e in ent) and ent else ""))
        elif p.startswith("docs/audit/") and p.endswith((".py", ".sh")):
            status = "test-only"
            reasons.append("audit verification script kept as evidence (run manually, outputs under docs/audit)")
        elif p.startswith(("docs/sql/", "scripts/")) and (code_refs or p in R_cli):
            status = "active" if any(r in R_cli or r in entry for r in code_refs) else "shared"
        else:
            status = "unresolved"

        oq = next((v for k, v in OPEN_ROLE_QUESTIONS.items() if p.startswith(k) and ft != "markdown"), None)
        if oq and re.search(rf"###\s*{oq[0]}:(?:(?!\n###).)*?$", text.get("docs/governance/OPEN_QUESTIONS.md", ""), re.S | re.M) \
                and "مُغلَق" not in re.search(rf"###\s*{oq[0]}:[^\n]*", text.get("docs/governance/OPEN_QUESTIONS.md", "")).group(0):
            status = "unresolved"
            gaps.append(f"role unresolved: {oq[1]}")
            nxt.append(oq[2])
            unresolved.append({"path": p, "group": comp, "file_type": ft, "reason": oq[1], "next_step": oq[2],
                               "question": oq[0]})
        elif status == "unresolved" or (status in ("shared",) and not (ent or imp_by or dyn or refs)):
            status = "unresolved"
            if ft == "python":
                why = ("not reachable from any parsed entry point (deploy uvicorn app, CLI __main__, CI run step, pytest)"
                       + (f"; imported only by tests: {', '.join(tests_direct[:3])}" if tests_direct else "")
                       + (f"; referenced textually by {len(refs)} file(s)" if refs else "; no static reference found"))
                step = ("trace runtime use: grep env-flag/registry lookups + run server with coverage (R-156 route map) "
                        "to confirm active or legacy; then KEEP/INTEGRATE")
            else:
                why = f"no parsed consumer for {ft} file" + (f"; referenced by {len(refs)} file(s)" if refs else "; no reference found")
                step = "identify consuming tool/config manually (R-156) and record owner"
            gaps.append("role unresolved: " + why)
            nxt.append(step)
            unresolved.append({"path": p, "group": comp, "file_type": ft, "reason": why, "next_step": step})

        # tests / evidence
        cats = set()
        for tp in tests_direct:
            tt = text.get(tp, "")
            cats.add("web-node-test" if tp.startswith("web/") else "pytest")
            if "mark.network" in tt or "network" in posixpath.basename(tp):
                cats.add("network(live)")
            if "R042_LIVE" in tt:
                cats.add("storage(live)")
        if cov is not None:
            cats.add("executed-by-pytest" if cov > 0 else "collected-not-executed")
        if test_mentions and not tests_direct:
            cats.add("path-mentioned-in-tests")
        if p.startswith("src/") or p.startswith("web/src/"):
            cats.add("build(vite)+lint" if p.startswith("src/") else "build(vite)")
        if ft == "ci-workflow" or p.startswith("scripts/check_"):
            cats.add("governance-ci")
        if status == "test-only":
            cats.add("is-test" if posixpath.basename(p).startswith(("test_", "conftest")) or p.endswith(".test.mjs") else "test-support")
        last_evidence = full_sha if (tests_direct or cov or test_mentions or status == "test-only" or "build(vite)" in " ".join(cats)
                                     or ft == "ci-workflow") else ""

        # risk
        risk = "low"
        if ft in ("python", "typescript", "tsx", "jsx", "javascript", "shell", "sql") and status not in ("test-only", "generated", "docs-only"):
            risk = "medium"
            if re.search(r"(auth|secur|safety|storage|supabase|db_|tool|network|exec|sandbox|key|tenant|llm|redis|queue|server)", p, re.I) \
                    or p in ("server.py",) or (info and info.routes):
                risk = "high"
            if not tests_direct and not cov and not test_mentions and ft == "python":
                risk = "high" if not p.startswith("docs/audit/") else "low"
                gaps.append("no direct test and no pytest execution recorded")
        if p.startswith("docs/audit/") and ft in ("python", "shell"):
            gaps.append("historical evidence script — not re-run at this snapshot; its output logs are the evidence")
        if ft in ("docker", "render-config", "ci-workflow", "dependency-lock", "dependency-manifest", "env-template"):
            risk = "high"
        if is_shim:
            risk = "low"      # no implementation here; behaviour (and its risk) lives in _templates/<spec>_base.py
            gaps.append("risk carried by the specialization base class in _templates/")
        if status == "unresolved" and risk == "low":
            risk = "medium"

        # duplicates / adapters
        dup = []
        same = [x for x in by_blob[blob] if x != p]
        if same and size > 0:
            dup.append(f"identical blob with {len(same)} file(s): {', '.join(same[:3])}{' …' if len(same) > 3 else ''}")
        if p in norm_of and len(norm[norm_of[p]]) > 1:
            dup.append(f"generated shim — same normalized body as {len(norm[norm_of[p]]) - 1} other shim(s)")
        if comp.startswith("frontend-root") and p.startswith("src/"):
            dup.append("overlaps functionally with web/src (second frontend; R-158 decides)")
        if p.startswith("web/src/"):
            dup.append("overlaps functionally with root src/ (second frontend; R-158 decides)")
        if p.startswith("agos_kernel/"):
            dup.append("parallel kernel package next to agent_civilization (overlap to assess in R-157)")
        adapter = "this file is a compatibility shim (re-exports generated class)" if is_shim else (
            "adapter: " + info.doc.splitlines()[0][:120] if info and re.search(r"\badapter\b", info.doc[:400], re.I) else "none found")

        # decision (never DELETE)
        if status == "unresolved":
            decision = "INTEGRATE" if ft == "python" else "DOCUMENT"
        elif status == "legacy-compatible":
            decision = "ADAPTER"
        elif "no direct test and no pytest execution recorded" in gaps and not p.startswith("docs/audit/"):
            decision = "UPGRADE"
            nxt.append("add a direct test or record execution evidence")
        elif status in ("docs-only", "generated"):
            decision = "KEEP" if status == "generated" else "DOCUMENT"
        else:
            decision = "KEEP"
        if dup and any("overlap" in d for d in dup):
            nxt.append("assess overlap (R-157/R-158) — no merge/cleanup in R-155")
        if status == "legacy-compatible" and not nxt:
            nxt.append("keep as compatibility shim (D-049); R-157 checks for external importers before any adapter change")
        if not nxt:
            nxt.append("none in R-155 (KEEP); re-check in R-156 route/dependency map" if status not in ("docs-only", "generated")
                       else "none in R-155")

        # confidence
        if status == "unresolved":
            conf = "low"
        elif ent or (status == "active" and (imp_by or dyn)) or status in ("generated", "test-only", "legacy-compatible"):
            conf = "high"
        elif status == "docs-only":
            conf = "high" if p.startswith("docs/") or ft == "markdown" else "medium"
        else:
            conf = "medium"

        where = []
        if ent:
            where.append("ENTRY " + "; ".join(ent))
        if imp_by:
            where.append(f"imported/loaded by {len(imp_by)}: {', '.join(imp_by[:MAXLIST])}{' …' if len(imp_by) > MAXLIST else ''}")
        reach = [n for n, s in (("deploy-server", R_deploy), ("web-build", R_web), ("root-frontend-build", R_rootfe),
                                ("cli/ci", R_cli), ("tests", R_test)) if p in s]
        if reach:
            where.append("reachable from: " + ", ".join(reach))
        dynamic = list(dyn)
        if code_refs:
            dynamic.append(f"code/config refs {len(code_refs)}: {', '.join(code_refs[:3])}{' …' if len(code_refs) > 3 else ''}")
        if doc_refs:
            dynamic.append(f"doc/evidence refs {len(doc_refs)}")
        if reasons:
            gaps.extend(reasons)
        rows.append({
            "path": p, "file_type": ft,
            "last_modified_utc": (last.get(p, ("", ""))[1] + (f" ({last[p][0][:7]})" if p in last else "")),
            "git_blob_sha": blob, "size_bytes": str(size), "owner_component": comp, "purpose": purpose,
            "entrypoint_or_imported_by": " | ".join(where) or "none found (static)",
            "dynamic_registration_or_reference": " | ".join(dynamic) or "none found (static)",
            "public_contract": contract or ("n/a" if status in ("docs-only", "generated") else "none parsed"),
            "dependencies": "; ".join(filter(None, [("third-party: " + ", ".join(deps)) if deps else "",
                                                  (f"internal {len(internal_deps)}: " + ", ".join(internal_deps[:4]) +
                                                   (" …" if len(internal_deps) > 4 else "")) if internal_deps else ""])) or "none",
            "current_tests": "; ".join(filter(None, [
                (f"direct {len(tests_direct)}: " + ", ".join(tests_direct[:4]) + (" …" if len(tests_direct) > 4 else ""))
                if tests_direct else "no direct test import",
                (f"path mentioned by {len(test_mentions)} test(s) (subprocess or fixture — not proof of coverage): "
                 + ", ".join(test_mentions[:3])) if test_mentions else "",
                f"pytest suite executed {cov:.1f}% of lines" if cov is not None else ""])),
            "last_evidence_sha": last_evidence, "test_categories": ", ".join(sorted(cats)) or "none",
            "risk_level": risk, "inventory_status": status, "decision_no_delete": decision,
            "known_duplicate_or_overlap": " | ".join(dup) or "none found", "compatibility_adapter": adapter,
            "gaps": " | ".join(dict.fromkeys(gaps)) or "none found", "next_action": " | ".join(dict.fromkeys(nxt)),
            "confidence": conf, "reviewed_by": REVIEWER, "review_date_utc": review_date,
        })

    summary = {
        "snapshot_sha": full_sha, "total_files": len(rows),
        "by_status": dict(sorted(Counter(r["inventory_status"] for r in rows).items())),
        "by_risk": dict(sorted(Counter(r["risk_level"] for r in rows).items())),
        "by_decision": dict(sorted(Counter(r["decision_no_delete"] for r in rows).items())),
        "by_confidence": dict(sorted(Counter(r["confidence"] for r in rows).items())),
        "by_file_type": dict(sorted(Counter(r["file_type"] for r in rows).items())),
        "by_component": dict(sorted(Counter(r["owner_component"] for r in rows).items())),
        "reviewed_role_determined": sum(r["inventory_status"] != "unresolved" for r in rows),
        "unresolved": unresolved,
        "unresolved_by_group": dict(sorted(Counter(u["group"] for u in unresolved).items())),
        "high_risk_by_component": dict(sorted(Counter(r["owner_component"] for r in rows if r["risk_level"] == "high").items())),
        "reachability": {"deploy-server": len(R_deploy), "web-build": len(R_web), "root-frontend-build": len(R_rootfe),
                         "cli/ci": len(R_cli), "tests": len(R_test)},
        "coverage_files": len(coverage),
    }
    return rows, summary


DEFAULT_PURPOSE = {
    "Dockerfile": "Container image build: web stage (Node) + Python runtime; CMD runs uvicorn server:app",
    ".dockerignore": "Excludes files from the Docker build context",
    "render.yaml": "Render deployment blueprint (build + start command)",
    ".env.example": "Template of environment variables (no secrets)",
    ".gitignore": "Git ignore rules",
    "pytest.ini": "pytest configuration (testpaths, markers, default -m 'not network')",
    "package.json": "npm manifest (scripts + dependencies)",
    "package-lock.json": "npm lockfile (exact dependency tree)",
    "requirements.txt": "Python runtime dependencies (human-readable)",
    "requirements.lock": "Python runtime lock with hashes (pip --require-hashes)",
    "requirements-dev.in": "Python dev/test dependency input", "requirements-dev.lock": "Python dev/test lock with hashes",
    "requirements-audit.in": "pip-audit tool input", "requirements-audit.lock": "pip-audit tool lock with hashes",
    "vite.config.ts": "Vite/TanStack Start config for the root app", "tsconfig.json": "TypeScript config (root app)",
    "eslint.config.js": "ESLint config (root app)", "components.json": "shadcn/ui component config (root app)",
    "robots.txt": "Crawler rules for the root app", "vite.config.js": "Vite config for web/ (deployed frontend)",
}


def _purpose_by_rule(p: str, ft: str, t: str) -> str:
    if ft == "ci-workflow":
        m = re.search(r"^name:\s*(.+)$", t, re.M)
        return f"GitHub Actions workflow: {m.group(1).strip() if m else p}"
    if ft == "sql":
        return "SQL: " + (next((ln.strip("- ").strip() for ln in t.splitlines() if ln.startswith("--")), "") or "schema/grants")[:200]
    if ft == "shell":
        return "Shell script: " + (next((ln.strip("# ").strip() for ln in t.splitlines()[1:] if ln.startswith("#") and len(ln) > 3), "") or p)[:200]
    if ft in ("json", "jsonl", "csv", "txt", "image") and p.startswith("docs/audit/"):
        return f"Evidence artifact ({ft}) of {component(p)}"
    if ft in ("ts", "tsx", "typescript", "jsx", "javascript", "css", "html"):
        c = next((ln.strip("/* ").strip() for ln in t.splitlines() if ln.strip().startswith(("//", "/*"))), "")
        return (f"Frontend module ({component(p)})" + (f": {c}" if c else ""))[:220]
    return f"{ft} file ({component(p)})"


def write_csv(rows: List[Dict[str, str]], out: str) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writeheader()
    for r in rows:
        w.writerow({k: r[k].replace("\n", " ") for k in COLUMNS})
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def self_check(repo: str, sha: str, rows: List[Dict[str, str]]) -> List[str]:
    errs = []
    expected = [x for x in git(repo, "ls-tree", "-r", "-z", "--name-only", "--full-tree", sha, binary=True)
                .decode("utf-8").split("\0") if x]
    got = [r["path"] for r in rows]
    if len(got) != len(expected):
        errs.append(f"row count {len(got)} != tracked files {len(expected)}")
    if set(got) != set(expected):
        errs.append(f"path set differs: missing {sorted(set(expected) - set(got))[:5]} extra {sorted(set(got) - set(expected))[:5]}")
    if len(set(got)) != len(got):
        errs.append("duplicate paths in inventory")
    for r in rows:
        if r["decision_no_delete"] not in DECISIONS:
            errs.append(f"{r['path']}: decision {r['decision_no_delete']!r} not allowed (no DELETE)")
        if r["inventory_status"] not in STATUSES:
            errs.append(f"{r['path']}: status {r['inventory_status']!r} not allowed")
        if r["inventory_status"] == "unresolved" and (not r["gaps"] or not r["next_action"]):
            errs.append(f"{r['path']}: unresolved without reason/next action")
    return errs


def render_summary_md(summary: Dict, csv_name: str, cmd: str) -> str:
    """Arabic summary of the inventory (generated — do not edit by hand)."""
    s = summary
    L = [f"# ملخص جرد الملفات — اللقطة `{s['snapshot_sha'][:7]}` (R-155)", "",
         "> **مُولَّد آلياً** من `scripts/build_file_inventory.py` — لا يُحرَّر يدوياً. أعد الإنتاج بالأمر:", ">",
         f"> `{cmd}`", "",
         f"- **اللقطة المرجعية**: `{s['snapshot_sha']}` — قائمة المسارات = `git ls-files -z` عند هذا الـSHA "
         "(مقروءة من كائنات git لا من شجرة العمل).",
         f"- **ملف الجرد**: [`{csv_name}`]({csv_name}) — صف واحد لكل ملف متعقب.",
         f"- **إجمالي الملفات المتعقبة**: **{s['total_files']}**؛ صفوف الجرد: **{s['total_files']}** (فحص ذاتي: "
         "عدد الصفوف = عدد `git ls-files`، مجموعة المسارات متطابقة، لا تكرار، لا قرار `DELETE`).",
         f"- **ملفات حُسم دورها بدليل** (حالة ≠ `unresolved`): **{s['reviewed_role_determined']}** — "
         "المراجعة **آلية**: رسم استيراد AST، مراجع git/CI/Docker/Render، أنماط التحميل الديناميكي، تغطية تنفيذ pytest "
         "عند اللقطة. **ليست** قراءة بشرية سطراً بسطر لكل ملف، ولا تثبت صحة سلوك الملف.",
         f"- **صفوف غير محسومة**: **{len(s['unresolved'])}** — لذلك **لا يُعلن فهم كامل للمستودع**.",
         f"- ملفات لها بيانات تغطية تنفيذ: {s['coverage_files']}.", "",
         "## التوزيع", "", "| البعد | القيم |", "|---|---|"]
    for k, label in (("by_status", "الحالة"), ("by_risk", "المخاطر"), ("by_decision", "القرار (بلا DELETE)"),
                     ("by_confidence", "الثقة"), ("by_file_type", "نوع الملف")):
        L.append(f"| {label} | " + "، ".join(f"`{a}`={b}" for a, b in s[k].items()) + " |")
    L += ["", "## قابلية الوصول من نقاط الدخول (عدد الملفات)", "",
          "| الجذر | الملفات |", "|---|---|"]
    for k, v in s["reachability"].items():
        L.append(f"| {k} | {v} |")
    L += ["", "## المخاطر العالية حسب المكون", "", "| المكون | عدد `high` |", "|---|---|"]
    for k, v in sorted(s["high_risk_by_component"].items(), key=lambda kv: (-kv[1], kv[0])):
        L.append(f"| {k} | {v} |")
    L += ["", "## المكونات", "", "| المكون | الملفات |", "|---|---|"]
    for k, v in s["by_component"].items():
        L.append(f"| {k} | {v} |")
    L += ["", "## الملفات غير المحسومة (كاملة)", ""]
    if not s["unresolved"]:
        L.append("لا شيء.")
    groups = defaultdict(list)
    for u in s["unresolved"]:
        groups[(u["group"], u["reason"], u["next_step"])].append(u["path"])
    for (g, why, step), ps in sorted(groups.items()):
        L += [f"### المجموعة: {g} ({len(ps)})", "", f"- **سبب عدم الحسم**: {why}", f"- **الخطوة اللازمة**: {step}",
              "- **الملفات**:"] + [f"  - `{p}`" for p in ps] + [""]
    return "\n".join(L).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("sha")
    ap.add_argument("out")
    ap.add_argument("--coverage", help="JSON {path: percent_covered} from the pytest suite at the same SHA")
    ap.add_argument("--review-date", default="")
    ap.add_argument("--summary")
    ap.add_argument("--summary-md")
    ap.add_argument("--command-note", default="", help="reproduction command printed in the Markdown summary")
    a = ap.parse_args(argv)
    try:
        cov = json.load(open(a.coverage, encoding="utf-8")) if a.coverage else None
        rows, summary = build(a.repo, a.sha, cov, a.review_date)
        errs = self_check(a.repo, a.sha, rows)
    except (GitError, OSError, ValueError) as e:
        print(f"::error::inventory: {e}")
        return 2
    write_csv(rows, a.out)
    if a.summary:
        with open(a.summary, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    if a.summary_md:
        rel = posixpath.relpath(a.out, posixpath.dirname(a.summary_md) or ".")
        with open(a.summary_md, "w", encoding="utf-8") as f:
            f.write(render_summary_md(summary, rel, a.command_note or "python3 scripts/build_file_inventory.py …"))
    for e in errs:
        print(f"::error::inventory self-check: {e}")
    print(f"snapshot={summary['snapshot_sha']} rows={summary['total_files']} status={summary['by_status']} "
          f"unresolved={len(summary['unresolved'])} self_check={'FAIL' if errs else 'PASS'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
