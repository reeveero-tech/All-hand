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
import re
import socket
import sqlite3
import statistics
import time
import tokenize
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from agent_civilization.core import safety
from agent_civilization.core.agents import craft


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _extract_text(work: Dict[str, Any]) -> str:
    for key in ("prompt", "text", "query", "goal", "message", "input"):
        v = work.get(key)
        if isinstance(v, str) and v.strip():
            return v
    data = work.get("data")
    if isinstance(data, str):
        return data
    return ""


def _extract_code(work: Dict[str, Any]) -> str:
    data = work.get("data")
    if isinstance(data, str) and data.strip():
        return data
    if isinstance(data, dict):
        for k in ("code", "source"):
            v = data.get(k)
            if isinstance(v, str):
                return v
    text = _extract_text(work)
    m = re.search(r"```(?:python|py)?\n(.+?)```", text, re.S)
    if m:
        return m.group(1)
    # Heuristic: only treat free text as code if it looks like Python
    # (a prompt sentence is not code — return empty so callers see
    # "no code to run" instead of a confusing SyntaxError).
    if re.search(r"^(?:\s*)(?:def |class |import |from |print\(|[\w.]+\s*=)", text, re.M):
        return text
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
# real implementations, one per specialization
# ---------------------------------------------------------------------------

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
    m = re.search(r"```(?:python|py)?\n(.+?)```", out.get("content", ""), re.S)
    src = m.group(1) if m else out.get("content", "")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "error": f"LLM output is not valid Python (line {e.lineno}: {e.msg}); discarded"}
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
    issues = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"ok": True, "answer": f"syntax break at line {e.lineno}: {e.msg}",
                "tool_output": {"fatal": True}}
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(f"bare except at line {node.lineno}")
        if isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None
            for op, c in zip(node.ops, node.comparators)
        ):
            issues.append(f"== None instead of `is None` at line {node.lineno}")
    return {"ok": True, "answer": f"{len(issues)} issue(s)", "tool_output": {"issues": issues}}


async def _test_runner(work: Dict[str, Any]) -> Dict[str, Any]:
    """Run submitted Python in the sandbox — only if the policy allows it."""
    code = _extract_code(work)
    if not code.strip():
        return {"ok": False, "error": "no code to run"}
    allowed, reason = safety.code_execution_policy()
    if not allowed:
        return {"ok": False, "error": reason, "tool_output": {"executed": False}}
    r = await _to_thread(safety.run_sandboxed_python, code)
    if r["timed_out"]:
        return {"ok": False, "error": f"timeout after {safety.SANDBOX_WALL_TIMEOUT_S}s",
                "tool_output": r}
    rc = r["returncode"]
    note = f"exit={rc}" if rc >= 0 else f"killed by signal {-rc} (sandbox limit)"
    return {"ok": rc == 0, "answer": note,
            "tool_output": {"stdout": r["stdout"], "stderr": r["stderr"],
                            "elapsed_s": r["elapsed_s"], "sandbox": True}}


async def _validator(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
    data = work.get("data")
    payload = data if data is not None else text
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return {"ok": True, "answer": "valid JSON",
                    "tool_output": {"type": type(parsed).__name__,
                                    "size": len(parsed) if hasattr(parsed, "__len__") else 1}}
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"invalid JSON: {e.msg} at pos {e.pos}"}
    return {"ok": True, "answer": "valid python object",
            "tool_output": {"type": type(payload).__name__}}


async def _analyst(work: Dict[str, Any]) -> Dict[str, Any]:
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
    return {"ok": True, "answer": f"n={len(nums)} mean={mu:.3f} sd={sd:.3f} trend={trend}",
            "tool_output": {"n": len(nums), "mean": mu, "stdev": sd,
                             "min": min(nums), "max": max(nums),
                             "outliers": outliers, "trend": trend}}


async def _data_processor(work: Dict[str, Any]) -> Dict[str, Any]:
    nums = _extract_numbers(work)
    if not nums:
        # R-104 (D-047): text-only input — the deterministic aggregation has nothing
        # to do; delegate to the LLM-backed DataWorkerAgent path (honest failure when
        # the LLM key is missing — the exact OpenRouter error is propagated).
        text = _extract_text(work)
        if not text.strip():
            return {"ok": False, "error": "no numeric data and no text to process"}
        from agent_civilization.agents.executors.data_worker import process_text
        out = await process_text(text)
        if not out.get("ok"):
            return {"ok": False,
                    "error": f"llm-backed data processing unavailable: {out.get('error')}",
                    "model": out.get("model")}
        return {"ok": True, "answer": out.get("answer", ""),
                "tool_output": {"source": "llm", "model": out.get("model")}}
    sorted_ns = sorted(nums)
    q = lambda p: sorted_ns[min(len(sorted_ns) - 1, int(p * (len(sorted_ns) - 1)))]
    return {"ok": True, "answer": f"processed {len(nums)} samples",
            "tool_output": {"n": len(nums), "sum": sum(nums),
                             "median": statistics.median(nums),
                             "p25": q(0.25), "p75": q(0.75),
                             "variance": statistics.pvariance(nums) if len(nums) > 1 else 0}}


async def _db_manager(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
    sql_match = re.search(r"(SELECT|CREATE|INSERT|UPDATE|DELETE|WITH)\b.+", text, re.I | re.S)
    if not sql_match:
        return {"ok": False, "error": "no SQL statement detected"}
    sql = sql_match.group(0)
    statements = [st for st in sql.split(";") if st.strip()]
    try:
        rows, cols = await _to_thread(safety.run_sqlite_statements, statements)
        return {"ok": True, "answer": f"{len(rows)} row(s)",
                "tool_output": {"columns": cols, "rows": rows}}
    except sqlite3.Error as e:
        return {"ok": False, "error": f"sqlite: {e}"}


async def _network_client(work: Dict[str, Any]) -> Dict[str, Any]:
    url = _extract_url(work)
    if not url:
        # try host-only probe
        text = _extract_text(work).strip()
        host_match = re.search(r"([a-zA-Z0-9][a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})", text)
        if not host_match:
            return {"ok": False, "error": "no url/host detected"}
        host = host_match.group(1)
        def probe():
            t0 = time.time()
            addrs = safety.check_host(host)
            return {"host": host, "ip": addrs[0], "addresses": addrs,
                    "resolve_ms": round((time.time() - t0) * 1000, 2)}
        try:
            return {"ok": True, "answer": f"resolved {host}",
                    "tool_output": await _to_thread(probe)}
        except safety.UnsafeURLError as e:
            return {"ok": False, "error": f"blocked: {e}"}
        except socket.gaierror as e:
            return {"ok": False, "error": f"dns: {e}"}

    def head():
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "AGOS-Civilization/1.0"})
        t0 = time.time()
        with safety.safe_urlopen(req, timeout=6) as resp:
            return {"status": resp.status, "url": resp.geturl(),
                    "content_type": resp.headers.get("content-type", ""),
                    "content_length": resp.headers.get("content-length"),
                    "server": resp.headers.get("server", ""),
                    "latency_ms": round((time.time() - t0) * 1000, 2)}
    try:
        return {"ok": True, "answer": f"HEAD {url}",
                "tool_output": await _to_thread(head)}
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
        return {"ok": True, "answer": f"GET {url}", "tool_output": await _to_thread(get)}
    except safety.UnsafeURLError as e:
        return {"ok": False, "error": f"blocked: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"http: {e}"}


async def _researcher(work: Dict[str, Any]) -> Dict[str, Any]:
    q = _extract_text(work).strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    # DuckDuckGo Instant Answer API — no key needed.
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "no_html": "1", "no_redirect": "1"})
    def fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "AGOS-Research/1.0"})
        with safety.safe_urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    try:
        j = await _to_thread(fetch)
        abstract = j.get("AbstractText") or j.get("Heading") or ""
        related = [r.get("Text", "") for r in j.get("RelatedTopics", [])[:5]
                   if isinstance(r, dict) and r.get("Text")]
        return {"ok": True,
                "answer": abstract or "no direct answer; see related",
                "tool_output": {"abstract": abstract,
                                 "source": j.get("AbstractURL", ""),
                                 "related": related}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"search: {e}"}


async def _architect(work: Dict[str, Any]) -> Dict[str, Any]:
    """Component graph derived from the request (R-102): requested + implied
    components, dependencies and a topological build order."""
    return craft.design_architecture(_extract_text(work), work.get("data"))


async def _strategist(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
    data = work.get("data")
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


async def _reviewer(work: Dict[str, Any]) -> Dict[str, Any]:
    code = _extract_code(work)
    if not code.strip():
        text = _extract_text(work)
        wc = len(text.split())
        return {"ok": True, "answer": f"text review: {wc} words",
                "tool_output": {"words": wc, "chars": len(text)}}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
        comments = sum(1 for t in tokens if t.type == tokenize.COMMENT)
        loc = code.count("\n") + 1
        return {"ok": True,
                "answer": f"reviewed {loc} loc, {comments} comments",
                "tool_output": {"loc": loc, "comments": comments,
                                 "comment_ratio": round(comments / max(loc, 1), 3)}}
    except tokenize.TokenizeError as e:
        return {"ok": False, "error": f"tokenize: {e}"}


async def _monitor(work: Dict[str, Any]) -> Dict[str, Any]:
    def snapshot():
        import os, resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return {"pid": os.getpid(),
                "user_cpu_s": round(ru.ru_utime, 3),
                "sys_cpu_s": round(ru.ru_stime, 3),
                "max_rss_kb": ru.ru_maxrss,
                "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None}
    try:
        snap = await _to_thread(snapshot)
        return {"ok": True, "answer": "system snapshot captured", "tool_output": snap}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def _educator(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
    sentences = [s.strip() for s in re.split(r"[.!؟\n]", text) if s.strip()]
    keypoints = sentences[:5]
    return {"ok": True,
            "answer": f"first {len(keypoints)} sentence(s) as key points",
            "tool_output": {"key_points": keypoints,
                             "reading_time_min": round(len(text.split()) / 200, 2)}}


async def _communicator(work: Dict[str, Any]) -> Dict[str, Any]:
    text = _extract_text(work)
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
    return {"ok": True, "answer": f"tone={label}",
            "tool_output": {"positive": p, "negative": n, "score": round(score, 3),
                             "label": label}}


async def _designer(work: Dict[str, Any]) -> Dict[str, Any]:
    """Palette from the colour (hex or name) and mood in the request, every
    foreground checked against WCAG contrast targets (R-102)."""
    return craft.design_palette(_extract_text(work), work.get("data"))


async def _builder(work: Dict[str, Any]) -> Dict[str, Any]:
    """Scaffold real files, verify each (compile/json/toml), return them (R-102).
    Before: answered "N build action(s) queued" with no queue."""
    return await _to_thread(craft.scaffold, _extract_text(work), work.get("data"))


async def _modifier(work: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the requested edit (rename / remove unused imports) and return the
    new code + diff, AST-verified (R-102). Before: whitespace only, result discarded."""
    code = _extract_code(work)
    if not code.strip():
        return {"ok": False, "error": "no code to modify"}
    instruction = " ".join(str(work.get(k) or "") for k in ("prompt", "text", "query", "goal", "message"))
    return craft.modify_code(code, instruction)


async def _self_developer(work: Dict[str, Any]) -> Dict[str, Any]:
    # inspect this module's own coverage as proof of self-reflection
    real_specs = sorted(_HANDLERS.keys())
    return {"ok": True,
            "answer": f"{len(real_specs)} specializations backed by real handlers",
            "tool_output": {"real_specializations": real_specs}}


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
