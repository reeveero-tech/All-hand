#!/usr/bin/env python3
"""R-161: opt-in integration runner — storage (PostgREST/Supabase), LLM
provider (OpenAI-compatible), external tool (OpenAI-compatible external agent),
MCP — with every result classified and nothing counted that did not happen.

It REUSES the product clients and the R-160 contracts: SupabaseStorage,
LLMClient, ExternalAgentAdapter, agent_civilization.core.contracts. It is not a
parallel implementation of any of them.

Outcome per target:   PASS | FAIL | BLOCKED | NOT_RUN
Evidence class:       mock | local_integration | live_integration
  * mock              — the operator/test declared a simulated service
                        (--simulated); loopback endpoints only.
  * local_integration — a real service on loopback (127.0.0.0/8, ::1, localhost),
                        e.g. PostgreSQL+PostgREST in CI job ``storage``.
  * live_integration  — a public https endpoint. Only with R161_LIVE=1, the host
                        in R161_ALLOWED_HOSTS and a declared credential expiry.
A target that was not asked for is NOT_RUN. A target asked for whose
configuration is missing or refused by policy is BLOCKED (with the variable
NAMES or the policy rule — never values). Only a target that connected and
whose response passed its contract checks is PASS.

Exit code: 0 = at least one target ran and every requested target PASSed;
1 = any FAIL (a failed cleanup is a FAIL); 2 = no FAIL but something BLOCKED;
3 = nothing ran (only NOT_RUN). A provider failure is never turned into 0.

Configuration (environment; all optional, values never printed):
  R161_LIVE=1                     allow non-loopback (live) endpoints at all
  R161_ALLOWED_HOSTS=h1,h2        exact host allowlist for live endpoints
  R161_CREDENTIAL_EXPIRES_AT=ISO  operator's declaration of when the test
                                  credentials expire (UTC, <= 7 days ahead);
                                  an attestation recorded in the report, not
                                  proof — see docs/governance/OPERATIONS.md
  R161_TIMEOUT_S=30               per-target deadline (1..300)
  R161_STORAGE_URL / R161_STORAGE_KEY     isolated test project, service key
  R161_LLM_URL / R161_LLM_API_KEY / R161_LLM_MODEL
  R161_EXTERNAL_URL / R161_EXTERNAL_API_KEY / R161_EXTERNAL_MODEL

Usage:
  python3 scripts/r161_live_integration.py --targets storage,llm,external,mcp \\
      --report r161_report.json [--simulated]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TARGETS = ("storage", "llm", "external", "mcp")
OUTCOMES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
CLASSES = ("mock", "local_integration", "live_integration")
REPORT_VERSION = 1
MAX_EXPIRY_AHEAD = dt.timedelta(days=7)
DEFAULT_LLM_URL = "https://openrouter.ai/api/v1/chat/completions"
PROBE_SYSTEM = "You are an integration test probe. Answer with exactly one word: PONG"
PROBE_PROMPT = "Reply with the single word PONG."
MCP_REASON = ("no MCP client or transport exists in this repository (MCP appears only as catalogue "
              "metadata in agent_civilization/registry/oss_agents.py); building one is outside R-161")

# Names of variables whose VALUES are secrets (always redacted when present).
SECRET_VARS = ("R161_STORAGE_KEY", "R161_LLM_API_KEY", "R161_EXTERNAL_API_KEY",
               "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY",
               "OPENROUTER_API_KEY", "AGOS_API_KEY")

logger = logging.getLogger("r161")


# ------------------------------------------------------------------ redaction
class Redactor:
    """Removes secret values, URL credentials and bearer tokens from any text."""

    _USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
    _BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
    _JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

    def __init__(self, env: Dict[str, str]):
        vals = {env.get(k, "") for k in SECRET_VARS}
        for k, v in env.items():
            if re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", k) and k.startswith(("R161_", "SUPABASE_")):
                vals.add(v)
        # URL userinfo values too (a URL with credentials is BLOCKED, but its
        # parts must not leak through the reason either)
        for k in ("R161_STORAGE_URL", "R161_LLM_URL", "R161_EXTERNAL_URL"):
            u = env.get(k, "")
            try:
                p = urllib.parse.urlsplit(u)
                vals |= {p.username or "", p.password or ""}
            except ValueError:
                pass
        self.values = sorted((v for v in vals if v and len(v) >= 4), key=len, reverse=True)

    def text(self, s: Any) -> str:
        s = str(s)
        for v in self.values:
            s = s.replace(v, "<redacted>")
        s = self._USERINFO.sub(r"\1<redacted>@", s)
        s = self._BEARER.sub(r"\1<redacted>", s)
        return self._JWT.sub("<redacted-jwt>", s)

    def obj(self, o: Any) -> Any:
        if isinstance(o, dict):
            return {self.text(k): self.obj(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.obj(v) for v in o]
        if isinstance(o, str):
            return self.text(o)
        return o


class _RedactingFilter(logging.Filter):
    def __init__(self, red: Redactor):
        super().__init__()
        self.red = red

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.red.text(record.getMessage())
        record.args = ()
        return True


def install_log_redaction(red: Redactor) -> _RedactingFilter:
    """Every log record of every logger passes the redactor (root handlers and
    the root logger itself), including records produced inside the clients."""
    f = _RedactingFilter(red)
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)
    return f


# ------------------------------------------------------------------- results
def _result(target: str, outcome: str, reason: str, evidence_class: Optional[str] = None,
            **extra) -> Dict[str, Any]:
    assert outcome in OUTCOMES
    assert evidence_class in CLASSES + (None,)
    r = {"target": target, "outcome": outcome, "reason": reason, "evidence_class": evidence_class,
         "endpoint_host": None, "connected": False, "checks": [], "cleanup": None, "duration_s": 0.0}
    r.update(extra)
    return r


class _Checks:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    @property
    def all_ok(self) -> bool:
        return bool(self.items) and all(c["ok"] for c in self.items)

    def first_failure(self) -> str:
        for c in self.items:
            if not c["ok"]:
                return f"check failed: {c['name']}" + (f" ({c['detail']})" if c["detail"] else "")
        return ""


# ------------------------------------------------------------------- policy
def _is_loopback(host: str) -> bool:
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def preflight(target: str, url: str, env: Dict[str, str], simulated: bool,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Policy for one endpoint. Returns {"ok": True, "class": ..., "host": ...}
    or {"ok": False, "reason": ...} (BLOCKED). Never resolves or connects."""
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        _ = p.port
    except ValueError:
        return {"ok": False, "reason": f"{target}: endpoint URL is not a valid URL"}
    if p.username or p.password:
        return {"ok": False, "reason": f"{target}: credentials inside the endpoint URL are refused"}
    if p.scheme not in ("http", "https") or not host:
        return {"ok": False, "reason": f"{target}: endpoint must be an http(s) URL with a host"}
    if _is_loopback(host):
        cls = "mock" if simulated else "local_integration"
        return {"ok": True, "class": cls, "host": host}
    if simulated:
        return {"ok": False, "reason": f"{target}: --simulated accepts loopback endpoints only"}
    if _is_ip_literal(host) and not ipaddress.ip_address(host.strip("[]")).is_global:
        return {"ok": False, "reason": f"{target}: private/reserved address is neither local (loopback) nor live"}
    # ---- live
    if env.get("R161_LIVE") != "1":
        return {"ok": False, "reason": f"{target}: live integration not enabled (R161_LIVE is not 1)"}
    if p.scheme != "https":
        return {"ok": False, "reason": f"{target}: live endpoints must use https (TLS verified)"}
    allowed = {h.strip().lower() for h in env.get("R161_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host not in allowed:
        return {"ok": False, "reason": f"{target}: host is not in R161_ALLOWED_HOSTS"}
    exp = env.get("R161_CREDENTIAL_EXPIRES_AT", "")
    if not exp:
        return {"ok": False, "reason": f"{target}: R161_CREDENTIAL_EXPIRES_AT not declared (temporary credentials required)"}
    try:
        when = dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError
    except ValueError:
        return {"ok": False, "reason": f"{target}: R161_CREDENTIAL_EXPIRES_AT is not an ISO-8601 time with a zone"}
    now = now or dt.datetime.now(dt.timezone.utc)
    if when <= now:
        return {"ok": False, "reason": f"{target}: declared credential expiry is in the past"}
    if when - now > MAX_EXPIRY_AHEAD:
        return {"ok": False, "reason": f"{target}: declared credential expiry is more than 7 days ahead (not temporary)"}
    return {"ok": True, "class": "live_integration", "host": host, "credential_expires_at": when.isoformat()}


def _timeout(env: Dict[str, str]) -> float:
    try:
        v = float(env.get("R161_TIMEOUT_S", "30"))
    except ValueError:
        return 30.0
    return min(max(v, 1.0), 300.0)


def _missing(env: Dict[str, str], names) -> List[str]:
    return [n for n in names if not env.get(n)]


# ------------------------------------------------------------------- probes
async def _public_dns_check(url: str) -> Optional[str]:
    """Live endpoints must resolve to public addresses only (R-146 policy)."""
    from agent_civilization.core import pools, safety
    try:
        await pools.run("net", safety.check_url, url, False)
        return None
    except safety.UnsafeURLError as e:
        return f"endpoint refused by network policy: {e}"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        return f"endpoint check failed: {type(e).__name__}"


# R-161d: fixed reasons of supabase_store that prove an HTTP answer, and the
# cleanup's own "<METHOD|verify> <table> -> HTTP NNN" detail.
_STORE_ANSWER_RE = re.compile(r"^(HTTP \d{3}|malformed response|no row updated)$")
_CLEANUP_ANSWER_RE = re.compile(r"-> HTTP \d{3}$")


class _StorageCleanup:
    """Deletes ONLY the rows of this run: tenant_id = the run's unique
    synthetic tenant (never a shared filter). Verifies nothing is left."""

    def __init__(self, url: str, key: str, tenant: str, allow_private: bool = False):
        self.url, self.key, self.tenant = url.rstrip("/"), key, tenant
        self.allow_private = allow_private

    async def run(self, timeout_s: float) -> Dict[str, Any]:
        import aiohttp
        out = {"ran": True, "ok": False, "deleted": {}, "left": {}, "detail": ""}
        hdr = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Prefer": "return=representation"}
        flt = "tenant_id=eq." + urllib.parse.quote(self.tenant, safe="")
        try:
            from agent_civilization.agents.external_adapter import guarded_session_kwargs
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s), headers=hdr,
                                             **guarded_session_kwargs(self.allow_private)) as s:
                for table in ("events", "tasks"):
                    async with s.delete(f"{self.url}/rest/v1/{table}?{flt}") as r:
                        if r.status not in (200, 204):
                            out["detail"] = f"DELETE {table} -> HTTP {r.status}"
                            return out
                        body = await r.json(content_type=None) if r.status == 200 else []
                        out["deleted"][table] = len(body) if isinstance(body, list) else -1
                for table in ("events", "tasks"):
                    async with s.get(f"{self.url}/rest/v1/{table}?{flt}&select=tenant_id") as r:
                        rows = await r.json(content_type=None)
                        if r.status != 200 or not isinstance(rows, list):
                            out["detail"] = f"verify {table} -> HTTP {r.status}"
                            return out
                        out["left"][table] = len(rows)
        except asyncio.CancelledError:
            out["detail"] = "cleanup cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            out["detail"] = f"cleanup error: {type(e).__name__}"
            return out
        if any(out["left"].values()):
            out["detail"] = "rows of this run are still present after cleanup"
            return out
        out["ok"] = True
        return out


async def probe_storage(env: Dict[str, str], pf: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    from agent_civilization.core import contracts as C
    from agent_civilization.storage import supabase_store as st
    url, key = env["R161_STORAGE_URL"].rstrip("/"), env["R161_STORAGE_KEY"]
    run = secrets.token_hex(6)
    tenant, other = f"r161_{run}", f"r161_other_{run}"
    task_id = run                                     # 12 hex chars, like server task ids
    checks = _Checks()
    saved = (st.SUPABASE_URL, st.SUPABASE_KEY, st.SUPABASE_KEY_SOURCE)
    st.SUPABASE_URL, st.SUPABASE_KEY, st.SUPABASE_KEY_SOURCE = url, key, "R161_STORAGE_KEY"
    # R-161d (G-161-7): observe, for this probe only, whether the store gave
    # any HTTP answer. supabase_store records every operation outcome through
    # its module-level _record (fixed reasons only: "HTTP NNN", "malformed
    # response", "no row updated", "exception: <type>"); a success or one of
    # the first three is an answer, an exception is not. Only a boolean is
    # kept — never a reason text or a body. Restored in the finally below.
    answered_http = {"seen": False}
    original_record = st._record

    def _observe(op, ok, reason=None):
        if ok or _STORE_ANSWER_RE.match(reason or ""):
            answered_http["seen"] = True
        return original_record(op, ok, reason)
    st._record = _observe
    allow_private = pf["class"] != "live_integration"
    store = st.SupabaseStorage(guarded=True, allow_private=allow_private)   # G-161-3
    cleanup = _StorageCleanup(url, key, tenant, allow_private)
    res = _result("storage", "FAIL", "", pf["class"], endpoint_host=pf["host"],
                  synthetic={"tenant": tenant, "task_id": task_id})

    async def steps():
        ok = await store.insert_task(task_id, "analyst", "R-161 synthetic probe (no user data)",
                                     status="queued", tenant_id=tenant)
        checks.add("insert_task accepted", ok)
        checks.add("update_task updated the row", await store.update_task(
            task_id, "completed", agent_name=f"r161_agent_{run}", result={"ok": True, "answer": "r161"}, elapsed_s=0.001))
        row = await store.get_task(task_id, scope=tenant)
        checks.add("get_task returns the row", row is not None)
        if row is not None:
            try:
                C.validate("task_row", row)
                checks.add("row matches task_row v1", True)
            except Exception as e:  # noqa: BLE001
                checks.add("row matches task_row v1", False, type(e).__name__)
            checks.add("row values round-trip", row.get("status") == "completed"
                       and row.get("tenant_id") == tenant and isinstance(row.get("result"), dict))
        rows = await store.get_tasks(limit=5, scope=tenant)
        checks.add("get_tasks scoped to the run tenant", [r.get("task_id") for r in rows] == [task_id])
        checks.add("other tenant cannot read the row", await store.get_task(task_id, scope=other) is None)
        checks.add("insert_event accepted", await store.insert_event("r161_probe", {"run": run}, tenant_id=tenant))
        ev = await store.get_events(limit=5, scope=tenant)
        checks.add("get_events returns the event", len(ev) == 1 and ev[0].get("event_type") == "r161_probe")

    try:
        await asyncio.wait_for(steps(), timeout_s)
        res["outcome"] = "PASS" if checks.all_ok else "FAIL"
        res["reason"] = "all checks passed" if checks.all_ok else checks.first_failure()
    except asyncio.TimeoutError:
        res["reason"] = f"timeout after {timeout_s:g}s"
    except asyncio.CancelledError:
        _note_cancel(res)
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"probe error: {type(e).__name__}"
    finally:
        res["checks"] = checks.items
        try:
            await store.close()
        except Exception:  # noqa: BLE001
            pass
        st.SUPABASE_URL, st.SUPABASE_KEY, st.SUPABASE_KEY_SOURCE = saved
        st._record = original_record
        # cleanup always runs — also after a timeout, an error or a cancel —
        # and its own failure is a FAIL of the target (never hidden)
        try:
            res["cleanup"] = await asyncio.shield(cleanup.run(min(timeout_s, 30.0)))
        except asyncio.CancelledError:
            res["cleanup"] = {"ran": True, "ok": False, "detail": "cleanup cancelled"}
        if not res["cleanup"]["ok"]:
            res["outcome"] = "FAIL"
            res["reason"] = (res["reason"] + "; " if res["reason"] and res["reason"] != "all checks passed" else "") \
                + "cleanup failed: " + res["cleanup"]["detail"]
        # connected = the store really answered over HTTP — the definition the
        # llm/external targets use (R-161d, G-161-7): a status of any kind, a
        # malformed answer or a confirmed write/read during the steps, or an
        # HTTP status answered to the cleanup. A refused connection, a DNS
        # failure or a timeout with no answer is not connected. ("other
        # tenant cannot read" is true without any server, so it never counts.)
        answered = {"insert_task accepted", "update_task updated the row", "get_task returns the row",
                    "insert_event accepted", "get_events returns the event"}
        cl = res["cleanup"] or {}
        if answered_http["seen"] or any(c["ok"] for c in checks.items if c["name"] in answered) \
                or cl.get("ok") or _CLEANUP_ANSWER_RE.search(str(cl.get("detail") or "")):
            res["connected"] = True
    return res


def _note_cancel(res: Dict[str, Any]) -> None:
    """A cancelled probe (SIGINT/SIGTERM, CI cancel) is a FAIL that still runs
    its cleanup and is still written to the report; the runner stops after it."""
    res["reason"], res["cancelled"] = "cancelled", True
    task = asyncio.current_task()
    if task is not None and hasattr(task, "uncancel"):
        task.uncancel()          # let the cleanup's own timeouts work normally


def _llm_reason(r: Dict[str, Any]) -> str:
    err = str(r.get("error") or "")
    m = re.match(r"LLM HTTP (\d{3})", err)
    if m:
        return f"provider answered HTTP {m.group(1)}"
    if err in ("LLM empty completion", "LLM malformed completion"):
        return err.replace("LLM ", "provider: ")
    if err.startswith("LLM request failed") or err:
        return "provider request failed"
    return "provider: unknown failure"


async def probe_llm(env: Dict[str, str], pf: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    from agent_civilization.core import contracts as C
    from agent_civilization.core.llm import LLMClient
    checks = _Checks()
    res = _result("llm", "FAIL", "", pf["class"], endpoint_host=pf["host"])
    client = LLMClient(api_key=env["R161_LLM_API_KEY"], model=env.get("R161_LLM_MODEL") or None,
                       url=env.get("R161_LLM_URL") or DEFAULT_LLM_URL,
                       guarded=True, allow_private=pf["class"] != "live_integration")   # G-161-3
    try:
        if client.provider != "openrouter":                 # never the public keyless path
            res["reason"] = "client did not select the keyed provider"
            return res
        r = await asyncio.wait_for(client.complete(PROBE_SYSTEM, PROBE_PROMPT, max_tokens=16), timeout_s)
        try:
            C.validate("llm_result", r)
            checks.add("result matches llm_result v1", True)
        except Exception as e:  # noqa: BLE001
            checks.add("result matches llm_result v1", False, type(e).__name__)
        res["connected"] = bool(r.get("ok")) or bool(re.match(r"LLM HTTP \d{3}", str(r.get("error") or ""))) \
            or str(r.get("error") or "").startswith(("LLM empty", "LLM malformed"))
        if not r.get("ok"):
            checks.add("provider answered ok", False, _llm_reason(r))
        else:
            checks.add("provider answered ok", True)
            checks.add("answer is the expected word", "pong" in str(r.get("content", "")).lower(),
                       f"content length {len(str(r.get('content', '')))}")
        res["outcome"] = "PASS" if checks.all_ok else "FAIL"
        res["reason"] = "all checks passed" if checks.all_ok else checks.first_failure()
    except asyncio.TimeoutError:
        res["reason"] = f"timeout after {timeout_s:g}s"
    except asyncio.CancelledError:
        _note_cancel(res)
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"probe error: {type(e).__name__}"
    finally:
        res["checks"] = checks.items
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
        res["cleanup"] = {"ran": False, "ok": True, "detail": "nothing created"}
    return res


async def probe_external(env: Dict[str, str], pf: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter
    from agent_civilization.core import contracts as C
    checks = _Checks()
    res = _result("external", "FAIL", "", pf["class"], endpoint_host=pf["host"])
    agent = ExternalAgentAdapter(name="r161_probe", url=env["R161_EXTERNAL_URL"],
                                 model=env.get("R161_EXTERNAL_MODEL", ""),
                                 api_key=env.get("R161_EXTERNAL_API_KEY", ""),
                                 allow_private=pf["class"] != "live_integration")
    try:
        out = await asyncio.wait_for(agent.execute({"prompt": PROBE_PROMPT}), timeout_s)
        try:
            C.validate("agent_outcome", out)
            checks.add("outcome matches agent_outcome v1", True)
        except Exception as e:  # noqa: BLE001
            checks.add("outcome matches agent_outcome v1", False, type(e).__name__)
        result = out.get("result") or {}
        err = str(result.get("error") or "")
        res["connected"] = bool(out.get("ok")) or err.startswith(("HTTP ", "empty completion", "malformed completion",
                                                                  "JSONDecodeError"))
        if not out.get("ok"):
            checks.add("external agent answered ok", False,
                       err if re.fullmatch(r"(HTTP \d{3}|empty completion|malformed completion|[A-Za-z]+Error)", err)
                       else "external agent failed")
        else:
            checks.add("external agent answered ok", True)
            checks.add("answer is the expected word", "pong" in str(result.get("answer", "")).lower())
        res["outcome"] = "PASS" if checks.all_ok else "FAIL"
        res["reason"] = "all checks passed" if checks.all_ok else checks.first_failure()
    except asyncio.TimeoutError:
        res["reason"] = f"timeout after {timeout_s:g}s"
    except asyncio.CancelledError:
        _note_cancel(res)
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"probe error: {type(e).__name__}"
    finally:
        res["checks"] = checks.items
        res["cleanup"] = {"ran": False, "ok": True, "detail": "nothing created"}
    return res


PROBES: Dict[str, Callable] = {"storage": probe_storage, "llm": probe_llm, "external": probe_external}
REQUIRED = {"storage": ("R161_STORAGE_URL", "R161_STORAGE_KEY"),
            "llm": ("R161_LLM_API_KEY",),
            "external": ("R161_EXTERNAL_URL",)}
URL_VAR = {"storage": "R161_STORAGE_URL", "llm": "R161_LLM_URL", "external": "R161_EXTERNAL_URL"}


async def run_target(target: str, env: Dict[str, str], simulated: bool) -> Dict[str, Any]:
    started = time.monotonic()
    if target == "mcp":
        return _result("mcp", "BLOCKED", MCP_REASON)
    miss = _missing(env, REQUIRED[target])
    if miss:
        return _result(target, "BLOCKED", f"{target}: missing configuration: {', '.join(miss)}")
    url = env.get(URL_VAR[target]) or (DEFAULT_LLM_URL if target == "llm" else "")
    pf = preflight(target, url, env, simulated)
    if not pf["ok"]:
        return _result(target, "BLOCKED", pf["reason"])
    if pf["class"] == "live_integration":
        why = await _public_dns_check(url)
        if why:
            return _result(target, "BLOCKED", f"{target}: {why}", endpoint_host=pf["host"])
    try:
        res = await PROBES[target](env, pf, _timeout(env))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — never a traceback (it may carry a secret)
        res = _result(target, "FAIL", f"probe error: {type(e).__name__}", pf["class"], endpoint_host=pf["host"])
    if pf.get("credential_expires_at"):
        res["credential_expires_at_declared"] = pf["credential_expires_at"]
    res["duration_s"] = round(time.monotonic() - started, 3)
    if res.get("cancelled"):
        res["outcome"] = "FAIL"
    if res["outcome"] == "PASS" and not res["connected"]:     # never a PASS without a real answer
        res["outcome"], res["reason"] = "FAIL", "no response was received from the endpoint"
    return res


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    by = {c: {o: 0 for o in OUTCOMES} for c in CLASSES + ("unclassified",)}
    for r in results:
        by[r["evidence_class"] or "unclassified"][r["outcome"]] += 1
    return by


def exit_code(results: List[Dict[str, Any]]) -> int:
    outs = [r["outcome"] for r in results]
    if "FAIL" in outs:
        return 1
    if "BLOCKED" in outs:
        return 2
    if "PASS" not in outs:
        return 3
    return 0


def _git_sha() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


async def run(targets: List[str], env: Dict[str, str], simulated: bool = False) -> Dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    results: List[Dict[str, Any]] = []
    interrupted = False
    for t in TARGETS:
        if t not in targets:
            results.append(_result(t, "NOT_RUN", "not requested"))
        elif interrupted:
            results.append(_result(t, "FAIL", "run cancelled before this target"))
        else:
            try:
                r = await run_target(t, env, simulated)
            except asyncio.CancelledError:          # cancelled outside a probe (preflight)
                r = _result(t, "FAIL", "cancelled", cancelled=True)
                _note_cancel(r)
            results.append(r)
            interrupted = bool(r.get("cancelled"))
    report = {"report": "r161_integration", "version": REPORT_VERSION, "git_sha": _git_sha(),
              "started_at": started, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "simulated": simulated, "requested": [t for t in TARGETS if t in targets],
              "results": results, "summary": summarize(results)}
    report["exit_code"] = exit_code(results)
    return report


def render(report: Dict[str, Any]) -> str:
    lines = [f"R-161 integration report v{report['version']} git_sha={report['git_sha'][:12]} "
             f"simulated={report['simulated']} exit_code={report['exit_code']}"]
    for r in report["results"]:
        cls = r["evidence_class"] or "-"
        cl = r.get("cleanup") or {}
        lines.append(f"  {r['target']:<9} {r['outcome']:<8} class={cls:<17} host={r['endpoint_host'] or '-'} "
                     f"connected={r['connected']} cleanup={'ok' if cl.get('ok') else ('-' if not cl else 'FAILED')} "
                     f"reason={r['reason']}")
        for c in r.get("checks", []):
            lines.append(f"      [{'ok' if c['ok'] else 'FAIL'}] {c['name']}" + (f" — {c['detail']}" if c["detail"] else ""))
    s = report["summary"]
    lines.append("  summary: " + "; ".join(f"{c}: " + ", ".join(f"{o}={n}" for o, n in s[c].items() if n)
                                         for c in s if any(s[c].values())))
    lines.append("  NOTE: only live_integration PASS rows are live evidence; mock and local_integration are not.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--targets", default="", help="comma list of: " + ",".join(TARGETS))
    ap.add_argument("--report", default="", help="write the (redacted) JSON report here")
    ap.add_argument("--simulated", action="store_true",
                    help="the endpoints are simulated services (loopback only): results are class mock")
    a = ap.parse_args(argv)
    targets = [t.strip() for t in a.targets.split(",") if t.strip()]
    bad = [t for t in targets if t not in TARGETS]
    if bad:
        print(f"unknown target(s): {', '.join(bad)}", file=sys.stderr)
        return 64
    env = dict(os.environ)
    red = Redactor(env)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction(red)
    async def _main():
        import signal
        task = asyncio.current_task()
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        except (NotImplementedError, RuntimeError):
            pass
        return await run(targets, env, a.simulated)
    try:
        report = asyncio.run(_main())
    except KeyboardInterrupt:
        print("interrupted before a report could be written", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 — last resort: type only, redacted, no traceback
        print(red.text(f"runner error: {type(e).__name__}"), file=sys.stderr)
        return 1
    report = red.obj(report)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
    print(red.text(render(report)))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
