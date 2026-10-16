#!/usr/bin/env python3
"""
AGOS Agent Civilization - REST API Server (FastAPI)

- 20 specializations x N real agents (LLM brains + real tools)
- External open-source agents via OpenAI-compatible HTTP adapter
- Task dispatching with async execution + Supabase persistence
- Natural-language /chat endpoint that routes to the best agent
- Server-Sent Events (SSE) for real-time task updates
- Serves the built React frontend from /web/dist

Run: uvicorn server:app --host 0.0.0.0 --port $PORT

Security Features:
- Configurable CORS (restricted by default)
- API Key authentication (optional)
- Rate limiting (configurable)
- Input validation
- Structured logging
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from agent_civilization.agents.real import SPECIALIZATIONS, create_civilization
from agent_civilization.agents.real.real_agents import SPECIALIZATION_ICONS, SPECIALIZATION_TOOLS
from agent_civilization.agents.external_adapter import (
    ExternalAgentAdapter,
    load_external_agents_from_env,
)
from agent_civilization.core import experience, safety, tenancy
from agent_civilization.core.tenancy import ALL_TENANTS, Principal
from agent_civilization.core.sse import EventBus, SSELimits, StreamLimit
from agent_civilization.routing import route as route_request
from agent_civilization.core.llm import (
    get_llm, keyless_enabled, reset_public_llm_consent, set_public_llm_consent,
)

# R-100 (D-044): make /proc/<pid>/environ of the server unreadable to any child
# (including test_runner code) before anything else runs.
safety.harden_server_process()
from agent_civilization.storage import get_storage, is_configured as supabase_configured, key_status, log_key_status, storage_stats

# ============================================================================
# CONFIGURATION
# ============================================================================

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(funcName)s:%(lineno)d"
)
logger = logging.getLogger("agos.server")

# Security Configuration
API_KEY = os.getenv("AGOS_API_KEY", "")  # Legacy operator key (admin principal, SEC-02)
ALLOWED_ORIGINS = os.getenv("AGOS_ALLOWED_ORIGINS", "*").split(",")  # Default: all origins
# SEC-02 (R-138, D-056): API key -> Principal(tenant_id, role), from
# AGOS_TENANT_KEYS and/or AGOS_API_KEY. A malformed configuration raises
# tenancy.CredentialsError here, so the server does not start unprotected.
CREDENTIALS: Dict[str, Principal] = tenancy.load_credentials()
# SEC-03 (R-139): production (the default — AGOS_ENV unset) does not start
# without valid credentials; only an explicit AGOS_ENV=development|test may,
# and never on a deployed platform (RENDER=true). Raises CredentialsError.
RUNTIME_ENV = tenancy.enforce_startup(CREDENTIALS)
ENABLE_AUTH = bool(CREDENTIALS)  # credentials configured (tenant data needs them in any case)
logger.info("startup: AGOS_ENV=%s, %d credential(s) for %d tenant(s)", RUNTIME_ENV, len(CREDENTIALS),
            len({p.tenant_id for p in CREDENTIALS.values()}))
if not CREDENTIALS:
    logger.warning("startup: no credentials (AGOS_ENV=%s) — every tenant-data route answers 401", RUNTIME_ENV)

# Rate Limiting Configuration
RATE_LIMIT_REQUESTS = int(os.getenv("AGOS_RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("AGOS_RATE_LIMIT_WINDOW", "60"))  # seconds

# Request tracking for rate limiting
request_counts: Dict[str, List[datetime]] = defaultdict(list)

# App metadata
@asynccontextmanager
async def _lifespan(_app):
    """R-042: close the storage HTTP session on shutdown (was leaked — 'Unclosed client session').
    R-078: classify the Supabase key at startup and warn loudly if it is a public one."""
    log_key_status()
    yield
    await experience.persist()  # R-103: no-op unless AGOS_MEMORY_FILE is set
    await get_storage().close()


app = FastAPI(
    lifespan=_lifespan,
    title="AGOS Agent Civilization",
    description="A real AI agent civilization: 20 specializations of working agents serving the user.",
    version="3.0.0",
)

# ============================================================================
# SECURITY MIDDLEWARE
# ============================================================================

# Configure CORS securely
if ALLOWED_ORIGINS == ["*"]:
    logger.warning("CORS is open to all origins. Set AGOS_ALLOWED_ORIGINS for production.")
else:
    logger.info(f"CORS restricted to origins: {ALLOWED_ORIGINS}")

# R-004(2): allow_credentials is only safe with an explicit allowlist, never with "*".
_IS_WILDCARD = any(o.strip() == "*" for o in ALLOWED_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=not _IS_WILDCARD,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)
if _IS_WILDCARD:
    logger.warning("CORS allows all origins; credentials header disabled until AGOS_ALLOWED_ORIGINS is set.")

# R-083: count HTTP requests (method / route template / status) and durations
# for /metrics. Pure ASGI — streaming responses (SSE) pass through untouched.
from agos_kernel.infrastructure.metrics import MetricsMiddleware  # noqa: E402
app.add_middleware(MetricsMiddleware)


# ============================================================================
# SECURITY HELPERS
# ============================================================================

# API Key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


_AUTH_HEADERS = {"WWW-Authenticate": "ApiKey"}


def _authenticate(api_key: Optional[str]) -> Principal:
    """SEC-02: 401 for a missing or invalid key; the principal otherwise."""
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required", headers=_AUTH_HEADERS)
    principal = tenancy.authenticate(api_key, CREDENTIALS)
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid API key", headers=_AUTH_HEADERS)
    return principal


def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[Principal]:
    """Routes without tenant data (civilization registry/plan): a valid key is
    required when credentials are configured; open otherwise."""
    if not ENABLE_AUTH:
        return None
    return _authenticate(api_key)


def require_principal(api_key: Optional[str]) -> Principal:
    """SEC-02: every route that reads or changes tenant data. Always needs a
    valid key — with no credentials configured there is no identity to scope
    data to, so access is refused (never a default tenant or public data)."""
    if not ENABLE_AUTH:
        raise HTTPException(status_code=401, headers=_AUTH_HEADERS, detail=(
            "authentication is not configured on this server: tenant data is unavailable "
            "(set AGOS_TENANT_KEYS or AGOS_API_KEY)"))
    principal = _authenticate(api_key)
    tenancy.set_tenant(principal.tenant_id)  # this request's context only (copied into its tasks)
    return principal


def require_admin(api_key: Optional[str]) -> Principal:
    principal = require_principal(api_key)
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def _scope(principal: Principal, all_tenants: bool):
    """The data scope of a request: the caller's tenant; ALL_TENANTS only for an
    admin who asks for it explicitly. Never taken from a client-sent tenant id."""
    if all_tenants:
        if not principal.is_admin:
            raise HTTPException(status_code=403, detail="all_tenants requires the admin role")
        return ALL_TENANTS
    return principal.tenant_id


def check_rate_limit(client_id: str) -> None:
    """Check and enforce rate limiting per client."""
    now = datetime.now()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW)
    
    # Clean old entries
    request_counts[client_id] = [
        ts for ts in request_counts[client_id] if ts > window_start
    ]
    
    # Check limit
    if len(request_counts[client_id]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s"
        )
    
    # Add current request
    request_counts[client_id].append(now)


def rate_limit(func: Callable) -> Callable:
    """Decorator for rate limiting endpoints."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        client_id = request.client.host if request.client else "unknown"
        check_rate_limit(client_id)
        return await func(request, *args, **kwargs)
    return wrapper


# ============================================================================
# CIVILIZATION INITIALIZATION
# ============================================================================

# R-041 (D-029): جعل AGENTS_PER_SPEC إعداداً فعلياً (render.yaml يضبطه 3).
# سابقاً كان المتغير موجوداً في إعدادات النشر فقط ولا يقرؤه الكود — كان
# create_civilization() يُستدعى دائماً بالافتراضي المكتوب (50).
def _agents_per_spec() -> int:
    """قراءة AGENTS_PER_SPEC من البيئة مع تحقق الصلاحية.

    الافتراضي 50 (1000 وكيل على 20 تخصصاً). القيم غير الصالحة ترفع خطأ بدء
    واضحاً بدل تجاهلها بصمت.
    """
    raw = os.getenv("AGENTS_PER_SPEC", "")
    if not raw:
        return 50
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"AGENTS_PER_SPEC must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise RuntimeError(
            f"AGENTS_PER_SPEC must be a positive integer, got {value}"
        )
    return value


AGENTS_PER_SPEC = _agents_per_spec()
CIVILIZATION = create_civilization(AGENTS_PER_SPEC)
EXTERNAL_AGENTS: List[ExternalAgentAdapter] = load_external_agents_from_env()
TASKS: Dict[str, Dict[str, Any]] = {}
# SEC-04 (R-144): live events are broadcast in process — every SSE connection
# of a tenant has its own bounded queue and receives its own copy; nothing is
# kept for a tenant with no connection (SEC-02: a stream only ever subscribes
# to its own tenant). Limits: AGOS_SSE_* (agent_civilization/core/sse.py);
# an invalid value is a startup error.
def _sse_metric(name: str, value: float) -> None:
    from agos_kernel.infrastructure.metrics import get_metrics
    if name == "sse_connections":
        get_metrics().set_gauge(name, value)
    else:
        get_metrics().inc_counter(name, value)


SSE_LIMITS = SSELimits.from_env()
EVENT_BUS = EventBus(SSE_LIMITS, on_metric=_sse_metric)


async def _publish(tenant_id: Optional[str], event: Dict[str, Any]) -> None:
    """Never waits for a listener (a full listener queue drops its oldest event)."""
    if tenant_id is not None:
        tenancy.check_scope(tenant_id)
        EVENT_BUS.publish(tenant_id, event)


def _visible_task(task: Optional[Dict[str, Any]], scope) -> bool:
    if task is None:
        return False
    return scope is ALL_TENANTS or (task.get("tenant_id") is not None and task.get("tenant_id") == scope)
STARTED_AT = time.time()
MAX_TASKS_KEPT = 500

# BUG-05 (R-147): the startup log used to report specializations * a hardcoded
# 50 ("1000 agents total") even though AGENTS_PER_SPEC has controlled the actual
# count since R-041 (R-041/D-029).  The logged count is now the ACTUAL sum of
# the built civilization; external agents stay on their own separate line and
# are never added to the civilization count.
logger.info(f"Civilization initialized: {len(SPECIALIZATIONS)} specializations, "
            f"{sum(len(agents) for agents in CIVILIZATION.values())} civilization agents")
logger.info(f"External agents loaded: {len(EXTERNAL_AGENTS)}")


def _all_agents():
    for agents in CIVILIZATION.values():
        yield from agents
    yield from EXTERNAL_AGENTS


# BUG-01 (R-140): message for an "external" task with no registered external
# agent — names no agent, URL or key.
NO_EXTERNAL_AGENT = ("no external agent is registered: an 'external' task cannot run now "
                     "(an admin registers one via POST /api/agents/external)")


def _pick_agent(specialization: str):
    if specialization == "external" and EXTERNAL_AGENTS:
        return min(EXTERNAL_AGENTS, key=lambda a: a.stats["tasks_completed"])
    pool = CIVILIZATION.get(specialization)
    if not pool:
        raise HTTPException(404, f"Unknown specialization: {specialization}")
    idle = [a for a in pool if a.status == "idle"]
    return (idle or pool)[0]


def _prune_tasks():
    if len(TASKS) > MAX_TASKS_KEPT:
        for tid in sorted(TASKS, key=lambda t: TASKS[t]["created_at"])[: len(TASKS) - MAX_TASKS_KEPT]:
            TASKS.pop(tid, None)


async def _run_task(task_id: str, specialization: str, prompt: str, data: Any):
    # SEC-02: the background task runs as the tenant that owns the record
    # (explicit, not only inherited), so memory and events stay in that tenant.
    tenant_id = TASKS[task_id]["tenant_id"]
    tenancy.set_tenant(tenant_id)
    try:
        agent = _pick_agent(specialization)
    except HTTPException as e:
        # BUG-01: create_task refuses "external" with no agent (409), but the last
        # agent may be removed between the 202 and this run — end the task as
        # failed with a result instead of leaving it queued with an unhandled error.
        reason = NO_EXTERNAL_AGENT if specialization == "external" else str(e.detail)
        outcome = {"ok": False, "agent": None, "specialization": specialization, "elapsed_s": 0,
                   "result": {"error": reason}}
        TASKS[task_id].update(status="failed", result=outcome, finished_at=time.time())
        storage = get_storage()
        await storage.update_task(task_id, "failed", result=outcome, elapsed_s=0)
        await storage.insert_event("task_completed", {"task_id": task_id, "agent": None,
                                                      "specialization": specialization, "ok": False},
                                   tenant_id=tenant_id)
        await _publish(tenant_id, {"type": "task_completed", "task_id": task_id, "agent": None,
                                   "specialization": specialization, "ok": False, "elapsed_s": 0})
        return
    TASKS[task_id]["status"] = "running"
    TASKS[task_id]["agent"] = agent.name
    storage = get_storage()
    await storage.update_task(task_id, "running", agent_name=agent.name)

    await _publish(tenant_id, {"type": "task_started", "task_id": task_id, "agent": agent.name,
                               "specialization": specialization})

    outcome = await agent.execute({"prompt": prompt, "data": data})
    TASKS[task_id]["status"] = "completed" if outcome["ok"] else "failed"
    TASKS[task_id]["result"] = outcome
    TASKS[task_id]["finished_at"] = time.time()

    await storage.update_task(task_id, TASKS[task_id]["status"],
                              agent_name=agent.name, result=outcome, elapsed_s=outcome.get("elapsed_s", 0))
    await storage.upsert_agent_stats(
        agent.name, specialization,
        agent.stats["tasks_completed"], agent.stats["tasks_failed"], agent.stats["total_time_s"]
    )
    await storage.insert_event("task_completed", {
        "task_id": task_id, "agent": agent.name, "specialization": specialization, "ok": outcome["ok"]
    }, tenant_id=tenant_id)

    await _publish(tenant_id, {
        "type": "task_completed",
        "task_id": task_id,
        "agent": agent.name,
        "specialization": specialization,
        "ok": outcome["ok"],
        "elapsed_s": outcome.get("elapsed_s", 0),
    })


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class TaskRequest(BaseModel):
    specialization: str = Field(..., description="One of the 20 specializations, or 'external'")
    prompt: str = Field(..., min_length=1, max_length=20000)
    data: Optional[Any] = Field(None, description="Optional payload for tool-enabled specializations")
    # SEC-06 (R-145): explicit per-request consent to the public (keyless) LLM
    # provider. Strict bool: only a JSON true is consent — a missing field,
    # false, or a non-boolean value (422) never enables it. Never inferred from
    # the prompt text, the API key or the operator's AGOS_LLM_KEYLESS.
    allow_public_llm: bool = Field(False, strict=True,
                                  description="Consent to send this request's text to the external public LLM provider")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    # SEC-06 (R-145): same explicit per-request consent as TaskRequest.
    allow_public_llm: bool = Field(False, strict=True,
                                  description="Consent to send this message's text to the external public LLM provider")


class ExternalAgentRequest(BaseModel):
    name: str
    url: str
    model: str = ""
    api_key: str = ""


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api")
async def api_root(request: Request):
    """Root API endpoint - returns API information."""
    check_rate_limit(request.client.host if request.client else "unknown")
    return {
        "name": "AGOS Agent Civilization",
        "version": "3.0.0",
        "status": "alive",
        "specializations": list(SPECIALIZATIONS.keys()),
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "metrics": "/metrics",
            "api_docs": "/docs",
        },
        "security": {
            "authentication": "enabled" if ENABLE_AUTH else "disabled",
            "rate_limiting": "enabled",
        }
    }


# ============================================================================
# HEALTH & METRICS ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """
    Comprehensive health check endpoint.
    
    Returns system health status including:
    - API availability
    - Civilization status
    - Storage status
    - Redis status (if configured)
    """
    try:
        # Import health check components
        from agos_kernel.infrastructure.health import get_health_registry
        
        registry = get_health_registry()
        system_health = await registry.get_system_health()
        
        # Fix R-004(4): if unhealthy, return 503 (previously always 200).
        # 'degraded' still serves traffic (storage fallback), so 200 is correct there.
        if system_health.get("status") == "unhealthy":
            return JSONResponse(status_code=503, content=system_health)
        return system_health
        
    except ImportError:
        # Fallback if infrastructure module not available
        return {
            "status": "healthy",
            "uptime_seconds": round(time.time() - STARTED_AT, 2),
            "checks": {
                "api": {"status": "healthy", "message": "OK"},
                "civilization": {"status": "healthy", "message": f"{len(SPECIALIZATIONS)} specializations"}
            },
            "summary": {"total": 1, "healthy": 1}
        }


@app.get("/health/live")
async def liveness_probe():
    """
    Kubernetes liveness probe.
    
    Returns 200 if the application is running.
    """
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/health/ready")
async def readiness_probe():
    """
    Kubernetes readiness probe.
    
    Returns 200 only if the application is ready to accept traffic; every
    not-ready state answers 503 (BUG-03, R-142 — before: 200 with
    ``status: not_ready``). Checks:
    - API is responsive
    - Civilization is initialized
    """
    def not_ready(reason: str) -> JSONResponse:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason})

    try:
        # Check if civilization is initialized
        if not CIVILIZATION:
            return not_ready("Civilization not initialized")

        # Check if agents are available
        total_agents = sum(len(agents) for agents in CIVILIZATION.values())
        if total_agents == 0:
            return not_ready("No agents available")

        return {
            "status": "ready",
            "agents": total_agents,
            "specializations": len(SPECIALIZATIONS)
        }

    except Exception as e:
        # The check could not complete: not ready. The response carries a
        # fixed reason (it used to echo str(e)); the internal log names the
        # exception type only, so no message content can leak from here.
        logger.warning("readiness check failed: %s", type(e).__name__)
        return not_ready("readiness check failed")


@app.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus metrics endpoint.
    
    Returns metrics in Prometheus text format for scraping.
    """
    try:
        from agos_kernel.infrastructure.metrics import get_metrics
        
        metrics = get_metrics()
        
        # Add runtime metrics
        metrics.set_gauge("uptime_seconds", time.time() - STARTED_AT)
        metrics.set_gauge("queue_size", len(TASKS))
        
        # Agent counts
        for spec, agents in CIVILIZATION.items():
            active = sum(1 for a in agents if a.status == "idle")
            metrics.set_gauge("agents_active", active, {"specialization": spec})

        # R-082 (RISK-013): storage operation outcomes since process start
        # (cumulative values exported as gauges; -1 = no operation yet).
        st = storage_stats()
        for op, c in st["operations"].items():
            metrics.set_gauge("storage_operations", c["ok"], {"operation": op, "outcome": "ok"})
            metrics.set_gauge("storage_operations", c["failed"], {"operation": op, "outcome": "failed"})
        last = st["last_ok"]
        metrics.set_gauge("storage_last_operation_ok", -1 if last is None else int(bool(last)))
        
        return Response(
            content=metrics.export_prometheus(),
            media_type="text/plain; charset=utf-8"
        )
        
    except ImportError:
        # Fallback basic metrics
        content = f"""# HELP agos_uptime_seconds System uptime in seconds
# TYPE agos_uptime_seconds gauge
agos_uptime_seconds {time.time() - STARTED_AT}

# HELP agos_tasks_in_queue Current tasks in queue
# TYPE agos_tasks_in_queue gauge
agos_tasks_in_queue {len(TASKS)}

# HELP agos_specializations Number of specializations
# TYPE agos_specializations gauge
agos_specializations {len(SPECIALIZATIONS)}
"""
        return Response(content=content, media_type="text/plain; charset=utf-8")


@app.get("/api/system/status")
@rate_limit
async def system_status(request: Request):
    """Get system status and health metrics."""
    logger.info(f"System status requested from {request.client.host if request.client else 'unknown'}")
    agents = [a.snapshot() for a in _all_agents()]
    completed = sum(a["stats"]["tasks_completed"] for a in agents)
    failed = sum(a["stats"]["tasks_failed"] for a in agents)
    storage = get_storage()
    # operational count across tenants (no content) — SEC-02, docs/governance/SECURITY.md
    db_tasks = await storage.get_tasks(limit=500, scope=ALL_TENANTS)
    return {
        "status": "healthy",
        "uptime_s": round(time.time() - STARTED_AT),
        "llm_configured": get_llm().available,
        "llm_model": get_llm().model,
        # SEC-06 (R-145): non-secret provider disclosure so the UI can warn and
        # ask for consent accurately: which provider is selected, whether the
        # public (keyless) provider is operator-enabled, and that using it
        # additionally requires the request's explicit consent. No keys, no
        # endpoints' secrets, no request content is ever exposed or logged here.
        "llm_provider": get_llm().provider,
        # SEC-06 (R-145): operator opt-in flag, independent of which provider
        # the OpenRouter key selects (with a key AND keyless enabled, the keyed
        # provider wins and no consent is required).
        "public_llm_enabled": keyless_enabled(),
        "public_llm_requires_consent": get_llm().provider == "keyless",
        "supabase_configured": supabase_configured(),
        "supabase_key": key_status(),  # R-078: {"kind": service|public|unknown|none, "source": var} — never the value
        "specializations": len(SPECIALIZATIONS),
        "total_agents": len(agents),
        "external_agents": len(EXTERNAL_AGENTS),
        "tasks_completed": completed,
        "tasks_failed": failed,
        "tasks_in_memory": len(TASKS),
        "tasks_in_db": len(db_tasks) if supabase_configured() else None,
        "security": {
            "authentication": "enabled" if ENABLE_AUTH else "disabled",
            "rate_limiting": f"{RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
            # R-100 (D-044): what real tools are allowed to do in this process.
            "tool_policy": safety.policy_snapshot(),
        },
        # R-103 (D-046): memory and learning on the live path. SEC-02: config
        # only here; a tenant's own memory/learning is at /api/memory, /api/learning.
        "experience": experience.public_stats(),
    }


@app.get("/api/agents")
@rate_limit
async def list_agents(request: Request, specialization: Optional[str] = None):
    """List all agents or filter by specialization."""
    agents = [a.snapshot() for a in _all_agents()]
    if specialization:
        agents = [a for a in agents if a["specialization"] == specialization]
    return {"count": len(agents), "agents": agents}


@app.get("/api/specializations")
@rate_limit
async def list_specializations(request: Request):
    """List all available specializations."""
    return {
        "specializations": [
            {
                "name": name,
                "description": prompt.split(".")[0],
                "agents": len(CIVILIZATION[name]),
                "icon": SPECIALIZATION_ICONS.get(name, "robot"),
                "tool": SPECIALIZATION_TOOLS.get(name),
            }
            for name, prompt in SPECIALIZATIONS.items()
        ]
    }


@app.post("/api/tasks", status_code=202)
@rate_limit
async def create_task(request: Request, req: TaskRequest, api_key: Optional[str] = Security(api_key_header)):
    # SEC-02: the owner is the authenticated tenant; any tenant field in the
    # body is ignored (TaskRequest has none; extra fields are dropped).
    principal = require_principal(api_key)
    if req.specialization not in SPECIALIZATIONS and req.specialization != "external":
        raise HTTPException(400, f"Unknown specialization '{req.specialization}'. Valid: {list(SPECIALIZATIONS)} + ['external']")
    if req.specialization == "external" and not EXTERNAL_AGENTS:
        # BUG-01 (R-140): after authentication (an anonymous caller learns
        # nothing about the configuration) and before any side effect — no
        # task_id, TASKS record, storage write, event, SSE or background run.
        raise HTTPException(409, NO_EXTERNAL_AGENT)
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "id": task_id,
        "specialization": req.specialization,
        "prompt": req.prompt[:500],
        "status": "queued",
        "created_at": time.time(),
        "result": None,
        "tenant_id": principal.tenant_id,
    }
    _prune_tasks()
    storage = get_storage()
    await storage.insert_task(task_id, req.specialization, req.prompt, "queued", tenant_id=principal.tenant_id)
    await storage.insert_event("task_created", {"task_id": task_id, "specialization": req.specialization},
                               tenant_id=principal.tenant_id)
    await _publish(principal.tenant_id, {"type": "task_created", "task_id": task_id,
                                         "specialization": req.specialization})
    # SEC-06 (R-145): the consent of THIS request is carried into the background
    # execution (asyncio.create_task copies the current context), so the agent
    # that later calls the LLM sees it — and only it. It is never written to the
    # task record, the event or storage, and it does not outlive the request.
    token = set_public_llm_consent(req.allow_public_llm)
    try:
        asyncio.create_task(_run_task(task_id, req.specialization, req.prompt, req.data))
    finally:
        reset_public_llm_consent(token)
    logger.info(f"Task created: {task_id} for specialization: {req.specialization}")
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks")
@rate_limit
async def list_tasks(request: Request, limit: int = 50, all_tenants: bool = False,
                     api_key: Optional[str] = Security(api_key_header)):
    """List recent tasks of the caller's tenant (SEC-02)."""
    scope = _scope(require_principal(api_key), all_tenants)
    storage = get_storage()
    if supabase_configured():
        db_tasks = await storage.get_tasks(limit=limit, scope=scope)
        if db_tasks:
            return {"count": len(db_tasks), "tasks": db_tasks, "source": "database"}
    mine = [t for t in TASKS.values() if _visible_task(t, scope)]
    items = sorted(mine, key=lambda t: t["created_at"], reverse=True)[: min(limit, 200)]
    return {"count": len(items), "tasks": items, "source": "memory"}


@app.get("/api/tasks/{task_id}")
@rate_limit
async def get_task(request: Request, task_id: str, all_tenants: bool = False,
                   api_key: Optional[str] = Security(api_key_header)):
    """Get task details by ID — only a task of the caller's tenant (SEC-02).
    Another tenant's task answers exactly like a missing one (404).
    BUG-04 (R-143): a task absent from process memory (e.g. after a restart)
    is read from storage by ID, the tenant scope enforced in the query."""
    scope = _scope(require_principal(api_key), all_tenants)
    task = TASKS.get(task_id)
    if task is not None:
        # in memory: the memory record decides — out of scope is 404, never
        # retried in storage
        if not _visible_task(task, scope):
            raise HTTPException(404, "task not found")
        return task
    if supabase_configured():
        row = await get_storage().get_task(task_id, scope=scope)
        if row is not None:
            # the stored row as is, plus ``id`` (= task_id, the memory record's
            # key) and ``source`` — no other field is added or invented
            return {**row, "id": row["task_id"], "source": "database"}
    raise HTTPException(404, "task not found")


@app.get("/api/memory")
@rate_limit
async def get_memory(request: Request, q: str = "", specialization: Optional[str] = None, limit: int = 20,
                     all_tenants: bool = False, api_key: Optional[str] = Security(api_key_header)):
    """R-103: what the civilization remembers of the caller tenant's earlier tasks (SEC-02)."""
    scope = _scope(require_principal(api_key), all_tenants)
    items = await experience.search(q, specialization, limit, scope=scope)
    return {"count": len(items), "memories": items, "stats": experience.stats(scope)}


@app.get("/api/learning")
@rate_limit
async def get_learning(request: Request, all_tenants: bool = False,
                       api_key: Optional[str] = Security(api_key_header)):
    """R-103: outcomes measured from the caller tenant's real tasks (SEC-02)."""
    scope = _scope(require_principal(api_key), all_tenants)
    return experience.stats(scope)


@app.delete("/api/memory")
@rate_limit
async def clear_memory(request: Request, all_tenants: bool = False,
                       api_key: Optional[str] = Security(api_key_header)):
    """R-103: forget the caller tenant's memory and learning (SEC-02). Only an
    admin with all_tenants=true clears everything (incl. quarantined entries)."""
    scope = _scope(require_principal(api_key), all_tenants)
    if scope is ALL_TENANTS:
        before = experience.stats(ALL_TENANTS)["memories"]
        experience.reset()
    else:
        before = await experience.forget_tenant(scope)
    await experience.persist()
    return {"cleared": before}


@app.get("/api/events")
@rate_limit
async def list_events(request: Request, limit: int = 50, all_tenants: bool = False,
                      api_key: Optional[str] = Security(api_key_header)):
    """List recent events of the caller's tenant (SEC-02)."""
    scope = _scope(require_principal(api_key), all_tenants)
    storage = get_storage()
    events = await storage.get_events(limit=limit, scope=scope)
    return {"count": len(events), "events": events}


SSE_LIMIT_DETAIL = "too many event streams, retry later"


class _SSEResponse(StreamingResponse):
    """Releases the subscription however the response ends (normal end,
    lifetime, client disconnect, cancellation, error) — even if the body
    generator never started."""

    def __init__(self, content, release: Callable[[], Any], **kw):
        super().__init__(content, **kw)
        self._release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


@app.get("/api/events/stream")
@rate_limit
async def event_stream(request: Request, api_key: Optional[str] = Security(api_key_header)):
    """Server-Sent Events stream of the caller tenant's task updates (SEC-02).
    Needs X-API-Key like every tenant-data route (the web UI reads it with
    fetch, not EventSource, so it can send the header).
    SEC-04 (R-144): admission (open-rate per client, total / per-tenant /
    per-client connections) is decided here, before the 200 — refusal is 429
    + Retry-After with a fixed message; the stream ends at AGOS_SSE_MAX_LIFETIME."""
    tenant = require_principal(api_key).tenant_id
    client = request.client.host if request.client else "unknown"
    try:
        sub = EVENT_BUS.open(tenant, client)
    except StreamLimit as e:
        logger.warning("sse: connection refused (%s), retry after %ss", e.reason, e.retry_after)
        raise HTTPException(429, SSE_LIMIT_DETAIL, headers={"Retry-After": str(e.retry_after)})

    def release() -> None:
        sub.bus.close(sub)  # the bus it was opened on (idempotent)

    async def generate():
        try:
            while True:
                left = sub.bus.lifetime_left(sub)
                if left <= 0:
                    return  # max lifetime: end the stream; the client reconnects
                event = await sub.next(timeout=min(sub.bus.limits.heartbeat_s, left))
                if event is None:
                    if sub.bus.lifetime_left(sub) <= 0:
                        return
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        finally:
            release()

    return _SSEResponse(generate(), release, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


ROUTER_SYSTEM = (
    "You are the dispatcher of an AI agent civilization with these specializations: "
    + ", ".join(SPECIALIZATIONS.keys())
    + ". Given a user command (in any language, including Arabic), reply with ONLY the single "
    "best specialization name from the list, nothing else."
)


@app.post("/api/chat")
@rate_limit
async def chat(request: Request, req: ChatRequest, api_key: Optional[str] = Security(api_key_header)):
    # SEC-02: identity first; memory recall/storage below runs as this tenant.
    principal = require_principal(api_key)
    """Natural-language command: route to the best specialist and execute."""
    # R-106: the LLM picks the specialist when configured; otherwise (or when
    # its answer names no specialization) the deterministic keyword router
    # decides.  Before R-106 every message went to "communicator" without an LLM.
    llm = get_llm()
    spec, method, reason = None, "keywords", ""
    # R-131 (D-054): the LLM chooses the specialist only with a configured
    # provider key. The public keyless provider serves one call at a time
    # (D-051): routing every chat message through it made requests that need
    # no LLM wait up to ~75 s behind it under concurrent HTTP load, and it
    # routed worse than the verified keyword router (R-122: 24/24)
    # — docs/audit/M7/logs/raw/156_r131_http_load_before.log.
    if llm.available and getattr(llm, "provider", "openrouter") != "keyless":
        routed = await llm.complete(ROUTER_SYSTEM, req.message, max_tokens=20)
        if routed["ok"]:
            candidate = routed["content"].strip().lower().replace("-", "_")
            for name in SPECIALIZATIONS:
                if name in candidate:
                    spec, method, reason = name, "llm", "LLM dispatcher"
                    break
    if spec is None:
        plan = route_request(req.message)
        spec, reason = plan["primary"], plan["reason"]
    agent = _pick_agent(spec)
    # SEC-06 (R-145): the consent of THIS chat request governs only the LLM
    # calls made while executing it (the routing LLM is only used with a keyed
    # provider — R-131/D-054 — so the keyless provider can appear only inside
    # agent execution). Reset in finally: consent never leaks past this request.
    token = set_public_llm_consent(req.allow_public_llm)
    try:
        outcome = await agent.execute({"prompt": req.message})
    finally:
        reset_public_llm_consent(token)
    # BUG-02 (R-141): one status derived from the outcome, used for the memory
    # record, both storage writes and the chat_completed event.
    chat_status = "completed" if outcome["ok"] else "failed"

    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "id": task_id,
        "specialization": spec,
        "prompt": req.message[:500],
        "status": chat_status,
        "created_at": time.time(),
        "finished_at": time.time(),
        "result": outcome,
        "agent": agent.name,
        "tenant_id": principal.tenant_id,
    }
    _prune_tasks()

    storage = get_storage()
    await storage.insert_task(task_id, spec, req.message, chat_status, tenant_id=principal.tenant_id)
    await storage.update_task(task_id, chat_status, agent_name=agent.name,
                              result=outcome, elapsed_s=outcome.get("elapsed_s", 0))
    await storage.upsert_agent_stats(
        agent.name, spec,
        agent.stats["tasks_completed"], agent.stats["tasks_failed"], agent.stats["total_time_s"]
    )
    # The event type stays chat_completed (end of processing, for existing
    # consumers); "status" says whether the execution succeeded.
    await storage.insert_event("chat_completed", {
        "task_id": task_id, "agent": agent.name, "specialization": spec, "ok": outcome["ok"],
        "status": chat_status,
    }, tenant_id=principal.tenant_id)

    logger.info(f"Chat processed: {task_id} routed to {spec} by agent {agent.name}")
    return {
        "task_id": task_id,
        "routed_to": spec,
        "routing": {"method": method, "reason": reason},
        "agent": agent.name,
        "outcome": outcome,
    }


@app.post("/api/agents/external", status_code=201)
@rate_limit
async def register_external_agent(
    request: Request,
    req: ExternalAgentRequest,
    api_key: Optional[str] = Security(api_key_header)
):
    """Register an external agent (requires the admin role)."""
    # SEC-02: an external agent is shared by every tenant — the prompts of all
    # tenants' "external" tasks are sent to it — so only an admin may add one.
    require_admin(api_key)

    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    if any(a.name == req.name for a in EXTERNAL_AGENTS):
        raise HTTPException(409, f"external agent '{req.name}' already registered")
    # R-100 (D-044): an anonymous client never reaches this point (admin role
    # required above); the admin may target its own private network, as the
    # AGOS_API_KEY holder could before SEC-02.
    allow_private = True
    try:
        from agent_civilization.core import pools  # R-132: DNS check on the net pool
        await pools.run("net", safety.check_url, req.url, allow_private)
    except safety.UnsafeURLError as e:
        raise HTTPException(400, f"url rejected: {e}")
    except OSError as e:
        raise HTTPException(400, f"url host cannot be resolved: {e}")
    adapter = ExternalAgentAdapter(req.name, req.url, req.model, req.api_key,
                                   allow_private=allow_private)
    EXTERNAL_AGENTS.append(adapter)
    logger.warning(f"External agent registered: {req.name} at {req.url}")
    return {"registered": adapter.snapshot()}


@app.delete("/api/agents/external/{name}")
@rate_limit
async def remove_external_agent(
    request: Request,
    name: str,
    api_key: Optional[str] = Security(api_key_header)
):
    """Remove an external agent (requires the admin role)."""
    require_admin(api_key)

    for i, a in enumerate(EXTERNAL_AGENTS):
        if a.name == name:
            EXTERNAL_AGENTS.pop(i)
            logger.warning(f"External agent removed: {name}")
            return {"removed": name}
    raise HTTPException(404, "external agent not found")


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Civilization auto-routing endpoints (no user-picked specialization)
# ---------------------------------------------------------------------------
try:
    from agent_civilization.civilization_routes import register_civilization_routes
    register_civilization_routes(app, CIVILIZATION, TASKS, _prune_tasks, verify_api_key, require_principal)
    logger.info("Civilization routes registered successfully")
except ImportError as e:
    logger.warning(f"Civilization routes not available: {e}")

# Serve the frontend (built React app from /web/dist)
# ---------------------------------------------------------------------------
WEB_DIST = Path(__file__).parent / "web" / "dist"

if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api") or full_path.startswith("docs") or full_path.startswith("openapi"):
            raise HTTPException(404)
        file_path = WEB_DIST / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        index = WEB_DIST / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, "frontend not built")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
