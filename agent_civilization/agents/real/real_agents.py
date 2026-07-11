#!/usr/bin/env python3
"""
Real specialist agents for the AGOS Agent Civilization.

20 specializations, all with real LLM reasoning + real tools where meaningful.

Real tools per specialization:
  researcher      -> live web search (DuckDuckGo)
  data_processor  -> real statistical computation
  test_runner     -> really executes Python code in a sandboxed subprocess
  validator       -> real JSON/YAML validation
  monitor         -> real system metrics (/proc)
  code_generator  -> real AST syntax check of generated code
  reviewer        -> real AST-based static analysis (complexity, issues)
  surgeon         -> real AST diagnosis of buggy code
  modifier        -> real AST syntax check of modified code
  api_integrator  -> real HTTP probe (when a URL is in prompt/data)
  network_client  -> real HTTP probe + DNS lookup
  db_manager      -> real SQLite execution of provided SQL
  analyst         -> real statistics extraction when numeric data provided
  architect       -> live web search grounding for tech choices
  builder         -> real AST check on scaffold code
  strategist      -> live web search grounding
  self_developer  -> real system metrics context
"""

import asyncio
import ast
import json
import logging
import os
import re
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from agent_civilization.core.llm import get_llm

logger = logging.getLogger("real_agents")

SPECIALIZATIONS: Dict[str, str] = {
    "analyst": "You are a senior data/business analyst agent in the AGOS civilization. Analyze the input rigorously: identify patterns, trends, outliers, risks and opportunities. Always return concrete findings with confidence levels.",
    "api_integrator": "You are an API integration expert agent. Given an API or integration task, produce concrete integration plans, endpoint mappings, auth flows and working example code (with error handling).",
    "architect": "You are a software architecture agent. Design robust, scalable architectures. Return components, data flows, technology choices and trade-off analysis. Prefer simple, proven designs.",
    "builder": "You are a builder agent. Turn specifications into concrete build plans and working code scaffolds. Be practical and complete: file structure, commands, configs.",
    "code_generator": "You are an expert code-generation agent. Write clean, complete, working code for the requested task. Include imports and brief usage notes. Never return pseudocode when real code is possible.",
    "communicator": "You are a communication agent. Draft clear messages, reports, announcements and translations. Adapt tone to the audience. Support Arabic and English fluently.",
    "data_processor": "You are a data-processing agent. Clean, transform, aggregate and summarize data. When raw data is provided, compute real statistics and describe the transformation steps precisely.",
    "db_manager": "You are a database expert agent. Design schemas, write optimized SQL, plan migrations and indexing strategies. Always consider integrity, performance and security (least privilege).",
    "designer": "You are a product/UI design agent. Produce concrete design specs: layout structure, color tokens, typography, spacing, component states and accessibility notes.",
    "educator": "You are an educator agent. Explain any topic clearly with examples, analogies and step-by-step breakdowns. Adapt depth to the learner's level. Support Arabic and English.",
    "modifier": "You are a code-modification agent. Given existing code and a change request, return the precise modified code with an explanation of each change and its risk.",
    "monitor": "You are a monitoring agent. Assess system health from metrics and logs, detect anomalies, and recommend concrete remediation steps with priorities.",
    "network_client": "You are a networking agent. Handle HTTP/API request design, debugging network issues, protocols, and connectivity plans. Provide exact requests and expected responses.",
    "researcher": "You are a research agent with live web search results provided in context. Synthesize accurate, sourced answers. Distinguish facts from inference. Cite the sources you used.",
    "reviewer": "You are a code/content review agent. Review rigorously: correctness, security, performance, readability. Return a structured review with severity levels and concrete fixes.",
    "self_developer": "You are a self-development agent for the civilization. Propose concrete improvements to agents, prompts, and workflows, with measurable success criteria.",
    "strategist": "You are a strategy agent. Build actionable strategies: goals, phases, resources, risks, KPIs. Be decisive and concrete, not generic.",
    "surgeon": "You are a precision-fix agent ('surgeon'). Diagnose the exact root cause of a bug or failure and return the minimal precise fix, with verification steps.",
    "test_runner": "You are a testing agent. Design and (when code is provided) really execute tests. Return test plans, cases, edge cases and actual execution results.",
    "validator": "You are a validation agent. Verify data, configs and outputs against rules/schemas. Return pass/fail per rule with exact violation details.",
}

SPECIALIZATION_ICONS: Dict[str, str] = {
    "analyst": "chart-line", "api_integrator": "plug", "architect": "building",
    "builder": "hammer", "code_generator": "code", "communicator": "comments",
    "data_processor": "calculator", "db_manager": "database", "designer": "palette",
    "educator": "graduation-cap", "modifier": "edit", "monitor": "monitor-heart",
    "network_client": "network-wired", "researcher": "search", "reviewer": "check-double",
    "self_developer": "seedling", "strategist": "chess", "surgeon": "syringe",
    "test_runner": "vial", "validator": "shield-check",
}

SPECIALIZATION_TOOLS: Dict[str, str] = {
    "researcher": "web_search",
    "data_processor": "compute_statistics",
    "test_runner": "run_python_code",
    "validator": "validate_json",
    "monitor": "system_metrics",
    "code_generator": "ast_syntax_check",
    "reviewer": "ast_static_analysis",
    "surgeon": "ast_diagnose",
    "modifier": "ast_syntax_check",
    "api_integrator": "http_probe",
    "network_client": "http_probe_and_dns",
    "db_manager": "sqlite_exec",
    "analyst": "compute_statistics",
    "architect": "web_search",
    "builder": "ast_syntax_check",
    "strategist": "web_search",
    "self_developer": "system_metrics",
}

URL_RE = re.compile(r"https?://[^\s'\"<>]+")
CODE_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.DOTALL)


async def web_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Live search via DuckDuckGo (no key needed)."""
    results: List[Dict[str, str]] = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            ) as r:
                data = json.loads(await r.text())
        if data.get("AbstractText"):
            results.append({"title": data.get("Heading", ""), "snippet": data["AbstractText"], "url": data.get("AbstractURL", "")})
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({"title": topic.get("Text", "")[:80], "snippet": topic.get("Text", ""), "url": topic.get("FirstURL", "")})
    except Exception as e:
        logger.warning("web_search failed: %s", e)
    return results[:max_results]


def compute_statistics(numbers: List[float]) -> Dict[str, Any]:
    if not numbers:
        return {"error": "no numeric data"}
    out: Dict[str, Any] = {
        "count": len(numbers),
        "sum": sum(numbers),
        "mean": statistics.fmean(numbers),
        "min": min(numbers),
        "max": max(numbers),
    }
    if len(numbers) > 1:
        out["stdev"] = statistics.stdev(numbers)
        out["median"] = statistics.median(numbers)
    return out


def run_python_code(code: str, timeout: int = 10) -> Dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "passed": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s", "passed": False}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def validate_json(payload: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(payload)
        return {"valid": True, "type": type(parsed).__name__, "keys": list(parsed.keys()) if isinstance(parsed, dict) else None}
    except json.JSONDecodeError as e:
        return {"valid": False, "error": str(e), "line": e.lineno, "column": e.colno}


def system_metrics() -> Dict[str, Any]:
    metrics: Dict[str, Any] = {"timestamp": time.time()}
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            metrics["load_1m"], metrics["load_5m"], metrics["load_15m"] = map(float, parts[:3])
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                if ":" in line:
                    key = line.split(":")[0].strip()
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        mem[key] = int(parts[1])
        metrics["mem_total_mb"] = round(mem.get("MemTotal", 0) / 1024)
        metrics["mem_available_mb"] = round(mem.get("MemAvailable", 0) / 1024)
    except Exception as e:
        metrics["error"] = str(e)
    return metrics


def ast_syntax_check(code: str) -> Dict[str, Any]:
    """Real Python AST parse."""
    try:
        tree = ast.parse(code)
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        imports: List[str] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports.extend(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.append(n.module)
        return {"valid": True, "functions": funcs, "classes": classes, "imports": imports, "lines": len(code.splitlines())}
    except SyntaxError as e:
        return {"valid": False, "error": str(e), "line": e.lineno, "offset": e.offset}


def ast_static_analysis(code: str) -> Dict[str, Any]:
    """Real static analysis: complexity, long functions, bare excepts, TODOs."""
    base = ast_syntax_check(code)
    if not base.get("valid"):
        return base
    tree = ast.parse(code)
    issues: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            body_lines = (node.end_lineno or node.lineno) - node.lineno
            if body_lines > 50:
                issues.append({"severity": "warn", "line": node.lineno, "msg": f"function '{node.name}' is long ({body_lines} lines)"})
            args = len(node.args.args)
            if args > 6:
                issues.append({"severity": "warn", "line": node.lineno, "msg": f"function '{node.name}' has many args ({args})"})
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({"severity": "high", "line": node.lineno, "msg": "bare except swallows all errors"})
        if isinstance(node, ast.Assert):
            issues.append({"severity": "info", "line": node.lineno, "msg": "assert stripped under python -O"})
    todos = [i + 1 for i, ln in enumerate(code.splitlines()) if "TODO" in ln or "FIXME" in ln]
    if todos:
        issues.append({"severity": "info", "msg": f"TODO/FIXME markers on lines {todos}"})
    base["issues"] = issues
    base["issue_count"] = len(issues)
    return base


def ast_diagnose(code: str) -> Dict[str, Any]:
    """Precise diagnosis for the surgeon: syntax + likely bugs."""
    res = ast_static_analysis(code)
    if not res.get("valid"):
        return res
    tree = ast.parse(code)
    findings: List[Dict[str, Any]] = res.get("issues", [])
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if isinstance(op, (ast.Is, ast.IsNot)):
                    for c in node.comparators:
                        if isinstance(c, ast.Constant) and isinstance(c.value, (int, str, float)):
                            findings.append({"severity": "high", "line": node.lineno, "msg": "using 'is' with a literal — use '==' instead"})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "eval":
            findings.append({"severity": "high", "line": node.lineno, "msg": "eval() is dangerous"})
    res["findings"] = findings
    return res


async def http_probe(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Real HTTP HEAD/GET probe to a URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": f"unsupported scheme: {parsed.scheme}"}
    started = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url) as r:
                text = await r.text()
                return {
                    "url": url,
                    "status": r.status,
                    "content_type": r.headers.get("Content-Type", ""),
                    "elapsed_ms": round((time.time() - started) * 1000),
                    "bytes": len(text),
                    "snippet": text[:400],
                }
    except Exception as e:
        return {"url": url, "error": str(e), "elapsed_ms": round((time.time() - started) * 1000)}


def dns_lookup(host: str) -> Dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return {"host": host, "addresses": addrs}
    except Exception as e:
        return {"host": host, "error": str(e)}


def sqlite_exec(sql: str) -> Dict[str, Any]:
    """Execute SQL in an ephemeral in-memory SQLite database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    out: Dict[str, Any] = {"statements": []}
    try:
        cur = conn.cursor()
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            try:
                cur.execute(stmt)
                rows = cur.fetchall() if cur.description else []
                out["statements"].append({
                    "sql": stmt[:200],
                    "ok": True,
                    "rowcount": cur.rowcount,
                    "rows": [dict(r) for r in rows[:20]],
                })
            except Exception as e:
                out["statements"].append({"sql": stmt[:200], "ok": False, "error": str(e)})
        conn.commit()
    finally:
        conn.close()
    out["ok"] = all(s.get("ok") for s in out["statements"])
    return out


def _extract_numbers(data: Any) -> List[float]:
    nums: List[float] = []
    if isinstance(data, (int, float)):
        nums.append(float(data))
    elif isinstance(data, (list, tuple)):
        for v in data:
            nums.extend(_extract_numbers(v))
    elif isinstance(data, dict):
        for v in data.values():
            nums.extend(_extract_numbers(v))
    elif isinstance(data, str):
        try:
            nums.append(float(data))
        except ValueError:
            pass
    return nums


def _extract_code(prompt: str, data: Any) -> Optional[str]:
    if isinstance(data, str) and data.strip():
        return data
    m = CODE_FENCE_RE.search(prompt or "")
    return m.group(1) if m else None


def _extract_sql(prompt: str, data: Any) -> Optional[str]:
    if isinstance(data, str) and data.strip():
        return data
    m = SQL_FENCE_RE.search(prompt or "")
    return m.group(1) if m else None


def _extract_url(prompt: str, data: Any) -> Optional[str]:
    if isinstance(data, str) and data.strip().startswith(("http://", "https://")):
        return data.strip()
    m = URL_RE.search(prompt or "")
    return m.group(0) if m else None


class RealSpecialistAgent:
    """A real working agent: LLM brain + real tools, per specialization."""

    def __init__(self, agent_id: int, specialization: str):
        assert specialization in SPECIALIZATIONS, f"unknown specialization {specialization}"
        self.agent_id = agent_id
        self.specialization = specialization
        self.name = f"{specialization}_{agent_id:04d}"
        self.status = "idle"
        self.stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}
        self.created_at = time.time()

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        prompt = str(task.get("prompt") or task.get("description") or "").strip()
        data = task.get("data")
        started = time.time()
        self.status = "working"
        try:
            result = await self._run(prompt, data)
            self.stats["tasks_completed"] += 1
            ok = True
        except Exception as e:
            logger.exception("agent %s failed", self.name)
            result = {"error": str(e)}
            self.stats["tasks_failed"] += 1
            ok = False
        elapsed = time.time() - started
        self.stats["total_time_s"] += elapsed
        self.status = "idle"
        return {
            "ok": ok,
            "agent": self.name,
            "specialization": self.specialization,
            "elapsed_s": round(elapsed, 2),
            "result": result,
        }

    async def _tool_phase(self, prompt: str, data: Any) -> Optional[Dict[str, Any]]:
        spec = self.specialization
        if spec == "researcher" and prompt:
            sources = await web_search(prompt)
            return {"tool": "web_search", "sources": sources}
        if spec in ("architect", "strategist") and prompt:
            sources = await web_search(prompt, max_results=4)
            return {"tool": "web_search", "sources": sources}
        if spec in ("data_processor", "analyst") and data is not None:
            nums = _extract_numbers(data)
            stats = compute_statistics(nums) if nums else {"note": "no numeric values found"}
            return {"tool": "compute_statistics", "statistics": stats}
        if spec == "test_runner":
            code = _extract_code(prompt, data)
            if code:
                execution = await asyncio.to_thread(run_python_code, code)
                return {"tool": "run_python_code", "execution": execution}
        if spec == "validator" and isinstance(data, str) and data.strip():
            return {"tool": "validate_json", "validation": validate_json(data)}
        if spec in ("monitor", "self_developer"):
            return {"tool": "system_metrics", "metrics": system_metrics()}
        if spec in ("code_generator", "builder"):
            code = _extract_code(prompt, data)
            if code:
                return {"tool": "ast_syntax_check", "check": ast_syntax_check(code)}
        if spec == "reviewer":
            code = _extract_code(prompt, data)
            if code:
                return {"tool": "ast_static_analysis", "analysis": ast_static_analysis(code)}
        if spec == "surgeon":
            code = _extract_code(prompt, data)
            if code:
                return {"tool": "ast_diagnose", "diagnosis": ast_diagnose(code)}
        if spec == "modifier":
            code = _extract_code(prompt, data)
            if code:
                return {"tool": "ast_syntax_check", "check": ast_syntax_check(code)}
        if spec == "api_integrator":
            url = _extract_url(prompt, data)
            if url:
                return {"tool": "http_probe", "probe": await http_probe(url)}
        if spec == "network_client":
            url = _extract_url(prompt, data)
            if url:
                probe = await http_probe(url)
                host = urlparse(url).hostname or ""
                return {"tool": "http_probe_and_dns", "probe": probe, "dns": dns_lookup(host) if host else None}
        if spec == "db_manager":
            sql = _extract_sql(prompt, data)
            if sql and re.search(r"\b(select|insert|update|delete|create|drop|alter|with)\b", sql, re.I):
                return {"tool": "sqlite_exec", "execution": await asyncio.to_thread(sqlite_exec, sql)}
        return None

    async def _run(self, prompt: str, data: Any) -> Dict[str, Any]:
        llm = get_llm()
        system = SPECIALIZATIONS[self.specialization]
        tool_output = await self._tool_phase(prompt, data)

        if tool_output:
            prompt = f"{prompt}\n\nReal tool output ({tool_output.get('tool')}):\n{json.dumps({k: v for k, v in tool_output.items() if k != 'tool'}, default=str)[:4000]}"

        if not prompt:
            return {"tool_output": tool_output, "answer": None, "note": "empty prompt"}

        llm_result = await llm.complete(system, prompt)
        out: Dict[str, Any] = {"answer": llm_result["content"] if llm_result["ok"] else None}
        if tool_output:
            out["tool_output"] = tool_output
        if not llm_result["ok"]:
            out["llm_error"] = llm_result["error"]
            if tool_output is None:
                raise RuntimeError(llm_result["error"])
        out["model"] = llm_result["model"]
        return out

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.agent_id,
            "name": self.name,
            "specialization": self.specialization,
            "status": self.status,
            "stats": dict(self.stats),
        }


def create_civilization(agents_per_spec: int = None) -> Dict[str, List[RealSpecialistAgent]]:
    n = agents_per_spec or int(os.environ.get("AGENTS_PER_SPEC", "3"))
    civilization: Dict[str, List[RealSpecialistAgent]] = {}
    agent_id = 0
    for spec in SPECIALIZATIONS:
        civilization[spec] = []
        for _ in range(n):
            civilization[spec].append(RealSpecialistAgent(agent_id, spec))
            agent_id += 1
    logger.info("Civilization created: %d specializations x %d agents = %d real agents",
                len(SPECIALIZATIONS), n, agent_id)
    return civilization
