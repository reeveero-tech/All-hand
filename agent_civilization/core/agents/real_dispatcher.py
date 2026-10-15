#!/usr/bin/env python3
"""
Real-execution dispatcher for every specialization.

For each specialization we implement genuine, non-simulated behaviour using
only the Python standard library plus commonly available packages. This
module is called by BaseAgent BEFORE any subclass `_process_task` runs, so
every one of the ~1000 specialist classes automatically gets real
capabilities instead of hard-coded mock returns.

Design goals:
  * No hallucinated data. Every dict this file returns comes from a real
    computation, syscall, parse, or network probe performed here and now.
  * Pure-stdlib fallbacks — no hard dependency on aiohttp/requests. If a
    richer library is present we use it; otherwise urllib does the job.
  * Deterministic error surface. Exceptions become {"ok": False, "error": ...}
    instead of crashing the agent loop.
  * Safe by default (R-100, D-044). Code execution, outbound URLs and SQL go
    through ``agent_civilization.core.safety``: code runs only when the policy
    allows it and then in an isolated interpreter; URLs that resolve to
    loopback/private/link-local addresses are refused (also on redirects);
    SQLite cannot ATTACH files and has a time budget.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import os
import re
import socket
import sqlite3
import ssl
import statistics
import time
import tokenize
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_civilization.core import safety
from agent_civilization.core.agents import craft


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_CONTEXT_MARK = "\n\n=== Previous stages summary ==="


def _extract_text(work: Dict[str, Any]) -> str:
    """The stage instruction, WITHOUT the orchestrator's previous-stages summary.

    R-114: the orchestrator appends earlier stages' output to the prompt; before,
    handlers parsed it as if it were the user's input (numbers from an earlier
    stage's statistics entered the analyst's sample, code-looking summary lines
    reached the parser). The summary is still available via ``_context_text``.
    """
    for key in ("prompt", "text", "query", "goal", "message", "input"):
        v = work.get(key)
        if isinstance(v, str) and v.strip():
            return v.split(_CONTEXT_MARK, 1)[0]
    data = work.get("data")
    if isinstance(data, str):
        return data
    return ""


def _goal(work: Dict[str, Any]) -> str:
    """The user's original request (R-114) — falls back to the stage text."""
    g = work.get("goal")
    return g if isinstance(g, str) and g.strip() else _extract_text(work)


def _context_text(work: Dict[str, Any]) -> str:
    ctx = work.get("context")
    if isinstance(ctx, list) and ctx:
        return "\n".join(str(c) for c in ctx)
    for key in ("prompt", "text"):
        v = work.get(key)
        if isinstance(v, str) and _CONTEXT_MARK in v:
            return v.split(_CONTEXT_MARK, 1)[1]
    return ""


_INLINE_CODE = re.compile(r":\s*(def |class |print\(|import |from \w+ import |if |for |while |return |[\w.]+\s*=)")


def _code_in(text: str) -> str:
    m = re.search(r"```(?:python|py)?\n(.+?)```", text, re.S)
    if m:
        return m.group(1)
    if re.search(r"^(?:\s*)(?:def |class |import |from |print\(|[\w.]+\s*=)", text, re.M):
        return text
    m2 = _INLINE_CODE.search(text)
    if m2:
        return text[m2.start() + 1:].strip()
    return ""


def _extract_code(work: Dict[str, Any]) -> str:
    data = work.get("data")
    if isinstance(data, str) and data.strip():
        return data
    if isinstance(data, dict):
        for k in ("code", "source", "new_code", "modified_code"):
            v = data.get(k)
            if isinstance(v, str):
                return v
    art = work.get("artifact")
    if isinstance(art, dict):
        for k in ("code", "new_code"):
            v = art.get(k)
            if isinstance(v, str) and v.strip():
                return v
    # R-114: the stage instruction first, then the user's goal (inline code in
    # the original request, e.g. "write and run tests for: def add(a, b): ...").
    text = _extract_text(work)
    found = _code_in(text)
    if found:
        return found
    goal = work.get("goal")
    if isinstance(goal, str) and goal.strip() and goal != text:
        return _code_in(goal)
    return ""


def _extract_numbers(work: Dict[str, Any]):
    data = work.get("data")
    if isinstance(data, list):
        out = []
        for x in data:
            try:
                out.append(float(x))
            except Exception:
                pass
        return out
    text = _extract_text(work) + " " + json.dumps(data) if data else _extract_text(work)
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _extract_url(work: Dict[str, Any]) -> Optional[str]:
    for source in (_extract_text(work), json.dumps(work.get("data") or "")):
        m = re.search(r"https?://[^\s\"'<>]+", source)
        if m:
            return m.group(0)
    return None


async def _to_thread(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


# ---------------------------------------------------------------------------
# R-116: shared real-world helpers (keyless, honest, bounded)
# ---------------------------------------------------------------------------

_DDG_HTML = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(fragment: str) -> str:
    import html as _html
    return _html.unescape(_TAG_RE.sub("", fragment)).strip()


def _web_search(query: str, limit: int = 5, timeout: float = 10.0):
    """Live web results (title, url, snippet) from DuckDuckGo's HTML endpoint."""
    body = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(_DDG_HTML, data=body, method="POST", headers={
        "User-Agent": "Mozilla/5.0 (AGOS-Research/1.0)",
        "Content-Type": "application/x-www-form-urlencoded"})
    with safety.safe_urlopen(req, timeout=timeout) as resp:
        page = resp.read(400_000).decode("utf-8", errors="replace")
    links = _RESULT_RE.findall(page)
    snippets = _SNIPPET_RE.findall(page)
    out = []
    for i, (href, title) in enumerate(links[:limit]):
        url = href
        if "uddg=" in href:
            url = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg", [href])[0])
        if url.startswith("//"):
            url = "https:" + url
        out.append({"title": _clean_html(title), "url": url,
                    "snippet": _clean_html(snippets[i]) if i < len(snippets) else ""})
    return out


async def _llm_text(system: str, prompt: str, max_tokens: int = 600) -> Dict[str, Any]:
    from agent_civilization.core.llm import get_llm  # local: keeps import order of server.py
    llm = get_llm()
    if not llm.available:
        return {"ok": False, "error": "no LLM configured (set OPENROUTER_API_KEY, or allow the keyless provider: AGOS_LLM_KEYLESS=1)"}
    out = await llm.complete(system, prompt, max_tokens=max_tokens)
    if not out.get("ok") or not (out.get("content") or "").strip():
        return {"ok": False, "error": f"llm: {out.get('error') or 'empty answer'}", "model": out.get("model")}
    return {"ok": True, "content": out["content"].strip(), "model": out.get("model"),
            "provider": out.get("provider") or getattr(llm, "provider", "openrouter")}


_LANGS = {"french": "fr", "français": "fr", "الفرنسية": "fr", "english": "en", "الإنجليزية": "en",
          "arabic": "ar", "العربية": "ar", "spanish": "es", "الإسبانية": "es", "german": "de",
          "الألمانية": "de", "italian": "it", "turkish": "tr", "chinese": "zh", "japanese": "ja"}


def _parse_translation(text: str):
    m = re.search(r"""['"“«](.+?)['"”»]""", text)
    phrase = m.group(1) if m else re.sub(r"(?i)^\s*translate\s+|\s+(to|into|إلى)\s+\w+\s*$", "", text).strip()
    target = None
    for name, code in _LANGS.items():
        if re.search(rf"(?i)\b(to|into|إلى|الى)\s+{re.escape(name)}", text) or re.search(rf"(?i)\b{re.escape(name)}\b", text):
            target = code
            break
    source = "ar" if re.search(r"[\u0600-\u06FF]", phrase) else "en"
    return phrase, source, target


def _mymemory(phrase: str, source: str, target: str, timeout: float = 10.0) -> str:
    url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(
        {"q": phrase, "langpair": f"{source}|{target}"})
    req = urllib.request.Request(url, headers={"User-Agent": "AGOS-Civilization/1.0"})
    with safety.safe_urlopen(req, timeout=timeout) as resp:
        j = json.loads(resp.read().decode("utf-8", errors="replace"))
    if int(j.get("responseStatus") or 0) != 200:
        raise RuntimeError(f"translation service status {j.get('responseStatus')}: {j.get('responseDetails')}")
    return (j.get("responseData") or {}).get("translatedText") or ""


# ---------------------------------------------------------------------------
# real implementations, one per specialization
# ---------------------------------------------------------------------------

def _python_from_llm(content: str):
    """Pick the Python out of an LLM reply: every fenced block is tried (any
    language tag), then the whole reply. Returns (source, error|None)."""
    blocks = re.findall(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", content or "", re.S)
    candidates = [b for tag, b in blocks if tag.lower() in ("", "python", "py", "python3")] + \
                 [b for tag, b in blocks if tag.lower() not in ("", "python", "py", "python3")]
    candidates.append((content or "").strip().strip("`"))
    last = "empty reply"
    for c in candidates:
        try:
            tree = ast.parse(c)
        except SyntaxError as e:
            last = f"line {e.lineno}: {e.msg}"
            continue
        # prose can parse as Python ("this is prose, not code" is a tuple
        # expression): require at least one real statement, not only bare expressions
        if any(not isinstance(n, ast.Expr) for n in tree.body):
            return c, None
        last = "no Python statements (only bare expressions / prose)"
    return "", last


async def _code_generator(work: Dict[str, Any]) -> Dict[str, Any]:
    """Generate verified Python (R-102, D-045), or say plainly why not.

    1. ``data`` is a structured spec ({"function": ..} / {"class": ..}) ->
       deterministic generation, compile-verified.
    2. The request is a description and an LLM is configured -> the LLM writes
       the code; it is returned only if ``ast.parse`` accepts it.
    3. Code was supplied -> it is validated (functions/classes), and the answer
       says it was validated, not generated.
    """
    data = work.get("data")
    if isinstance(data, dict):
        spec_out = craft.generate_from_spec(data)
        if spec_out is not None:
            return spec_out
    code = _extract_code(work)
    if code.strip():
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return {"ok": False, "error": f"SyntaxError line {e.lineno}: {e.msg}"}
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        return {"ok": True,
                "answer": f"validated supplied code (not generated): {len(funcs)} funcs, {len(classes)} classes",
                "tool_output": {"generated": False, "parsed": True, "functions": funcs,
                                "classes": classes, "lines": code.count("\n") + 1}}
    text = _extract_text(work)
    if not text.strip():
        return {"ok": False, "error": "no code, spec or description provided"}
    from agent_civilization.core.llm import get_llm  # local: keeps import order of server.py
    llm = get_llm()
    if not llm.available:
        return {"ok": False,
                "error": ("generating code from a description needs an LLM (set OPENROUTER_API_KEY); "
                          "without one, send a structured spec in data, e.g. "
                          '{"function": "mean", "params": ["xs"], "returns": "sum(xs) / len(xs)"}')}
    out = await llm.complete(
        "You write Python. Reply with exactly one ```python fenced block and nothing else.",
        text, max_tokens=800)
    if not out.get("ok"):
        return {"ok": False, "error": f"llm: {out.get('error', 'failed')}"}
    src, err = _python_from_llm(out.get("content", ""))
    if err:
        # R-116: one retry with the parse error fed back, then fail honestly.
        out = await llm.complete(
            "You write Python. Reply with exactly one ```python fenced block and nothing else.",
            f"{text}\n\nYour previous answer was not valid Python ({err}). Return only valid Python code.",
            max_tokens=900)
        if not out.get("ok"):
            return {"ok": False, "error": f"llm: {out.get('error', 'failed')}"}
        src, err = _python_from_llm(out.get("content", ""))
    if err:
        return {"ok": False, "error": f"LLM output is not valid Python ({err}); discarded"}
    tree = ast.parse(src)
    return {"ok": True,
            "answer": f"generated {src.count(chr(10)) + 1} line(s) with the LLM; ast.parse OK (not executed)",
            "tool_output": {"generated": True, "method": "llm", "code": src[:8000],
                            "functions": [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)],
                            "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
                            "verified": "ast.parse"}}


async def _surgeon(work: Dict[str, Any]) -> Dict[str, Any]:
    code = _extract_code(work)
    if not code.strip():
        return {"ok": False, "error": "no code provided"}
    try:
        issues, funcs = _static_findings(code)
    except SyntaxError as e:
        return {"ok": True, "answer": f"syntax break at line {e.lineno}: {e.msg}",
                "tool_output": {"fatal": True, "issues": [f"syntax error line {e.lineno}: {e.msg}"]}}
    return {"ok": True,
            "answer": f"{len(issues)} issue(s)" + (": " + "; ".join(issues[:5]) if issues else " — no defects found by AST diagnostics"),
            "tool_output": {"issues": issues, "functions": funcs, "code": code}}


_ARG_GUESS = {"email": '"alice@example.com"', "mail": '"alice@example.com"', "name": '"alice"',
              "username": '"alice"', "user": '"alice"', "password": '"S3cure!passw0rd"', "text": '"hello"',
              "s": '"hello"', "string": '"hello"', "word": '"hello"', "age": "30", "n": "5", "k": "2",
              "x": "3", "y": "4", "a": "2", "b": "3", "items": "[1, 2, 3]", "xs": "[1, 2, 3]",
              "nums": "[1, 2, 3]", "numbers": "[1, 2, 3]", "values": "[1, 2, 3]", "data": "[1, 2, 3]"}


def _guess_args(fn: ast.FunctionDef, skip_self: bool = False) -> str:
    args = fn.args.args[1:] if skip_self else fn.args.args
    n_defaults = len(fn.args.defaults)
    required = args[: len(args) - n_defaults] if n_defaults else args
    return ", ".join(_ARG_GUESS.get(a.arg.lower(), "1") for a in required)


def _drop_main_guard(code: str) -> str:
    """R-122: remove top-level ``if __name__ == "__main__":`` blocks from the copy
    that is executed with appended tests. In the sandbox the code runs as
    __main__, so a guard such as ``raise SystemExit(main())`` ended the process
    BEFORE the appended tests — the stage reported "code ran" and tested nothing
    (found by the builder template: scaffolded main.py)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    drop = set()
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == "__main__"):
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    if not drop:
        return code
    return "\n".join(ln for i, ln in enumerate(code.splitlines(), 1) if i not in drop) + "\n"


def _generate_basic_tests(code: str) -> str:
    """R-111/R-116: smoke tests for the TOP-LEVEL functions and classes in code.

    R-116 fixes: methods are no longer called as if they were module functions
    (ast.walk visited class bodies -> NameError), and arguments are chosen from
    the parameter names (n=5, email="alice@example.com", ...) instead of always
    0, which made valid code like nth_prime(0) "fail". A function passes when
    it runs without raising; a class passes when it can be instantiated, or when
    it rejects the sample input with ValueError/TypeError (its own validation).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    tests = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            tests.append(
                f"def test_{node.name}_smoke():\n"
                f"    assert callable({node.name})\n"
                f"    result = {node.name}({_guess_args(node)})\n"
                f"    print('{node.name}(...) ->', repr(result)[:200])\n")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            init = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            args = _guess_args(init, skip_self=True) if init else ""
            tests.append(
                f"def test_{node.name}_smoke():\n"
                f"    assert isinstance({node.name}, type)\n"
                f"    try:\n"
                f"        obj = {node.name}({args})\n"
                f"        print('{node.name}(...) ->', type(obj).__name__)\n"
                f"    except (ValueError, TypeError) as e:\n"
                f"        print('{node.name} validation rejected sample input:', e)\n")
    if not tests:
        return ""
    names = [t.split("(")[0].replace("def ", "") for t in tests]
    runner = "\n_failed = 0\nfor _t in [" + ", ".join(names) + "]:\n" \
             "    try:\n        _t()\n        print('PASS', _t.__name__)\n" \
             "    except Exception as _e:\n        _failed += 1\n        print('FAIL', _t.__name__, type(_e).__name__, _e)\n" \
             "if _failed:\n    raise SystemExit(1)\n"
    return "\n".join(tests) + runner


def _wrap_fragment(code: str) -> str:
    """A fragment with a top-level ``return`` (e.g. a refactored snippet) is not
    a runnable module; wrap it as ``def fragment(<free names>)`` so it can be
    tested for real (R-116)."""
    try:
        compile(code, "<snippet>", "exec")
        return code
    except SyntaxError as e:
        if "outside function" not in str(e.msg):
            return code
    import builtins as _b
    tree = ast.parse(code)
    assigned = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    free = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                  - assigned - set(dir(_b)))
    body = "\n".join("    " + ln for ln in code.splitlines())
    return f"def fragment({', '.join(free)}):\n{body}\n"


async def _test_runner(work: Dict[str, Any]) -> Dict[str, Any]:
    """Run submitted Python in the sandbox — only if the policy allows it.

    R-111: when the code defines functions but carries no tests, generate
    assert-based tests for them and run code + tests together, so the stage
    actually TESTS something instead of just executing the code.
    """
    code = _extract_code(work)
    if not code.strip():
        return {"ok": False, "error": "no code to run"}
    code = _wrap_fragment(code)
    allowed, reason = safety.code_execution_policy()
    if not allowed:
        return {"ok": False, "error": reason, "tool_output": {"executed": False}}

    # R-111: generate tests when the code has none.
    # R-116: count test functions (not lines), and when the code DEFINES
    # test_* functions without calling them, add a runner — before, such code
    # "passed" because merely defining the tests never runs them.
    tests_generated = 0
    if "def test_" not in code and "assert " not in code:
        gen = _generate_basic_tests(code)
        if gen:
            code = _drop_main_guard(code) + "\n\n# --- generated tests (R-111/R-116) ---\n" + gen
            tests_generated = gen.count("\ndef test_") + (1 if gen.startswith("def test_") else 0)
    elif "def test_" in code and "_t()" not in code:
        try:
            names = [n.name for n in ast.parse(code).body
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        except SyntaxError:
            names = []
        if names:
            code = _drop_main_guard(code)
            code += ("\n\n# --- test runner (R-116) ---\n_failed = 0\nfor _t in [" + ", ".join(names) + "]:\n"
                     "    try:\n        _t()\n        print('PASS', _t.__name__)\n"
                     "    except Exception as _e:\n        _failed += 1\n        print('FAIL', _t.__name__, type(_e).__name__, _e)\n"
                     "if _failed:\n    raise SystemExit(1)\n")
    r = await _to_thread(safety.run_sandboxed_python, code)
    if r["timed_out"]:
        return {"ok": False, "error": f"timeout after {safety.SANDBOX_WALL_TIMEOUT_S}s",
                "tool_output": r}
    rc = r["returncode"]
    passed = len(re.findall(r"^PASS ", r["stdout"] or "", re.M))
    failed = len(re.findall(r"^FAIL ", r["stdout"] or "", re.M))
    note = f"exit={rc}" if rc >= 0 else f"killed by signal {-rc} (sandbox limit)"
    if passed or failed:
        note += f"; {passed} test(s) passed, {failed} failed"
    elif rc == 0:
        note += "; code ran (no test functions found to run)"
    err_tail = (r["stderr"] or "").strip().splitlines()[-1:] if rc != 0 else []
    out = {"ok": rc == 0, "answer": note,
           "tool_output": {"stdout": r["stdout"], "stderr": r["stderr"],
                           "elapsed_s": r["elapsed_s"], "sandbox": True,
                           "tests_passed": passed, "tests_failed": failed,
                           "r111_tests_generated": tests_generated}}
    if rc != 0:
        out["error"] = note + (f" — {err_tail[0][:200]}" if err_tail else "")
    return out


async def _validator(work: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the payload's integrity. R-110: prose fallback. R-116: when an
    earlier stage produced a numeric analysis (artifact), its internal
    consistency is checked (min <= mean <= max, n matches, sum = mean * n)."""
    text = _extract_text(work)
    data = work.get("data")
    art = work.get("artifact") if isinstance(work.get("artifact"), dict) else None
    if data is None and art and isinstance(art.get("analysis"), dict):
        a = art["analysis"]
        checks = {}
        try:
            n, mean, lo, hi = int(a["n"]), float(a["mean"]), float(a["min"]), float(a["max"])
            samples = [float(x) for x in a.get("samples") or []]
            checks["n_positive"] = n > 0
            checks["min<=mean<=max"] = lo - 1e-9 <= mean <= hi + 1e-9
            if "sum" in a:
                checks["sum==mean*n"] = abs(float(a["sum"]) - mean * n) <= 1e-6 * max(1.0, abs(float(a["sum"])))
            if samples and len(samples) == n:
                checks["recomputed_mean"] = abs(statistics.fmean(samples) - mean) <= 1e-9 * max(1.0, abs(mean))
        except (KeyError, TypeError, ValueError) as e:
            return {"ok": False, "error": f"analysis artifact malformed: {e}"}
        failed = [k for k, v in checks.items() if not v]
        return {"ok": not failed,
                "answer": f"analysis integrity: {len(checks) - len(failed)}/{len(checks)} checks passed"
                          + (f" (failed: {', '.join(failed)})" if failed else ""),
                "tool_output": {"checks": checks}}
    payload = data if data is not None else text
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return {"ok": False, "error": "empty payload — nothing to validate"}
        candidate = stripped
        m = re.search(r"(\{.*\}|\[.*\])", stripped, re.S)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            if m and m.group(1) != stripped:
                try:
                    parsed = json.loads(m.group(1))
                    return {"ok": True, "answer": "valid JSON (embedded in the request)",
                            "tool_output": {"type": type(parsed).__name__,
                                            "size": len(parsed) if hasattr(parsed, "__len__") else 1,
                                            "keys": sorted(parsed)[:20] if isinstance(parsed, dict) else None}}
                except json.JSONDecodeError:
                    pass
            # Not JSON. R-110: distinguish garbage (no prose structure) from
            # real prose (sentences/numbers/lists). Garbage stays "invalid
            # JSON" (R-055a contract); real prose gets an integrity check.
            has_sentences = sum(1 for p in stripped.split(".") if p.strip()) >= 2
            has_numbers = any(ch.isdigit() for ch in stripped)
            has_lists = any(marker in stripped for marker in ("-", "*", "•", "1.", "2."))
            if not (has_sentences or has_numbers or has_lists):
                return {"ok": False,
                        "error": f"invalid JSON: {e.msg} at pos {e.pos}"}
            if stripped.lower().startswith(("error", "exception", "traceback", "failed")):
                return {"ok": False,
                        "error": f"payload looks like an error, not a result: {stripped[:80]}"}
            return {"ok": True, "answer": "valid prose",
                    "tool_output": {"chars": len(stripped),
                                    "sentences": len([p for p in stripped.split(".") if p.strip()])}}
        return {"ok": True, "answer": "valid JSON",
                "tool_output": {"type": type(parsed).__name__,
                                "size": len(parsed) if hasattr(parsed, "__len__") else 1}}
    return {"ok": True, "answer": "valid python object",
            "tool_output": {"type": type(payload).__name__}}


def _requirements(goal: str) -> Dict[str, Any]:
    """R-116: what the request actually contains (for the 'understand' stage)."""
    req: Dict[str, Any] = {}
    code = _code_in(goal)
    if code:
        req["inline_code"] = code[:300]
    nums = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", goal)]
    if nums:
        req["numbers"] = nums[:50]
    url = re.search(r"https?://\S+", goal)
    host = re.search(r"\b((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})\b", goal)
    if url or host:
        req["endpoint"] = (url or host).group(0)
    quoted = re.findall(r"""['"“«](.+?)['"”»]""", goal)
    if quoted:
        req["quoted_text"] = quoted[:5]
    phrase, _, target = _parse_translation(goal) if re.search(r"(?i)translat|ترجم", goal) else ("", "", None)
    if target:
        req["target_language"] = target
    req["action"] = (re.match(r"\s*(\w+)", goal) or [None, ""])[1].lower()
    return req


async def _analyst(work: Dict[str, Any]) -> Dict[str, Any]:
    if work.get("stage") == "understand" and work.get("goal"):
        # R-116: analyse the USER'S request — before, the understand stage
        # measured its own boilerplate instruction ("request, analyze, user, ...").
        goal = str(work["goal"])
        nums = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", goal)]
        if len(nums) < 2:
            out = craft.analyse_text(goal)
            req = _requirements(goal)
            if out.get("ok"):
                out["tool_output"] = {**(out.get("tool_output") or {}), "requirements": req}
                out["answer"] = (f"request: action='{req.get('action')}'"
                                 + "".join(f", {k}={str(v)[:60]!r}" for k, v in req.items() if k != "action")
                                 + f" | {out.get('answer', '')}")
            return out
        work = {**work, "prompt": goal}
    nums = _extract_numbers(work)
    if len(nums) < 2:
        # Fall back to a text analysis when no numeric samples — the
        # "understand" stage of a pipeline sends a prose prompt.
        text = _extract_text(work)
        if text.strip():
            return craft.analyse_text(text)  # R-102: real text measures, honest answer
        return {"ok": False, "error": "need >=2 numeric samples"}
    mu = statistics.fmean(nums)
    sd = statistics.pstdev(nums)
    outliers = [x for x in nums if sd and abs(x - mu) > 2 * sd]
    trend = "up" if nums[-1] > nums[0] else ("down" if nums[-1] < nums[0] else "flat")
    total = sum(nums)
    med = statistics.median(nums)
    # R-116: answer the question that was asked (sum / average / median / trend),
    # not always the same summary line.
    ask = _extract_text(work).lower()
    parts = []
    if re.search(r"\b(sum|total|add up)\b|مجموع|اجمع", ask):
        parts.append(f"sum={total:g}")
    if re.search(r"\b(average|mean)\b|متوسط", ask):
        parts.append(f"average={mu:g}")
    if re.search(r"\bmedian\b|وسيط", ask):
        parts.append(f"median={med:g}")
    if re.search(r"\btrends?\b|اتجاه", ask):
        diffs = [b - a for a, b in zip(nums, nums[1:])]
        steady = len(set(round(d, 9) for d in diffs)) == 1
        parts.append(f"trend={trend}" + (f" (constant step {diffs[0]:g})" if steady and diffs else ""))
    head = "; ".join(parts) if parts else f"n={len(nums)} mean={mu:.3f} sd={sd:.3f} trend={trend}"
    return {"ok": True, "answer": f"{head} (n={len(nums)})" if parts else head,
            "tool_output": {"n": len(nums), "sum": total, "mean": mu, "median": med, "stdev": sd,
                            "min": min(nums), "max": max(nums),
                            "outliers": outliers, "trend": trend, "samples": nums[:100]}}


async def _data_processor(work: Dict[str, Any]) -> Dict[str, Any]:
    nums = _extract_numbers(work)
    if not nums:
        # R-104 (D-047): text-only input — the deterministic aggregation has nothing
        # to do; delegate to the LLM-backed DataWorkerAgent path (honest failure when
        # the LLM key is missing — the exact OpenRouter error is propagated).
        text = _extract_text(work)
        if not text.strip():
            return {"ok": False, "error": "no numeric data and no text to process"}
        # R-104c2: send BOTH the prompt and the data to the LLM path — the prompt
        # alone was sent before, silently dropping data dict/list contents. The
        # payload assembly lives in agent_civilization/utils (its canonical home).
        from agent_civilization.utils import build_llm_payload
        payload = build_llm_payload(text, work.get("data"))
        from agent_civilization.agents.executors.data_worker import process_text
        out = await process_text(payload)
        if not out.get("ok"):
            return {"ok": False,
                    "error": f"llm-backed data processing unavailable: {out.get('error')}",
                    "model": out.get("model")}
        return {"ok": True, "answer": out.get("answer", ""),
                "tool_output": {"source": "llm", "model": out.get("model")}}
    sorted_ns = sorted(nums)
    q = lambda p: sorted_ns[min(len(sorted_ns) - 1, int(p * (len(sorted_ns) - 1)))]
    # R-122: the answer states the computed aggregates (was only "processed N samples").
    return {"ok": True, "answer": (f"processed {len(nums)} samples: sum={sum(nums):g} "
                                   f"median={statistics.median(nums):g} p25={q(0.25):g} p75={q(0.75):g}"),
            "tool_output": {"n": len(nums), "sum": sum(nums),
                             "median": statistics.median(nums),
                             "p25": q(0.25), "p75": q(0.75),
                             "variance": statistics.pvariance(nums) if len(nums) > 1 else 0}}


async def _db_manager(work: Dict[str, Any]) -> Dict[str, Any]:
    """SQL execution, schema design, migration rehearsal — all executed for real
    in an isolated SQLite engine (R-116). Before: anything without literal SQL
    failed with "no SQL statement detected"."""
    text = _extract_text(work)
    sql_match = re.search(r"\b(SELECT|CREATE|INSERT|UPDATE|DELETE|WITH)\b\s+.+", text, re.S)
    if sql_match and re.search(r"(?i)\b(from|table|into|set|as)\b", sql_match.group(0)):
        sql = sql_match.group(0)
        statements = [st for st in sql.split(";") if st.strip()]
        try:
            rows, cols = await _to_thread(safety.run_sqlite_statements, statements)
            return {"ok": True, "answer": f"{len(rows)} row(s)",
                    "tool_output": {"columns": cols, "rows": rows}}
        except sqlite3.Error as e:
            return {"ok": False, "error": f"sqlite: {e}"}
    goal = _goal(work)
    if re.search(r"(?i)\bmigrat", goal):
        return await _to_thread(_migration_rehearsal, goal)
    if re.search(r"(?i)\b(schema|table|tables|model|entities)\b|جدول|مخطط", goal):
        return await _schema_design(goal)
    return {"ok": False, "error": "no SQL statement, schema request or migration request detected"}


def _singular(word: str) -> str:
    return word[:-3] + "y" if word.endswith("ies") else word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _heuristic_schema(goal: str) -> list:
    m = re.search(r"(?i)\bfor\s+([a-z_ ]+?)(?:\s+with\s+([a-z_ ,]+?))?(?:[.?!]|$)", goal)
    if not m:
        return []
    parent = re.sub(r"\s+", "_", m.group(1).strip().lower())
    children = [re.sub(r"\s+", "_", c.strip().lower()) for c in re.split(r",|\band\b", m.group(2) or "") if c.strip()]
    fk = f"{_singular(parent)}_id"
    stmts = [f"CREATE TABLE {parent} (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'new')"]
    for child in children:
        cols = ["id INTEGER PRIMARY KEY", f"{fk} INTEGER NOT NULL REFERENCES {parent}(id) ON DELETE CASCADE"]
        if "item" in child:
            cols += ["product TEXT NOT NULL", "quantity INTEGER NOT NULL CHECK (quantity > 0)",
                     "unit_price NUMERIC NOT NULL CHECK (unit_price >= 0)"]
        else:
            cols += ["name TEXT NOT NULL"]
        stmts.append(f"CREATE TABLE {child} ({', '.join(cols)})")
        stmts.append(f"CREATE INDEX idx_{child}_{fk} ON {child}({fk})")
    return stmts


async def _schema_design(goal: str) -> Dict[str, Any]:
    method, model, llm_error = "heuristic", None, None
    llm = await _llm_text("You design SQLite schemas. Reply with SQLite DDL only (CREATE TABLE / CREATE INDEX statements), no prose.",
                          goal, max_tokens=600)
    stmts: list = []
    if llm["ok"]:
        body = llm["content"]
        m = re.search(r"```(?:sql)?\n(.+?)```", body, re.S)
        body = m.group(1) if m else body
        stmts = [st.strip() for st in body.split(";") if re.match(r"(?is)\s*create\s", st)]
        method, model = "llm", llm.get("model")
    else:
        llm_error = llm["error"]
    # tables before indexes/other statements (LLMs sometimes list indexes first)
    stmts = ([st for st in stmts if re.match(r"(?is)\s*create\s+table", st)]
             + [st for st in stmts if not re.match(r"(?is)\s*create\s+table", st)])
    attempts = [(method, stmts)] if stmts else []
    heur = _heuristic_schema(goal)
    if heur and method != "heuristic":
        attempts.append(("heuristic", heur))
    elif heur:
        attempts = [("heuristic", heur)]
    if not attempts:
        return {"ok": False, "error": f"could not derive a schema (llm: {llm_error})"}
    rows, errors = None, []
    for method, stmts in attempts:
        verify = stmts + ["SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"]
        try:
            rows, _ = await _to_thread(safety.run_sqlite_statements, verify)
            break
        except sqlite3.Error as e:
            errors.append(f"{method}: {e}")
    if rows is None:
        return {"ok": False, "error": "schema does not execute in SQLite: " + "; ".join(errors)}
    if errors:
        llm_error = (llm_error + "; " if llm_error else "") + "; ".join(errors)
    tables = [r[0] for r in rows]
    return {"ok": True,
            "answer": f"schema with {len(tables)} table(s) ({', '.join(tables)}) — executed in SQLite ({method})",
            "tool_output": {"method": method, "model": model, "llm_error": llm_error, "tables": tables,
                            "sql": ";\n".join(stmts) + ";", "verified": "executed in isolated SQLite"}}


def _migration_rehearsal(goal: str, sample_rows: int = 20000, batch: int = 2000) -> Dict[str, Any]:
    """Rehearse an online expand -> backfill (in batches) -> verify -> index
    migration on a real SQLite sample and extrapolate the timing to the row
    count named in the request. Every number below is measured here."""
    m = re.search(r"(?i)(\d+(?:\.\d+)?)\s*([km]|million|thousand)?\s*-?\s*rows?", goal)
    target = sample_rows
    if m:
        n = float(m.group(1))
        unit = (m.group(2) or "").lower()
        target = int(n * (1_000_000 if unit in ("m", "million") else 1_000 if unit in ("k", "thousand") else 1))
    conn = sqlite3.connect(":memory:")
    steps = []
    try:
        conn.set_authorizer(safety._sqlite_authorizer)
        t = time.perf_counter()
        conn.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, amount_cents INTEGER NOT NULL)")
        conn.execute(f"WITH RECURSIVE s(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM s WHERE i < {sample_rows}) "
                     "INSERT INTO big(id, amount_cents) SELECT i, (i * 37) % 100000 FROM s")
        steps.append({"step": "seed sample", "rows": sample_rows, "ms": round((time.perf_counter() - t) * 1000, 2)})
        t = time.perf_counter()
        conn.execute("ALTER TABLE big ADD COLUMN amount NUMERIC")  # expand: nullable, no rewrite
        steps.append({"step": "expand: add nullable column", "ms": round((time.perf_counter() - t) * 1000, 2)})
        t = time.perf_counter()
        batches = 0
        for lo in range(1, sample_rows + 1, batch):
            conn.execute("UPDATE big SET amount = amount_cents / 100.0 WHERE id BETWEEN ? AND ? AND amount IS NULL",
                         (lo, lo + batch - 1))
            conn.commit()
            batches += 1
        backfill_ms = (time.perf_counter() - t) * 1000
        steps.append({"step": f"backfill in batches of {batch}", "batches": batches, "ms": round(backfill_ms, 2)})
        t = time.perf_counter()
        nulls = conn.execute("SELECT COUNT(*) FROM big WHERE amount IS NULL").fetchone()[0]
        mismatch = conn.execute("SELECT COUNT(*) FROM big WHERE CAST(ROUND(amount * 100) AS INTEGER) <> amount_cents").fetchone()[0]
        steps.append({"step": "verify", "null_rows": nulls, "mismatched_rows": mismatch,
                      "ms": round((time.perf_counter() - t) * 1000, 2)})
        t = time.perf_counter()
        conn.execute("CREATE INDEX idx_big_amount ON big(amount)")
        steps.append({"step": "index new column", "ms": round((time.perf_counter() - t) * 1000, 2)})
    finally:
        conn.close()
    ok = nulls == 0 and mismatch == 0
    rate = sample_rows / max(backfill_ms / 1000, 1e-9)
    est_s = target / rate
    return {"ok": ok,
            "answer": (f"migration rehearsed on {sample_rows:,} rows: expand -> {batches} batch backfill -> verify "
                       f"({nulls} nulls, {mismatch} mismatches) -> index; measured {rate:,.0f} rows/s, "
                       f"estimated backfill for {target:,} rows ≈ {est_s:,.1f}s on this engine")
            if ok else f"migration rehearsal failed verification ({nulls} nulls, {mismatch} mismatches)",
            "tool_output": {"method": "expand-backfill-verify rehearsal (SQLite sample)", "steps": steps,
                            "sample_rows": sample_rows, "target_rows": target, "rows_per_s": round(rate),
                            "estimated_backfill_s": round(est_s, 1),
                            "plan": ["add the new column as nullable (no table rewrite)",
                                     f"backfill in batches of {batch} rows, committing each batch",
                                     "verify: no NULLs and values match the source column",
                                     "build the index after the backfill",
                                     "switch reads/writes to the new column, then drop the old one in a later release"]}}


async def _network_client(work: Dict[str, Any]) -> Dict[str, Any]:
    """DNS + live HTTP probe (R-116: a host-only request is now really probed
    over HTTPS/HTTP — before, "is example.com reachable" only resolved DNS)."""
    url = _extract_url(work) or _extract_url({"prompt": _goal(work)})
    host = None
    if not url:
        for source in (_extract_text(work), _goal(work)):
            host_match = re.search(r"\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})\b", source or "")
            if host_match:
                host = host_match.group(1)
                break
        if not host:
            return {"ok": False, "error": "no url/host detected"}

    def head_or_get(target: str):
        t0 = time.time()
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(target, method=method,
                                         headers={"User-Agent": "AGOS-Civilization/1.0"})
            try:
                with safety.safe_urlopen(req, timeout=6) as resp:
                    return {"status": resp.status, "url": resp.geturl(), "method": method,
                            "content_type": resp.headers.get("content-type", ""),
                            "content_length": resp.headers.get("content-length"),
                            "server": resp.headers.get("server", ""),
                            "latency_ms": round((time.time() - t0) * 1000, 2)}
            except urllib.error.HTTPError as e:
                if method == "HEAD" and e.code in (403, 405, 501):
                    continue  # some servers refuse HEAD — retry with GET
                return {"status": e.code, "url": target, "method": method,
                        "latency_ms": round((time.time() - t0) * 1000, 2)}
        raise RuntimeError("no response")

    ask = f"{_extract_text(work)} {_goal(work)}".lower()
    dns_only = bool(re.search(r"\b(resolve|dns|lookup|nslookup)\b", ask)) and \
        not re.search(r"\b(reachable|reachability|http|up|ping|latency|probe)\b|متاح|اتصال", ask)
    if host:
        def probe():
            t0 = time.time()
            addrs = safety.check_host(host)
            dns = {"host": host, "ip": addrs[0], "addresses": addrs,
                   "resolve_ms": round((time.time() - t0) * 1000, 2)}
            if dns_only:  # the user asked for name resolution only
                return {**dns, "reachable": None, "dns_only": True}
            errors = []
            for scheme in ("https", "http"):
                try:
                    return {**dns, "http": head_or_get(f"{scheme}://{host}/"), "reachable": True}
                except safety.UnsafeURLError:
                    raise
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{scheme}: {e}")
            return {**dns, "reachable": False, "http_errors": errors}
        try:
            out = await _to_thread(probe)
        except safety.UnsafeURLError as e:
            return {"ok": False, "error": f"blocked: {e}"}
        except socket.gaierror as e:
            return {"ok": False, "error": f"dns: {e}"}
        if out.get("dns_only"):
            return {"ok": True, "answer": f"{host} resolves to {', '.join(out['addresses'][:4])} "
                                          f"in {out['resolve_ms']} ms (DNS only, as asked)", "tool_output": out}
        if not out.get("reachable"):
            return {"ok": False, "error": f"{host} resolved but did not answer over HTTP(S)",
                    "tool_output": out}
        h = out["http"]
        return {"ok": True,
                "answer": f"{host} is reachable: HTTP {h['status']} via {h['method']} in {h['latency_ms']} ms (DNS {out['resolve_ms']} ms)",
                "tool_output": out}
    try:
        h = await _to_thread(head_or_get, url)
        return {"ok": True, "answer": f"{h['method']} {url} -> HTTP {h['status']} in {h['latency_ms']} ms",
                "tool_output": h}
    except safety.UnsafeURLError as e:
        return {"ok": False, "error": f"blocked: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"http: {e}"}


async def _api_integrator(work: Dict[str, Any]) -> Dict[str, Any]:
    url = _extract_url(work)
    if not url:
        return {"ok": False, "error": "no url detected"}
    def get():
        req = urllib.request.Request(url, headers={"User-Agent": "AGOS/1.0",
                                                    "Accept": "application/json,text/*"})
        t0 = time.time()
        with safety.safe_urlopen(req, timeout=8) as resp:
            body = resp.read(65536)
            ct = resp.headers.get("content-type", "")
            parsed = None
            if "json" in ct:
                try:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                except Exception:
                    pass
            return {"status": resp.status, "content_type": ct,
                    "latency_ms": round((time.time() - t0) * 1000, 2),
                    "bytes": len(body),
                    "sample": (parsed if parsed is not None
                               else body[:512].decode("utf-8", errors="replace"))}
    try:
        out = await _to_thread(get)
        # R-122: the answer states the real response (was only "GET <url>").
        return {"ok": True, "tool_output": out,
                "answer": (f"GET {url} -> HTTP {out['status']} ({out['content_type'] or 'no content-type'}, "
                           f"{out['bytes']} bytes, {out['latency_ms']} ms)")}
    except safety.UnsafeURLError as e:
        return {"ok": False, "error": f"blocked: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"http: {e}"}


# R-107: which DuckDuckGo failures are "transient" (worth a retry) vs. hard errors
# (empty query, HTTP 4xx/5xx, address-policy refusals, certificate-verification
# failures — never retried). Only transport flakiness retries.
_TRANSIENT_NET_ERRORS = (
    ConnectionResetError,           # Errno 104 — seen on CI job network (raw/59)
    ConnectionAbortedError,          # Errno 53
    TimeoutError,                   # socket.timeout / SSL handshake timeout
)


def _is_transient_search_error(exc: Exception) -> bool:
    """Only network/transport flakiness retries — never policy refusals, HTTP 4xx/5xx,
    or SSL certificate-verification failures (those indicate a real TLS problem,
    not a flake, and retrying them wastes the budget)."""
    if isinstance(exc, urllib.error.HTTPError):
        return False  # HTTP status — hard
    if isinstance(exc, _TRANSIENT_NET_ERRORS):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if reason is None:
            return False  # unknown cause — NOT transient (no blind retry)
        return isinstance(reason, _TRANSIENT_NET_ERRORS) or _is_transient_search_error(reason)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False  # certificate problem — hard (retrying won't help)
    if isinstance(exc, ssl.SSLError):
        # R-107c: narrower than "all SSL" — only the transport-level EOF and
        # handshake-timeout flakes seen in raw/32/33/34/48. Protocol errors
        # (WRONG_VERSION_NUMBER etc.) are hard.
        msg = str(exc).lower()
        # Only the specific transport flakes seen in the wild (raw/32/33/34/48):
        # an unexpected EOF mid-stream, or a transport-level connection reset.
        # A handshake FAILURE (TLS alert, e.g. SSLV3_ALERT_HANDSHAKE_FAILURE) is a
        # real TLS problem — hard. A handshake TIMEOUT is a TimeoutError (handled above).
        return "unexpected_eof" in msg or "connection reset" in msg
    if isinstance(exc, OSError) and exc.errno in (104, 53):
        return True
    return False


_RESEARCHER_ATTEMPTS = 2  # one live attempt + one retry for transient errors only
_RESEARCHER_TOTAL_TIMEOUT_S = 20.0  # hard budget for the whole handler (both attempts)


def _researcher_deadline() -> float:
    # R-104c2: the deadline helpers live in agent_civilization/runtime (their
    # canonical home) — the live handler consumes them from there.
    from agent_civilization.runtime import make_deadline
    return make_deadline(_RESEARCHER_TOTAL_TIMEOUT_S)


def _remaining_budget(deadline: float) -> float:
    from agent_civilization.runtime import remaining
    return remaining(deadline)


async def _researcher(work: Dict[str, Any]) -> Dict[str, Any]:
    q = _extract_text(work).strip()
    # R-116: a grounding stage ("Perform a live web search to ground ...") used
    # to search for its own instruction text; it now searches the user's goal.
    if work.get("stage") not in (None, "execute") and work.get("goal"):
        q = str(work["goal"]).strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    # DuckDuckGo Instant Answer API — no key needed.
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "no_html": "1", "no_redirect": "1"})
    deadline = _researcher_deadline()
    last_error: Optional[Exception] = None
    attempts_made = 0
    for attempt in range(_RESEARCHER_ATTEMPTS):
        remaining = _remaining_budget(deadline)
        if remaining <= 0.5:
            break  # budget exhausted — no more attempts (count what actually ran)
        socket_timeout = min(8.0, remaining)
        def fetch():
            req = urllib.request.Request(url, headers={"User-Agent": "AGOS-Research/1.0"})
            with safety.safe_urlopen(req, timeout=socket_timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        try:
            # The wait is bounded by the REMAINING budget itself (not budget+1s), so the
            # handler cannot exceed its deadline by more than scheduling latency.
            # Note (honest limit): cancelling this wait does NOT stop the worker
            # thread — a stuck DNS resolve or socket read keeps running in the
            # background until its own timeout; we only stop waiting for it.
            j = await asyncio.wait_for(_to_thread(fetch), timeout=remaining)
            attempts_made += 1
            abstract = j.get("AbstractText") or ""
            related = [r.get("Text", "") for r in j.get("RelatedTopics", [])[:5]
                       if isinstance(r, dict) and r.get("Text")]
            if not abstract and not related:
                # R-116: the Instant Answer API has nothing for most real questions;
                # before, that empty result was still reported as ok=True ("no
                # direct answer; see related") with no sources. Now: live web
                # results, and failure if there are none.
                try:
                    results = await asyncio.wait_for(_to_thread(_web_search, q, 5, min(10.0, remaining)),
                                                     timeout=max(1.0, _remaining_budget(deadline)))
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": f"search: no instant answer and web search failed: {e}"}
                if not results:
                    return {"ok": False, "error": "search: no sources found"}
                return {"ok": True,
                        "answer": f"{len(results)} source(s): " + "; ".join(r["title"] for r in results[:3]),
                        "tool_output": {"method": "web-search", "query": q[:300],
                                        "sources": results, "abstract": ""}}
            out = {"ok": True,
                   "answer": abstract or ("related: " + related[0][:200]),
                   "tool_output": {"method": "instant-answer", "abstract": abstract,
                                    "source": j.get("AbstractURL", ""),
                                    "related": related}}
            if attempt:
                out["tool_output"]["retried"] = True
                out["tool_output"]["attempts"] = attempts_made
            return out
        except Exception as e:  # noqa: BLE001
            attempts_made += 1
            if isinstance(e, asyncio.TimeoutError):
                e = TimeoutError("overall budget exceeded")  # normalize for the classifier
            if not _is_transient_search_error(e):
                return {"ok": False, "error": f"search: {e}"}  # hard error — no retry
            last_error = e
    if attempts_made == 0:
        return {"ok": False,
                "error": f"search: budget exhausted before any attempt could start "
                         f"({_RESEARCHER_TOTAL_TIMEOUT_S}s)"}
    return {"ok": False, "error": f"search: {last_error} (transient, {attempts_made} attempts)"}


async def _architect(work: Dict[str, Any]) -> Dict[str, Any]:
    """Component graph derived from the request (R-102): requested + implied
    components, dependencies and a topological build order."""
    # R-116: as the "plan" stage, derive the architecture of the user's GOAL —
    # before, it parsed its own instruction ("list files/tools/APIs" -> storage).
    text = _goal(work) if work.get("stage") == "plan" else _extract_text(work)
    return craft.design_architecture(text, work.get("data"))


async def _strategist(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
    data = work.get("data")
    plan_stages = work.get("plan_stages")
    if isinstance(plan_stages, list) and plan_stages:
        # R-116: the plan stage returns the execution plan the civilization will
        # ACTUALLY run for this goal (each step, the specialist, and why) —
        # before, it split its own instruction sentence into "objectives".
        steps = [{"step": i + 1, "stage": st.get("name"), "specialist": st.get("specialization"),
                  "purpose": st.get("reason")} for i, st in enumerate(plan_stages)]
        return {"ok": True,
                "answer": " -> ".join(f"{st['stage']}({st['specialist']})" for st in steps),
                "tool_output": {"roadmap": steps, "goal": _goal(work)[:300], "source": "execution plan"}}
    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
        steps = [x.strip() for x in data if x.strip()]  # R-102: objectives given as a list
    else:
        steps = [s.strip() for s in re.split(r"[.!؟\n]", text) if s.strip()]
    priorities = [{"step": i + 1, "goal": s[:120],
                   "weight": round(1.0 - i / max(len(steps), 1), 2)}
                  for i, s in enumerate(steps[:6])]
    # R-102: weights follow the order given — say so, do not claim prioritisation logic.
    return {"ok": True, "answer": f"{len(priorities)} objective(s) in the order given (weight by position)",
            "tool_output": {"roadmap": priorities}}


def _static_findings(code: str):
    """AST checks shared by reviewer and surgeon (R-116)."""
    tree = ast.parse(code)
    issues = []
    import builtins as _b
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(f"bare except at line {node.lineno}")
        if isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None
            for op, c in zip(node.ops, node.comparators)
        ):
            issues.append(f"== None instead of `is None` at line {node.lineno}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + node.args.kw_defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    issues.append(f"mutable default argument in {node.name}() at line {node.lineno}")
            for a in node.args.args:
                if a.arg in dir(_b):
                    issues.append(f"parameter '{a.arg}' shadows a builtin in {node.name}() at line {node.lineno}")
            if not any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node)) and \
                    not any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node)):
                if not (len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Expr))):
                    issues.append(f"{node.name}() never returns a value (line {node.lineno})")
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) \
                and isinstance(node.right, ast.Constant) and node.right.value == 0:
            issues.append(f"division by zero at line {node.lineno}")
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) and any(
                isinstance(c, ast.Constant) and isinstance(c.value, (int, str)) and not isinstance(c.value, bool)
                for c in node.comparators):
            issues.append(f"`is` comparison with a literal at line {node.lineno}")
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return issues, funcs


async def _reviewer(work: Dict[str, Any]) -> Dict[str, Any]:
    code = _extract_code(work)
    if not code.strip():
        text = _extract_text(work)
        wc = len(text.split())
        return {"ok": True, "answer": f"text metrics only (no code to review): {wc} words",
                "tool_output": {"words": wc, "chars": len(text), "code_reviewed": False}}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        return {"ok": False, "error": f"tokenize: {e}"}
    comments = sum(1 for t in tokens if t.type == tokenize.COMMENT)
    loc = code.count("\n") + 1
    try:
        issues, funcs = _static_findings(code)
    except SyntaxError as e:
        return {"ok": True, "answer": f"reviewed {loc} loc: syntax error at line {e.lineno}: {e.msg}",
                "tool_output": {"loc": loc, "syntax_error": f"line {e.lineno}: {e.msg}", "issues": [f"syntax error line {e.lineno}"],
                                "code_reviewed": True}}
    return {"ok": True,
            "answer": (f"reviewed {loc} loc, {len(funcs)} function(s): {len(issues)} issue(s)"
                       + (": " + "; ".join(issues[:5]) if issues else " — no defects found by static checks")),
            "tool_output": {"loc": loc, "comments": comments, "comment_ratio": round(comments / max(loc, 1), 3),
                            "functions": funcs, "issues": issues, "code_reviewed": True,
                            "checks": ["bare except", "== None", "mutable defaults", "builtin shadowing",
                                       "missing return", "division by zero", "is-literal"]}}


async def _monitor(work: Dict[str, Any]) -> Dict[str, Any]:
    """Health check of the host process and machine (R-116: the answer now
    states the measured values and a verdict — before it said only "system
    snapshot captured", hiding what was measured)."""
    def snapshot():
        import os
        import resource
        import shutil
        ru = resource.getrusage(resource.RUSAGE_SELF)
        snap = {"pid": os.getpid(),
                "user_cpu_s": round(ru.ru_utime, 3),
                "sys_cpu_s": round(ru.ru_stime, 3),
                "max_rss_kb": ru.ru_maxrss,
                "cpus": os.cpu_count() or 1,
                "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None}
        try:
            with open("/proc/self/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        snap["rss_kb"] = int(line.split()[1])
            with open("/proc/meminfo", encoding="utf-8") as fh:
                mem = {ln.split(":")[0]: int(ln.split()[1]) for ln in fh if ln.split()[1:2]}
            snap["mem_total_kb"], snap["mem_available_kb"] = mem.get("MemTotal"), mem.get("MemAvailable")
        except OSError:
            pass
        du = shutil.disk_usage("/")
        snap["disk_free_pct"] = round(100 * du.free / du.total, 1)
        return snap
    try:
        snap = await _to_thread(snapshot)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    problems = []
    if snap.get("loadavg") and snap["loadavg"][0] / snap["cpus"] > 2.0:
        problems.append(f"load {snap['loadavg'][0]:.2f} on {snap['cpus']} cpu(s)")
    if snap.get("mem_total_kb") and snap.get("mem_available_kb") is not None:
        avail_pct = 100 * snap["mem_available_kb"] / snap["mem_total_kb"]
        snap["mem_available_pct"] = round(avail_pct, 1)
        if avail_pct < 10:
            problems.append(f"memory available {avail_pct:.1f}%")
    if snap["disk_free_pct"] < 5:
        problems.append(f"disk free {snap['disk_free_pct']}%")
    snap["verdict"] = "degraded" if problems else "healthy"
    parts = [f"rss={snap.get('rss_kb', snap['max_rss_kb']) / 1024:.1f}MB"]
    if snap.get("loadavg"):
        parts.append(f"load1={snap['loadavg'][0]:.2f}/{snap['cpus']}cpu")
    if "mem_available_pct" in snap:
        parts.append(f"mem_avail={snap['mem_available_pct']}%")
    parts.append(f"disk_free={snap['disk_free_pct']}%")
    return {"ok": True,
            "answer": f"system {snap['verdict']}: " + ", ".join(parts)
                      + (f" — problems: {'; '.join(problems)}" if problems else ""),
            "tool_output": snap}


async def _educator(work: Dict[str, Any]) -> Dict[str, Any]:
    """Explain the user's topic (R-116). Before: returned the first sentences of
    the request itself as "key points". Now: an LLM explanation when one is
    available, otherwise key points taken from live web sources; the method and
    sources are always reported. Fails honestly when neither is reachable."""
    topic = _goal(work).strip()
    if not topic:
        return {"ok": False, "error": "nothing to explain"}
    llm = await _llm_text(
        "You are a precise teacher. Explain clearly in at most 180 words, with a short bullet list of key differences or points.",
        topic, max_tokens=500)
    if llm["ok"]:
        text = llm["content"]
        points = [ln.strip("-*• ").strip() for ln in text.splitlines() if ln.strip().startswith(("-", "*", "•"))]
        return {"ok": True, "answer": text[:1500],
                "tool_output": {"method": "llm", "model": llm.get("model"), "provider": llm.get("provider"),
                                "key_points": points[:8], "explanation": text[:4000],
                                "reading_time_min": round(len(text.split()) / 200, 2)}}
    # Material supplied by the caller (>= 2 sentences): without an LLM, its
    # key points are a real, honestly-labelled result (the R-102 behaviour).
    # A bare question ("explain X") is NOT material — echoing it back was the
    # hollow success R-116 removed.
    material = _extract_text(work)
    sentences = [x.strip() for x in re.split(r"[.!؟?\n]", material) if len(x.strip().split()) >= 2]
    if len(sentences) >= 2:
        keypoints = sentences[:5]
        return {"ok": True, "answer": f"first {len(keypoints)} sentence(s) as key points",
                "tool_output": {"method": "key-points-from-provided-text", "llm_error": llm["error"],
                                "key_points": keypoints,
                                "reading_time_min": round(len(material.split()) / 200, 2)}}
    try:
        results = await _to_thread(_web_search, topic, 5)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"no explanation source: {llm['error']}; web: {e}"}
    points = [r["snippet"] for r in results if r.get("snippet")]
    if not points:
        return {"ok": False, "error": f"no explanation source: {llm['error']}; web search returned no snippets"}
    return {"ok": True, "answer": " | ".join(points[:3])[:1500],
            "tool_output": {"method": "web-snippets", "llm_error": llm["error"], "key_points": points[:5],
                            "sources": [{"title": r["title"], "url": r["url"]} for r in results]}}


async def _communicator(work: Dict[str, Any]) -> Dict[str, Any]:
    """Translate, draft, or measure tone (R-116).

    Before: every request (translate / draft) got only a lexical tone score —
    "translate 'hello world' to French" answered "tone=neutral". Now:
      * translation -> live translation service (MyMemory), LLM as fallback;
      * draft/write a message -> LLM draft (fails honestly with no LLM);
      * otherwise -> the deterministic tone measure (unchanged).
    """
    goal = _goal(work)
    text = _extract_text(work)
    if re.search(r"(?i)\btranslat\w*|ترجم", goal):
        phrase, source, target = _parse_translation(goal)
        if not target:
            return {"ok": False, "error": "translation requested but no target language recognised"}
        if not phrase.strip():
            return {"ok": False, "error": "translation requested but no text found"}
        try:
            out = await _to_thread(_mymemory, phrase, source, target)
            if out.strip():
                return {"ok": True, "answer": out,
                        "tool_output": {"method": "mymemory", "source_lang": source, "target_lang": target,
                                        "source_text": phrase, "translation": out}}
            err = "empty translation"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        llm = await _llm_text(f"Translate the user's text into language code '{target}'. Reply with the translation only.",
                              phrase, max_tokens=200)
        if llm["ok"]:
            return {"ok": True, "answer": llm["content"],
                    "tool_output": {"method": "llm", "model": llm.get("model"), "source_lang": source,
                                    "target_lang": target, "source_text": phrase,
                                    "translation": llm["content"], "service_error": err}}
        return {"ok": False, "error": f"translation failed: service: {err}; llm: {llm['error']}"}
    if re.search(r"(?i)\b(draft|write|compose)\b.*\b(message|email|announcement|post|note)\b|صغ|اكتب رسالة", goal):
        llm = await _llm_text("You write clear, professional workplace messages. Reply with the message only, under 150 words.",
                              goal, max_tokens=400)
        if not llm["ok"]:
            return {"ok": False, "error": f"drafting a message needs an LLM: {llm['error']}"}
        tone = _tone(llm["content"])
        return {"ok": True, "answer": llm["content"][:1500],
                "tool_output": {"method": "llm", "model": llm.get("model"), "draft": llm["content"][:4000],
                                "words": len(llm["content"].split()), **tone}}
    tone = _tone(text)
    return {"ok": True, "answer": f"tone={tone['label']}", "tool_output": tone}


def _tone(text: str) -> Dict[str, Any]:
    # sentiment via simple lexical scoring — deterministic, real computation
    pos = {"good", "great", "excellent", "success", "win", "improve", "fast", "safe",
           "جيد", "ممتاز", "ناجح", "نجاح", "سريع"}
    neg = {"bad", "fail", "slow", "error", "bug", "risk", "danger",
           "سيء", "خطأ", "بطيء", "فشل", "خطر"}
    tokens = re.findall(r"\w+", text.lower())
    p = sum(1 for t in tokens if t in pos)
    n = sum(1 for t in tokens if t in neg)
    score = (p - n) / max(len(tokens), 1)
    label = "positive" if score > 0.01 else "negative" if score < -0.01 else "neutral"
    return {"positive": p, "negative": n, "score": round(score, 3), "label": label}


async def _designer(work: Dict[str, Any]) -> Dict[str, Any]:
    """Palette from the colour (hex or name) and mood in the request, every
    foreground checked against WCAG contrast targets (R-102)."""
    return craft.design_palette(_extract_text(work), work.get("data"))


async def _builder(work: Dict[str, Any]) -> Dict[str, Any]:
    """Scaffold real files, verify each (compile/json/toml), return them (R-102).
    Before: answered "N build action(s) queued" with no queue."""
    return await _to_thread(craft.scaffold, _extract_text(work), work.get("data"))


async def _modifier(work: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the requested edit (rename / remove unused imports / refactor) and
    return the new code + diff, AST-verified (R-102, R-116). A refactor is also
    checked for behavioural equivalence in the sandbox when code execution is
    allowed (every boolean assignment of the fragment's free names)."""
    code = _extract_code(work)
    if not code.strip():
        return {"ok": False, "error": "no code to modify"}
    instruction = " ".join(str(work.get(k) or "") for k in ("prompt", "text", "query", "goal", "message"))
    out = craft.modify_code(code, instruction)
    if not out.get("ok"):
        return out
    to = out.get("tool_output") or {}
    new_code = to.get("new_code") or ""
    if any(a.startswith("merged") for a in to.get("applied", [])):
        before, _ = craft.normalise_one_line_compounds(code)
        eq = await _equivalence_check(before, new_code)
        to["equivalence"] = eq
        if eq.get("checked") and not eq.get("equivalent"):
            return {"ok": False, "error": f"refactor changed behaviour: {eq.get('detail')}", "tool_output": to}
        if eq.get("checked"):
            out["answer"] += f"; behaviour identical on {eq['cases']} input combination(s)"
    to["code"] = new_code
    out["tool_output"] = to
    return out


async def _equivalence_check(before: str, after: str) -> Dict[str, Any]:
    allowed, reason = safety.code_execution_policy()
    if not allowed:
        return {"checked": False, "reason": reason}
    import builtins as _b
    names = sorted({n.id for src in (before, after) for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - set(dir(_b)))[:8]
    def wrap(fn, src):
        body = "\n".join("    " + ln for ln in src.splitlines()) or "    pass"
        return f"def {fn}({', '.join(names)}):\n{body}\n"
    script = (wrap("_before", before) + wrap("_after", after) +
              "import itertools\n"
              f"cases = list(itertools.product([False, True], repeat={len(names)}))\n"
              "bad = [c for c in cases if _before(*c) != _after(*c)]\n"
              "print(len(cases), len(bad), bad[:1])\n")
    r = await _to_thread(safety.run_sandboxed_python, script)
    if r["timed_out"] or r["returncode"] != 0:
        return {"checked": False, "reason": (r.get("stderr") or "sandbox failure")[-200:]}
    total, bad, sample = r["stdout"].split(maxsplit=2)
    return {"checked": True, "equivalent": int(bad) == 0, "cases": int(total), "names": names,
            "detail": sample.strip()}


# R-125: offline probes the self-development cycle runs against its own handlers
# (deterministic: no network, no LLM, no code execution).
_SELF_PROBES: Dict[str, Tuple[Dict[str, Any], Callable[[Dict[str, Any]], bool]]] = {
    "analyst": ({"data": [2, 4, 6]}, lambda r: "mean=4" in str(r.get("answer"))),
    "data_processor": ({"data": [1, 2, 3, 4]}, lambda r: "sum=10" in str(r.get("answer"))),
    "validator": ({"data": {"type": "json", "value": [1, 2]}}, lambda r: bool(r.get("ok"))),
    "surgeon": ({"data": "def f(x=[]):\n    return x\n"}, lambda r: "mutable" in str(r.get("answer")).lower()),
    "reviewer": ({"data": "def f(x):\n    return x * 2\n"}, lambda r: "reviewed" in str(r.get("answer"))),
    "strategist": ({"data": ["a", "b", "c"]}, lambda r: bool(r.get("ok"))),
    "designer": ({"data": "brand: forest"}, lambda r: bool(r.get("ok"))),
    "db_manager": ({"prompt": "SELECT 1 AS one;"}, lambda r: bool(r.get("ok"))),
}
_EXTERNAL_HINTS = ("search:", "llm", "http", "timed out", "timeout", "rate", "urlopen", "connection",
                   "network", "dns", "unreachable", "429", "503")
_SELF_CIV: Optional[Dict[str, list]] = None


def _self_retest_max() -> int:
    try:
        return max(0, min(10, int(os.getenv("AGOS_SELF_RETEST_MAX", "3"))))
    except ValueError:
        return 3


async def _self_developer(work: Dict[str, Any]) -> Dict[str, Any]:
    """R-125 (D-053): a real self-development cycle instead of a static listing.

    1. probe — run deterministic probes against its own handlers and check the
       answers (a handler that errors or answers wrongly is reported);
    2. retest — take the most recent *remembered failures* from the experience
       layer (distinct user goals, newest first, AGOS_SELF_RETEST_MAX, default 3)
       and run each goal again through the real router + pipeline;
    3. change — a goal that now succeeds on every stage has its failure
       memories retired (removed from the store, so recall stops surfacing a
       fixed failure); one that still fails is reported with the failing stage,
       its reason and whether the cause looks external (network/LLM/search) or
       internal.
    It does NOT edit source code (D-053: no self-modifying code without the owner).
    """
    global _SELF_CIV
    from agent_civilization.core import experience as _exp

    real_specs = sorted(_HANDLERS.keys())
    probes = {}
    for spec, (pw, check) in _SELF_PROBES.items():
        try:
            r = await _HANDLERS[spec](dict(pw))
            good = bool(isinstance(r, dict) and r.get("ok") and check(r))
            probes[spec] = {"ok": good, "answer": str((r or {}).get("answer") or (r or {}).get("error") or "")[:160]}
        except Exception as e:  # noqa: BLE001
            probes[spec] = {"ok": False, "answer": f"{type(e).__name__}: {e}"[:160]}
    healthy = sum(1 for v in probes.values() if v["ok"])

    retests: List[Dict[str, Any]] = []
    retired = 0
    if _exp.enabled():
        mem = _exp.memory()
        failed = [e for e in mem._memories.values()
                  if e.metadata.get("layer") == "experience" and not e.metadata.get("ok")
                  and e.metadata.get("specialization") != "self_developer" and e.metadata.get("goal")]
        failed.sort(key=lambda e: e.created_at, reverse=True)
        goals: List[str] = []
        for e in failed:
            g = e.metadata["goal"]
            if g not in goals:
                goals.append(g)
        if goals and _self_retest_max():
            from agent_civilization.agents.real import create_civilization
            from agent_civilization.pipeline.orchestrator import execute_plan
            from agent_civilization.routing.auto_router import route
            if _SELF_CIV is None:
                _SELF_CIV = create_civilization(1)
            for goal in goals[:_self_retest_max()]:
                plan = route(goal)
                if any(st.get("specialization") == "self_developer" for st in plan.get("stages", [])):
                    continue
                try:
                    res = await asyncio.wait_for(execute_plan(plan, _SELF_CIV), timeout=120)
                    stages = res.get("stages", [])
                except Exception as e:  # noqa: BLE001
                    stages = [{"name": "retest", "ok": False, "output": f"{type(e).__name__}: {e}"}]
                bad = next((s for s in stages if not s.get("ok")), None)
                item = {"goal": goal[:160], "fixed": bool(stages) and bad is None}
                if item["fixed"]:
                    ids = [e.memory_id for e in failed if e.metadata.get("goal") == goal]
                    for mid in ids:
                        if await mem.forget(mid):
                            retired += 1
                    item["retired_memories"] = len(ids)
                else:
                    reason = str((bad or {}).get("output") or "no stages")[:200]
                    item.update(stage=(bad or {}).get("name"), specialization=(bad or {}).get("specialization"),
                                reason=reason,
                                cause="external" if any(h in reason.lower() for h in _EXTERNAL_HINTS) else "internal")
                retests.append(item)

    fixed = sum(1 for r in retests if r["fixed"])
    still = [r for r in retests if not r["fixed"]]
    answer = (f"self-development cycle: probes {healthy}/{len(probes)} healthy"
              + (f" (failing: {', '.join(s for s, v in probes.items() if not v['ok'])})" if healthy < len(probes) else "")
              + f"; {len(real_specs)} specializations backed by real handlers; ")
    if retests:
        answer += (f"retested {len(retests)} remembered failure(s): {fixed} fixed and retired ({retired} memories), "
                   f"{len(still)} still failing"
                   + (": " + "; ".join(f"{r['specialization']}@{r['stage']} [{r['cause']}] {r['reason'][:80]}"
                                       for r in still) if still else ""))
    else:
        answer += "no remembered failures to retest"
    return {"ok": healthy == len(probes), "answer": answer,
            **({} if healthy == len(probes) else {"error": answer}),
            "tool_output": {"real_specializations": real_specs, "probes": probes,
                            "retests": retests, "retired_memories": retired}}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "code_generator": _code_generator,
    "surgeon": _surgeon,
    "test_runner": _test_runner,
    "validator": _validator,
    "analyst": _analyst,
    "data_processor": _data_processor,
    "db_manager": _db_manager,
    "network_client": _network_client,
    "api_integrator": _api_integrator,
    "researcher": _researcher,
    "architect": _architect,
    "strategist": _strategist,
    "reviewer": _reviewer,
    "monitor": _monitor,
    "educator": _educator,
    "communicator": _communicator,
    "designer": _designer,
    "builder": _builder,
    "modifier": _modifier,
    "self_developer": _self_developer,
}


async def dispatch(specialization: str, work: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run the real handler for a specialization, or return None if unmapped."""
    fn = _HANDLERS.get(specialization)
    if fn is None:
        return None
    try:
        return await fn(work or {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def coverage() -> Dict[str, Any]:
    return {"real_specializations": sorted(_HANDLERS.keys()),
            "count": len(_HANDLERS)}
