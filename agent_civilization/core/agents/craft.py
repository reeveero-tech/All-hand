#!/usr/bin/env python3
"""
Deterministic, self-verifying implementations for the "craft" specializations
(R-102, D-045): builder, architect, designer, modifier, code generation from a
structured spec, and text analysis.

Before R-102 these handlers claimed more than they did (``builder`` answered
"N build action(s) queued" with no queue; ``architect`` returned the same five
layers for any input; ``designer`` derived colours from a SHA-256 of the text;
``modifier`` normalised whitespace and discarded the result; ``code_generator``
only parsed).  Every function here:

* derives its output from the input (different input -> different output);
* verifies what it produced (compile / json / tomllib / ast / WCAG maths);
* states in ``answer`` exactly what was done — and what was not.

Pure stdlib, no network, no code execution, no side effects outside a
temporary directory that is always removed.
"""

from __future__ import annotations

import ast
import builtins
import colorsys
import difflib
import io
import json
import keyword
import os
import re
import tempfile
import tokenize
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

try:  # Python >= 3.11 (the supported floor)
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


# ---------------------------------------------------------------------------
# builder — real scaffold, verified, returned (never written to the server)
# ---------------------------------------------------------------------------

MAX_SCAFFOLD_FILES = 20
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.\-/]*$")  # relative only; ".." segments refused below


def _py_module_body(path: str) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    if name.startswith("test_"):
        target = name[len("test_"):]
        return (f'"""Tests for {target}."""\n\n\n'
                f"def test_{target}_imports():\n"
                f"    import importlib\n"
                f"    assert importlib.util.find_spec({target!r}) is not None\n")
    if name == "__init__":
        return '"""Package marker."""\n'
    return (f'"""{name} module."""\n\n\n'
            f"def main() -> int:\n"
            f"    return 0\n\n\n"
            f'if __name__ == "__main__":\n'
            f"    raise SystemExit(main())\n")


def _content_for(path: str, project: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path).lower()
    if ext == ".py":
        return _py_module_body(path)
    if ext == ".json":
        if base == "package.json":
            return json.dumps({"name": project, "version": "0.1.0", "private": True,
                               "type": "module", "scripts": {"start": "node index.js"}}, indent=2) + "\n"
        return "{}\n"
    if base == "pyproject.toml":
        return (f'[project]\nname = "{project}"\nversion = "0.1.0"\n'
                f'requires-python = ">=3.11"\n')
    if ext == ".toml":
        return "# configuration\n"
    if ext == ".md":
        return f"# {project}\n"
    if ext in (".js", ".mjs"):
        return "export function main() {\n  return 0;\n}\n"
    if base == "requirements.txt":
        return ""
    if base == ".gitignore":
        return "__pycache__/\n*.pyc\nnode_modules/\n.env\n"
    return ""


_STACKS: List[Tuple[str, str, List[str]]] = [
    # (regex on request, stack name, files)
    (r"\b(fastapi|rest api|web api|api)\b|واجهة برمجية", "python-api",
     ["app/__init__.py", "app/main.py", "tests/test_main.py", "requirements.txt", "README.md"]),
    (r"\b(node|javascript|express|npm)\b|جافاسكربت", "node-app",
     ["package.json", "index.js", "README.md", ".gitignore"]),
    (r"\b(library|package)\b|مكتبة|حزمة", "python-package",
     ["pyproject.toml", "src/__init__.py", "tests/test_core.py", "README.md"]),
    (r"\b(cli|command line)\b|سطر الأوامر", "python-cli",
     ["cli.py", "tests/test_cli.py", "README.md"]),
]


def _project_name(text: str) -> str:
    m = re.search(r"\b(?:called|named)\s+([A-Za-z][A-Za-z0-9_\-]{1,40})", text)
    if not m:
        m = re.search(r"(?:باسم|اسمه)\s+([A-Za-z][A-Za-z0-9_\-]{1,40})", text)
    return m.group(1) if m else "project"


_KNOWN_FILES = {"makefile", "dockerfile", "license", "readme", "procfile"}


def _looks_like_path(p: str) -> bool:
    base = p.rstrip("/").rsplit("/", 1)[-1].lower()
    return "/" in p or "." in base or base in _KNOWN_FILES


def scaffold(text: str, data: Any) -> Dict[str, Any]:
    """Plan files from ``data`` (paths or {path: content}) or from the stack named
    in ``text``; write them to a temp dir; verify each; return the manifest."""
    project = _project_name(text or "")
    supplied: Dict[str, Optional[str]] = {}
    source = ""
    # data counts as a file list only if every entry looks like a path — a
    # pipeline passes the same user data to every stage, and {"name": "x"} is
    # not a request to create a file called "name".
    if (isinstance(data, dict) and data and all(isinstance(k, str) and _looks_like_path(k) for k in data)
            and all(isinstance(v, str) for v in data.values())):
        supplied = dict(data)
        source = "paths and contents from data"
    elif isinstance(data, list) and data and all(isinstance(x, str) and _looks_like_path(x) for x in data):
        supplied = {p: None for p in data}
        source = "paths from data"
    stack = "custom"
    if not supplied:
        for pattern, name, files in _STACKS:
            if re.search(pattern, (text or "").lower()):
                stack, supplied, source = name, {p: None for p in files}, f"stack detected: {name}"
                break
    if not supplied:
        stack = "python-app"
        supplied = {p: None for p in ("main.py", "tests/test_main.py", "README.md")}
        source = "no stack or paths in the request — default python-app"
    if len(supplied) > MAX_SCAFFOLD_FILES:
        return {"ok": False, "error": f"too many files ({len(supplied)} > {MAX_SCAFFOLD_FILES})"}
    bad = [p for p in supplied if not _SAFE_PATH.match(p) or ".." in p.split("/")]
    if bad:
        return {"ok": False, "error": f"unsafe path(s) refused: {', '.join(bad[:5])}"}

    files = []
    with tempfile.TemporaryDirectory(prefix="agos-build-") as root:
        for rel, content in supplied.items():
            body = content if content is not None else _content_for(rel, project)
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full) or root, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(body)
            check, ok, detail = _verify_file(full, rel)
            files.append({"path": rel, "bytes": len(body.encode("utf-8")), "check": check,
                          "verified": ok, **({"detail": detail} if detail else {}),
                          "content": body[:2000]})
    verified = sum(1 for f in files if f["verified"])
    checked = sum(1 for f in files if f["check"] != "none")
    return {
        "ok": verified == len(files),
        "answer": (f"scaffolded {len(files)} file(s) ({stack}); {verified}/{len(files)} passed "
                   f"verification ({checked} with a syntax check); returned in tool_output, "
                   "not written to the server"),
        "tool_output": {"stack": stack, "project": project, "source": source,
                        "files": files, "persisted": False},
        **({} if verified == len(files) else
           {"error": "verification failed: " + ", ".join(f["path"] for f in files if not f["verified"])}),
    }


def _verify_file(full: str, rel: str) -> Tuple[str, bool, str]:
    ext = os.path.splitext(rel)[1].lower()
    try:
        if ext == ".py":
            with open(full, encoding="utf-8") as fh:
                compile(fh.read(), rel, "exec")  # syntax check, writes nothing
            return "compile", True, ""
        if ext == ".json":
            with open(full, encoding="utf-8") as fh:
                json.load(fh)
            return "json", True, ""
        if ext == ".toml" and tomllib is not None:
            with open(full, "rb") as fh:
                tomllib.load(fh)
            return "toml", True, ""
    except (SyntaxError, ValueError) as e:
        return {".py": "compile", ".json": "json"}.get(ext, "toml"), False, str(e)[:200]
    return "none", True, ""


# ---------------------------------------------------------------------------
# architect — component graph derived from the request
# ---------------------------------------------------------------------------

# name -> (layer, responsibility, keywords, depends_on)
_COMPONENTS: Dict[str, Tuple[str, str, str, List[str]]] = {
    "ui": ("presentation", "user interface",
           r"\b(ui|frontend|front-end|web ui|web app|dashboard|mobile app)\b|واجهة المستخدم|واجهة|تطبيق ويب|لوحة", ["api"]),
    "api": ("interface", "request/response contract",
            r"\b(api|rest|graphql|endpoint|backend|grpc)\b|واجهة برمجية|خادم", []),
    "auth": ("service", "identity, sessions, permissions",
             r"\b(auth|login|oauth|sso|jwt|users?|accounts?)\b|مصادقة|تسجيل الدخول|مستخدمين|حسابات", ["database"]),
    "tenancy": ("service", "tenant isolation",
                r"\b(multi-?tenant|tenants?|saas)\b|متعددة? المستأجرين", ["auth", "database"]),
    "payments": ("service", "billing and payment provider integration",
                 r"\b(payments?|billing|stripe|checkout|subscriptions?)\b|دفع|فوترة|اشتراك", ["auth", "database"]),
    "search": ("service", "indexing and query",
               r"\b(search|elasticsearch|full-?text)\b|بحث", ["database"]),
    "notifications": ("service", "email/push delivery",
                      r"\b(email|notifications?|push|sms)\b|إشعار|بريد", ["queue"]),
    "realtime": ("service", "push updates to clients",
                 r"\b(websockets?|real-?time|live updates|chat)\b|لحظي|مباشر|محادثة", ["api"]),
    "analytics": ("service", "event collection and reporting",
                  r"\b(analytics|metrics|reporting|tracking)\b|تحليلات|تقارير|قياس", ["queue", "database"]),
    "rate_limiting": ("service", "per-client request throttling (R-116)",
                      r"\b(rate[- ]?limit\w*|throttl\w*)\b|تحديد المعدل", ["api", "cache"]),
    "queue": ("infrastructure", "asynchronous work",
              r"\b(queue|worker|background jobs?|kafka|rabbitmq|celery)\b|طابور|مهام خلفية", []),
    "cache": ("infrastructure", "low-latency reads",
              r"\b(cache|redis|memcached|caching)\b|تخزين مؤقت", []),
    "storage": ("infrastructure", "files and blobs",
                r"\b(files?|uploads?|s3|blobs?|images?|media)\b|ملفات|رفع|صور", []),
    "database": ("data", "persistent state",
                 r"\b(db|database|postgres|mysql|sqlite|sql|mongo)\b|قاعدة بيانات|قواعد البيانات", []),
}


def _topo(nodes: List[str], deps: Dict[str, List[str]]) -> List[str]:
    order: List[str] = []
    seen: Dict[str, int] = {}

    def visit(n: str) -> None:
        if seen.get(n) == 2:
            return
        seen[n] = 1
        for d in deps.get(n, []):
            if d in nodes and seen.get(d) != 1:
                visit(d)
        seen[n] = 2
        order.append(n)

    for n in nodes:
        visit(n)
    return order


def design_architecture(text: str, data: Any) -> Dict[str, Any]:
    items = [text or ""]
    if isinstance(data, list):
        items += [str(x) for x in data]
    elif isinstance(data, str):
        items.append(data)
    blob = " \n".join(items).lower()
    found: Dict[str, str] = {}
    for name, (_, _, pattern, _) in _COMPONENTS.items():
        m = re.search(pattern, blob, flags=re.IGNORECASE)
        if m:
            found[name] = f"requested ('{m.group(0).strip()}')"
    assumed = not found
    if assumed:  # labelled default, like designer's neutral hue — never presented as derived
        found = {"api": "assumed (no components named in the request)",
                 "database": "assumed (no components named in the request)"}
    # close over dependencies: a required dependency is added as implied
    changed = True
    while changed:
        changed = False
        for name in list(found):
            for dep in _COMPONENTS[name][3]:
                if dep not in found:
                    found[dep] = f"implied by {name}"
                    changed = True
    deps = {n: [d for d in _COMPONENTS[n][3] if d in found] for n in found}
    order = _topo(sorted(found), deps)
    unmatched = []
    if isinstance(data, list):
        for x in data:
            if not any(re.search(_COMPONENTS[n][2], str(x).lower(), flags=re.IGNORECASE) for n in _COMPONENTS):
                unmatched.append(str(x))
    components = [{"name": n, "layer": _COMPONENTS[n][0], "responsibility": _COMPONENTS[n][1],
                   "depends_on": deps[n], "why": found[n]} for n in order]
    layers: Dict[str, List[str]] = {}
    for c in components:
        layers.setdefault(c["layer"], []).append(c["name"])
    requested = [n for n in order if found[n].startswith("requested")]
    implied = [n for n in order if found[n].startswith("implied")]
    if assumed:
        answer = ("no components named in the request (supported: ui, api, auth, database, queue, "
                  "cache, payments, search, storage, ...); assumed minimal baseline: "
                  + " -> ".join(order))
    else:
        answer = (f"{len(components)} component(s): {', '.join(requested)} requested"
                  + (f"; {', '.join(implied)} implied" if implied else "")
                  + f"; build order: {' -> '.join(order)}")
    return {"ok": True, "answer": answer,
            "tool_output": {"assumed_baseline": assumed,
                            "components": components, "layers": layers, "build_order": order,
                            "edges": [[n, d] for n in order for d in deps[n]],
                            "unmatched_items": unmatched}}


# ---------------------------------------------------------------------------
# designer — palette from the colour/mood in the request, WCAG-checked
# ---------------------------------------------------------------------------

_NAMED_HUES = {  # name -> (hue degrees, saturation)
    "red": (4, 0.70), "أحمر": (4, 0.70), "orange": (28, 0.80), "برتقالي": (28, 0.80),
    "yellow": (48, 0.85), "أصفر": (48, 0.85), "gold": (45, 0.70), "ذهبي": (45, 0.70),
    "green": (130, 0.50), "أخضر": (130, 0.50), "forest": (140, 0.45), "غابة": (140, 0.45),
    "teal": (175, 0.55), "turquoise": (174, 0.60), "فيروزي": (174, 0.60),
    "blue": (215, 0.65), "أزرق": (215, 0.65), "ocean": (200, 0.60), "بحر": (200, 0.60),
    "navy": (225, 0.55), "كحلي": (225, 0.55), "purple": (275, 0.50), "بنفسجي": (275, 0.50),
    "pink": (330, 0.60), "وردي": (330, 0.60), "brown": (25, 0.40), "بني": (25, 0.40),
    "sunset": (16, 0.75), "غروب": (16, 0.75), "desert": (36, 0.45), "صحراء": (36, 0.45),
    "gray": (220, 0.08), "grey": (220, 0.08), "رمادي": (220, 0.08),
}
_DARK = r"\b(dark|night|dark mode)\b|داكن|ليلي|الوضع الليلي"


def _hex(h: float, s: float, lightness: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, max(0, min(1, lightness)), max(0, min(1, s)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def _hue_from_hex(hex_color: str) -> Tuple[float, float]:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s


def _fit(fg_h: float, fg_s: float, start_l: float, bg: str, target: float, step: float) -> Tuple[str, float]:
    """Move lightness from ``start_l`` by ``step`` until contrast with ``bg`` >= target."""
    lightness = start_l
    color = _hex(fg_h, fg_s, lightness)
    while contrast_ratio(color, bg) < target and 0.0 <= lightness <= 1.0:
        lightness += step
        color = _hex(fg_h, fg_s, lightness)
    return color, contrast_ratio(color, bg)


def design_palette(text: str, data: Any) -> Dict[str, Any]:
    blob = " ".join([text or "", data if isinstance(data, str) else json.dumps(data or "", ensure_ascii=False)])
    low = blob.lower()
    m = re.search(r"#([0-9a-fA-F]{6})\b", blob)
    if m:
        hue, sat = _hue_from_hex("#" + m.group(1).lower())
        sat = max(sat, 0.25)
        source = f"hex #{m.group(1).lower()}"
    else:
        hit = next(((w, v) for w, v in _NAMED_HUES.items()
                    if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)), None)
        if hit:
            (hue, sat), source = hit[1], f"colour word '{hit[0]}'"
        else:
            hue, sat, source = 220.0, 0.30, "no colour in the request — neutral default hue 220"
    dark = bool(re.search(_DARK, low))
    bg = _hex(hue, min(sat, 0.20), 0.09 if dark else 0.97)
    surface = _hex(hue, min(sat, 0.20), 0.14 if dark else 0.92)
    text_c, text_cr = _fit(hue, min(sat, 0.15), 0.92 if dark else 0.12, bg, 7.0, 0.02 if dark else -0.02)
    primary, primary_cr = _fit(hue, sat, 0.60 if dark else 0.40, bg, 4.5, 0.02 if dark else -0.02)
    secondary, secondary_cr = _fit(hue + 30, sat, 0.60 if dark else 0.42, bg, 3.0, 0.02 if dark else -0.02)
    accent, accent_cr = _fit(hue + 180, sat, 0.60 if dark else 0.42, bg, 3.0, 0.02 if dark else -0.02)
    palette = {"background": bg, "surface": surface, "text": text_c,
               "primary": primary, "secondary": secondary, "accent": accent}
    checks = {"text_on_background": {"ratio": text_cr, "target": 7.0, "pass": text_cr >= 7.0},
              "primary_on_background": {"ratio": primary_cr, "target": 4.5, "pass": primary_cr >= 4.5},
              "secondary_on_background": {"ratio": secondary_cr, "target": 3.0, "pass": secondary_cr >= 3.0},
              "accent_on_background": {"ratio": accent_cr, "target": 3.0, "pass": accent_cr >= 3.0}}
    scale = [round(16 * 1.25 ** k) for k in range(-2, 5)]
    all_pass = all(c["pass"] for c in checks.values())
    return {"ok": all_pass,
            "answer": (f"{'dark' if dark else 'light'} palette from {source} (hue {round(hue % 360)}); "
                       f"text/background {text_cr}:1, primary/background {primary_cr}:1 — "
                       + ("all WCAG targets met" if all_pass else "some WCAG targets not met")),
            "tool_output": {"palette": palette, "theme": "dark" if dark else "light", "source": source,
                            "hue": round(hue % 360), "wcag": checks,
                            "typography_scale": {"base_px": 16, "ratio": 1.25, "sizes_px": scale}},
            **({} if all_pass else {"error": "palette failed WCAG contrast targets"})}


# ---------------------------------------------------------------------------
# modifier — instruction-driven, AST-verified edits that return the new code
# ---------------------------------------------------------------------------

_RENAME = [
    re.compile(r"\brename\s+([A-Za-z_]\w*)\s+(?:to|as|->)\s+([A-Za-z_]\w*)", re.I),
    re.compile(r"(?:غيّ?ر|غير|بدّ?ل)\s+اسم\s+([A-Za-z_]\w*)\s+(?:إلى|الى|ل)\s*([A-Za-z_]\w*)"),
    re.compile(r"أعد\s+تسمية\s+([A-Za-z_]\w*)\s+(?:إلى|الى|ب)\s*([A-Za-z_]\w*)"),
]
_UNUSED = re.compile(r"unused imports?|الاستيرادات غير المستخدمة|استيرادات غير مستخدمة", re.I)


def _rename_identifier(code: str, old: str, new: str) -> Tuple[str, int]:
    """Replace NAME tokens equal to ``old`` (never inside strings/comments)."""
    tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    lines = code.splitlines(keepends=True)
    hits = [t for t in tokens if t.type == tokenize.NAME and t.string == old]
    for t in reversed(hits):  # right-to-left keeps earlier columns valid
        row, col = t.start
        ln = lines[row - 1]
        lines[row - 1] = ln[:col] + new + ln[col + len(old):]
    return "".join(lines), len(hits)


def _remove_unused_imports(code: str) -> Tuple[str, List[str]]:
    tree = ast.parse(code)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    exported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets):
            exported |= {e.value for e in getattr(n.value, "elts", []) if isinstance(e, ast.Constant)}
    lines = code.splitlines(keepends=True)
    removed: List[str] = []
    drop = set()
    for node in tree.body:  # module-level, single-line import statements only
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.lineno != node.end_lineno:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        names = [(a.asname or a.name).split(".")[0] for a in node.names]
        if all(nm not in used and nm not in exported and nm != "*" for nm in names):
            drop.add(node.lineno)
            removed.append(lines[node.lineno - 1].strip())
    new = "".join(ln for i, ln in enumerate(lines, 1) if i not in drop)
    return new, removed


_COMPOUND_SPLIT = re.compile(r":[ \t]+(?=(?:if|for|while|with|def|class|try)\b)")
_REFACTOR = re.compile(r"refactor|cleaner|clean ?up|simplif|tidy|حسّن|بسّط|نظّف الكود|أعد كتابة", re.I)


def normalise_one_line_compounds(code: str) -> Tuple[str, bool]:
    """R-116: ``if a: if b: if c: return 1`` (one line, not valid Python) ->
    properly nested blocks. Only splits before compound-statement keywords; the
    result is returned only if ``ast.parse`` accepts it."""
    out_lines: List[str] = []
    changed = False
    for line in code.splitlines():
        indent = line[: len(line) - len(line.lstrip())]
        parts = _COMPOUND_SPLIT.split(line.strip())
        if len(parts) == 1:
            out_lines.append(line)
            continue
        changed = True
        for depth, part in enumerate(parts):
            suffix = ":" if depth < len(parts) - 1 else ""
            out_lines.append(indent + "    " * depth + part.strip() + suffix)
    new = "\n".join(out_lines) + ("\n" if code.endswith("\n") else "")
    if not changed:
        return code, False
    try:
        ast.parse(new)
    except SyntaxError:
        return code, False
    return new, True


class _MergeNestedIfs(ast.NodeTransformer):
    """``if a:\n    if b: X`` (no else on either) -> ``if a and b: X``."""

    def __init__(self) -> None:
        self.merged = 0

    def visit_If(self, node: ast.If) -> ast.AST:
        self.generic_visit(node)
        while (not node.orelse and len(node.body) == 1 and isinstance(node.body[0], ast.If)
               and not node.body[0].orelse):
            inner = node.body[0]
            left = node.test.values if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.And) else [node.test]
            right = inner.test.values if isinstance(inner.test, ast.BoolOp) and isinstance(inner.test.op, ast.And) else [inner.test]
            node.test = ast.BoolOp(op=ast.And(), values=[*left, *right])
            node.body = inner.body
            self.merged += 1
        return node


def refactor_code(code: str) -> Tuple[str, List[str]]:
    """Behaviour-preserving AST refactors; returns (new_code, applied)."""
    tree = ast.parse(code)
    t = _MergeNestedIfs()
    tree = t.visit(tree)
    ast.fix_missing_locations(tree)
    applied: List[str] = []
    if t.merged:
        applied.append(f"merged {t.merged} nested if(s) into 'and' conditions")
        return ast.unparse(tree) + "\n", applied
    return code, applied


def modify_code(code: str, instruction: str) -> Dict[str, Any]:
    applied: List[str] = []
    try:
        ast.parse(code)
    except SyntaxError as e:
        fixed, ok = normalise_one_line_compounds(code)
        if not ok:
            return {"ok": False, "error": f"input is not valid Python (line {e.lineno}: {e.msg})"}
        applied.append("split one-line compound statements into nested blocks (input was not valid Python)")
        code = fixed
    new = code
    if _REFACTOR.search(instruction or ""):
        new, refs = refactor_code(new)
        applied.extend(refs)
        if not refs:
            applied.append("refactor requested: no supported pattern found (supported: merge nested ifs)")
    for rx in _RENAME:
        m = rx.search(instruction or "")
        if m:
            old, repl = m.group(1), m.group(2)
            if keyword.iskeyword(repl) or not repl.isidentifier():
                return {"ok": False, "error": f"'{repl}' is not a valid identifier"}
            new, count = _rename_identifier(new, old, repl)
            applied.append(f"rename {old} -> {repl} ({count} occurrence(s))" if count
                           else f"rename {old} -> {repl}: '{old}' not found")
            break
    if _UNUSED.search(instruction or ""):
        new, removed = _remove_unused_imports(new)
        applied.append(f"removed {len(removed)} unused import(s)")
    lines = [ln.rstrip() for ln in new.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    normalised = "\n".join(lines) + "\n"
    if normalised != new:
        applied.append("trailing whitespace normalised")
    new = normalised
    try:
        ast.parse(new)
    except SyntaxError as e:  # never return broken code
        return {"ok": False, "error": f"modification produced invalid Python (line {e.lineno}); not applied"}
    diff = "".join(difflib.unified_diff(code.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile="before.py", tofile="after.py"))
    recognised = any(a.startswith(("rename", "removed", "merged")) for a in applied)
    if not recognised:
        answer = ("no supported instruction found (supported: 'rename X to Y', 'remove unused imports', "
                  "'refactor' = merge nested ifs); "
                  + ("trailing whitespace normalised only" if new != code else "no changes"))
    else:
        answer = "; ".join(applied)
    return {"ok": True, "answer": answer,
            "tool_output": {"changed": new != code or bool(applied), "applied": applied, "new_code": new[:8000],
                            "diff": diff[:8000], "verified": "ast.parse"}}


# ---------------------------------------------------------------------------
# code generation from a structured spec (no LLM needed)
# ---------------------------------------------------------------------------

def generate_from_spec(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``{"function": name, "params": [..], "returns": expr?, "doc": str?}`` or
    ``{"class": name, "fields": [..]}`` -> verified Python source; None if not a spec."""
    if isinstance(spec.get("function"), str):
        name = spec["function"]
        params = [str(p) for p in spec.get("params") or []]
        bad = [x for x in [name, *params] if not x.isidentifier() or keyword.iskeyword(x)]
        if bad:
            return {"ok": False, "error": f"invalid identifier(s): {', '.join(bad)}"}
        doc = str(spec.get("doc") or f"{name}({', '.join(params)}).").replace('"""', "'''")
        ret = spec.get("returns")
        if isinstance(ret, str) and ret.strip():
            try:
                expr = ast.parse(ret, mode="eval")
            except SyntaxError as e:
                return {"ok": False, "error": f"'returns' is not a Python expression: {e.msg}"}
            free = {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)} - set(params) - set(dir(builtins))
            if free:
                return {"ok": False, "error": f"'returns' uses undefined name(s): {', '.join(sorted(free))}"}
            body = f"    return {ret.strip()}\n"
            kind = "implemented"
        else:
            body = f"    raise NotImplementedError({name!r})\n"
            kind = "stub"
        src = f"def {name}({', '.join(params)}):\n    \"\"\"{doc}\"\"\"\n{body}"
    elif isinstance(spec.get("class"), str):
        name = spec["class"]
        fields = [str(f) for f in spec.get("fields") or []]
        bad = [x for x in [name, *fields] if not x.isidentifier() or keyword.iskeyword(x)]
        if bad:
            return {"ok": False, "error": f"invalid identifier(s): {', '.join(bad)}"}
        lines = ["from dataclasses import dataclass", "from typing import Any", "", "", "@dataclass",
                 f"class {name}:"]
        lines += [f"    {f}: Any = None" for f in fields] or ["    pass"]
        src = "\n".join(lines) + "\n"
        kind = "dataclass"
    else:
        return None
    compile(src, "<generated>", "exec")  # verification: the source compiles
    tree = ast.parse(src)
    return {"ok": True,
            "answer": f"generated {kind} {name} from the structured spec ({src.count(chr(10))} lines, compiles)",
            "tool_output": {"generated": True, "method": "structured-spec", "code": src,
                            "functions": [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)],
                            "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
                            "verified": "compile"}}


# ---------------------------------------------------------------------------
# text analysis (analyst, non-numeric input)
# ---------------------------------------------------------------------------

_STOP = set("""
the a an and or of to in on for with is are was were be this that it as at by from
please can you me my we our your i
في من إلى الى على عن مع هذا هذه ذلك التي الذي هو هي أن ان كان كانت ما لا لم قد ثم أو او و وما وفي ومن وهو وهي لماذا كيف هل
""".split())


def analyse_text(text: str) -> Dict[str, Any]:
    words = re.findall(r"[\w\u0600-\u06FF]+", text)
    sentences = [s for s in re.split(r"[.!?؟\n]+", text) if s.strip()]
    arabic = sum(1 for ch in text if "\u0600" <= ch <= "\u06FF")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    lang = "ar" if arabic > latin else "en" if latin > arabic else "mixed/unknown"
    terms = Counter(w.lower() for w in words if w.lower() not in _STOP and len(w) > 2 and not w.isdigit())
    top = [t for t, _ in terms.most_common(5)]
    questions = text.count("?") + text.count("؟")
    return {"ok": True,
            "answer": (f"text: {len(words)} words, {len(sentences)} sentence(s), language={lang}"
                       + (f", top terms: {', '.join(top)}" if top else "")
                       + " (no numeric samples to analyse)"),
            "tool_output": {"type": "text", "words": len(words), "chars": len(text),
                            "sentences": len(sentences), "questions": questions, "language": lang,
                            "top_terms": terms.most_common(5),
                            "unique_terms": len(terms)}}
