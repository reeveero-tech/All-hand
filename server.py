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
import hashlib
import hmac
import json
import logging
import logging.handlers
import math
import os
import queue
import random
import re
import secrets
import tempfile
import time
import urllib.parse
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
from agent_civilization.core import experience, safety, telemetry, tenancy
from agent_civilization.core import agent_registry
from agent_civilization.core import task_queue
from agent_civilization.core import backpressure, pools
from agent_civilization.core.tenancy import ALL_TENANTS, Principal
from agent_civilization.core.sse import EventBus, SSELimits, StreamLimit
from agent_civilization.core.sse_admission import (
    SSEAdmissionError,
    SSELeaseStore,
    renewal_loop as _sse_renewal_loop,
    sse_admission_default_db_path as _sse_admission_db_path,
)
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

# Operational hardening (log 294 §72): the telemetry middleware writes one INFO
# record per request from inside the event loop, so with synchronous
# destinations a slow or blocked log destination (full pipe, stalled collector)
# froze the whole server — reproduced red-first in
# tests/test_op_sync_logging_freeze.py. The root destinations now sit behind a
# queue: the loop only enqueues; a daemon drain thread writes to the real
# destinations. Reload-safe: if this module is reloaded (tests do
# importlib.reload), the existing queue and listener are reused and any handler
# added to the root logger in the meantime (e.g. a test capture handler) stays
# synchronous on the root logger instead of being moved behind the queue.
# Residual risk (documented, accepted): a destination blocked forever grows the
# queue in memory — the loop stays live; the queue drains when the destination
# recovers; monitoring/restart bounds the damage.
class _AsyncLogQueueHandler(logging.handlers.QueueHandler):
    """Marks our queue handler; carries the listener so a reload can reuse it."""

    listener: "logging.handlers.QueueListener | None" = None


_root_logger = logging.getLogger()
# NOTE: marked on the instance (not isinstance of this module's class) so that
# importlib.reload(server) — which redefines the class and breaks isinstance —
# still recognizes the previously installed handler and reuses its queue.
_existing_qh = next(
    (h for h in _root_logger.handlers if getattr(h, "_agos_async_log_queue", None)), None
)
if _existing_qh is None:
    _LOG_QUEUE: "queue.SimpleQueue[logging.LogRecord]" = queue.SimpleQueue()
    _LOG_LISTENER = logging.handlers.QueueListener(
        _LOG_QUEUE, *list(_root_logger.handlers), respect_handler_level=True
    )
    _LOG_LISTENER.start()
    _qh = _AsyncLogQueueHandler(_LOG_QUEUE)
    _qh.listener = _LOG_LISTENER
    _qh._agos_async_log_queue = _LOG_QUEUE
    _root_logger.handlers = [_qh]
else:
    _LOG_QUEUE = _existing_qh._agos_async_log_queue
    _LOG_LISTENER = _existing_qh.listener

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
# R-189: encrypted per-tenant secret storage — OPT-IN. No AGOS_SECRETS_DB =
# no store and no behavior change (env-configured SEC-02 auth stays the base
# and the production bootstrap). A configured-but-broken store stops the
# server loudly (fail-closed — never a silent auth downgrade). Store-backed
# tenant keys are additive principals: created/rotated/revoked at runtime,
# resolved per request (no cache — rotation and revocation apply immediately
# across processes sharing the file).
from agent_civilization.core.secret_store import (  # noqa: E402
    MasterKeyError, SecretStore, SecretStoreError,
    default_master_key_file as _secrets_master_key_file,
)
def _build_secret_store():
    db = (os.environ.get("AGOS_SECRETS_DB") or "").strip()
    if not db:
        return None
    store = SecretStore(db, _secrets_master_key_file())
    try:
        store.initialize()  # explicit init: creates key file + schema; never regenerates for existing data
    except (MasterKeyError, SecretStoreError) as exc:
        raise RuntimeError(f"AGOS_SECRETS_DB is configured but the secret store is unusable: {exc}") from None
    return store
_SECRET_STORE = _build_secret_store()
ENABLE_AUTH = bool(CREDENTIALS) or _SECRET_STORE is not None
logger.info("startup: AGOS_ENV=%s, %d credential(s) for %d tenant(s), secret store %s",
            RUNTIME_ENV, len(CREDENTIALS),
            len({p.tenant_id for p in CREDENTIALS.values()}),
            "enabled" if _SECRET_STORE is not None else "disabled")
if not CREDENTIALS:
    logger.warning("startup: no credentials (AGOS_ENV=%s) — every tenant-data route answers 401", RUNTIME_ENV)

# Rate Limiting Configuration
RATE_LIMIT_REQUESTS = int(os.getenv("AGOS_RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("AGOS_RATE_LIMIT_WINDOW", "60"))  # seconds

# Shared rate-limit counter (log 299; red tests b061663 / 76b7522): the limit
# must be GLOBAL across server processes — a process-local memory counter
# admitted a full window PER WORKER behind a balancer (N workers = N× the
# configured limit). One store instance per process; the SQLite file (WAL,
# BEGIN IMMEDIATE — atomic prune+count+insert) is the shared state. Created
# at import, loudly: a broken store stops the server, never runs unlimited.
from agent_civilization.core.rate_limit_store import (  # noqa: E402
    RateLimitStore, RateLimitStoreError,
    default_db_path as _rate_limit_default_db_path,
)
_RATE_LIMIT_STORE = RateLimitStore(_rate_limit_default_db_path())


def reset_rate_limits() -> None:
    """Test/ops helper: clear the shared counter (was ``request_counts.clear()``)."""
    _RATE_LIMIT_STORE.reset()

# App metadata
@asynccontextmanager
async def _lifespan(_app):
    """R-042: close the storage HTTP session on shutdown (was leaked — 'Unclosed client session').
    R-078: classify the Supabase key at startup and warn loudly if it is a public one."""
    log_key_status()
    _start_registry_worker()  # R-162: a fresh worker after a previous shutdown
    _start_queue_loops()  # R-163: durable dispatcher + reaper (storage configured)
    yield
    _stop_queue_loops()  # R-163: no lease renewal after shutdown
    _stop_registry_worker()  # R-162: no lease outlives the worker
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
from agos_kernel.infrastructure.health import civilization_status, set_civilization_provider  # noqa: E402  (R-157)
app.add_middleware(MetricsMiddleware)
# R-159 (D-071): one trace + one `http` span per request (outermost, so the
# trace covers the whole request; reset in finally — never inherited by a
# later request). Records hold no prompt/result/credential/raw tenant id.
app.add_middleware(telemetry.TelemetryMiddleware)


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
    if principal is None and _SECRET_STORE is not None:
        # R-189: store-backed tenant keys (the tenant identity comes ONLY
        # from this server-side resolution — SEC-02; never from the caller).
        info = _SECRET_STORE.authenticate_key(api_key)
        if info is not None:
            principal = Principal(info["tenant"], info["role"])
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


async def check_rate_limit(client_id: str) -> None:
    """Check and enforce rate limiting per client.

    Log 299 (b061663 / 76b7522): the counter is the SHARED store above, not
    process memory — behind a balancer the in-memory counter used to admit a
    full window per worker. The store call is blocking SQLite, so it runs
    off the event loop (``asyncio.to_thread``). Fail-closed: a store outage
    refuses the request (503 + Retry-After) — never a silent fallback to
    unlimited or process-local counting.
    """
    try:
        allowed, wait = await asyncio.to_thread(
            _RATE_LIMIT_STORE.check, client_id, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW)
    except RateLimitStoreError as exc:
        # R-149/R-160: the exception TYPE only — never the object (its message
        # could carry store details) in a runtime log.
        logger.error("rate-limit store unavailable — refusing (fail-closed): %s",
                    type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="rate limiter unavailable; retry later",
            headers={"Retry-After": "5"},
        )
    if not allowed:
        # R-164: tell the client WHEN — the oldest request in the window
        # leaves it after this many seconds (body unchanged: error_detail v1).
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s",
            headers={"Retry-After": str(wait)},
        )


def rate_limit(func: Callable) -> Callable:
    """Decorator for rate limiting endpoints."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        client_id = request.client.host if request.client else "unknown"
        await check_rate_limit(client_id)
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
# R-157 (D-069): /health judges this live civilization (read at call time, so a
# replaced CIVILIZATION is seen) instead of building a second default one.
set_civilization_provider(lambda: CIVILIZATION)
EXTERNAL_AGENTS: List[ExternalAgentAdapter] = load_external_agents_from_env()
TASKS: Dict[str, Dict[str, Any]] = {}

# R-162 (D-081): the agent registry — identity, worker and task are separate.
# Every civilization executor is an identity (agent_id = its unique name,
# version = the implementation); this process is ONE worker of pool "inproc"
# whose capacity (AGOS_WORKER_CAPACITY, default 1024) bounds the tasks running
# at once; each run holds a lease from pick to end. An invalid value is a
# startup error. External agents become identities "external:<name>" on first
# use and are disabled when removed.
WORKER_CAPACITY = agent_registry.positive_int_env("AGOS_WORKER_CAPACITY", 1024, 1, 100_000)
AGENT_REGISTRY = agent_registry.AgentRegistry()
agent_registry.register_civilization(
    AGENT_REGISTRY, CIVILIZATION, version=os.getenv("AGOS_AGENT_IMPL", "real").strip().lower() or "real",
    capabilities=SPECIALIZATION_TOOLS)
_WORKER_GENERATION = 0
WORKER_ID = ""


def _start_registry_worker() -> None:
    """Register this process as a ready worker unless the current one is
    still live in the table (ready, or draining on purpose). A stopped or
    dead worker is never revived (its leases were returned): it is removed
    from the table (no lease left, so no orphan) and a new generation gets a
    new unique id — the table stays bounded across restarts."""
    global WORKER_ID, _WORKER_GENERATION
    if WORKER_ID:
        try:
            if AGENT_REGISTRY.worker(WORKER_ID).state in ("starting", "ready", "draining"):
                return
            AGENT_REGISTRY.remove_worker(WORKER_ID)
        except agent_registry.UnknownWorker:
            pass
    WORKER_ID = f"inproc-{os.getpid()}-{_WORKER_GENERATION}"
    _WORKER_GENERATION += 1
    AGENT_REGISTRY.register_worker(WORKER_ID, "inproc", WORKER_CAPACITY)
    AGENT_REGISTRY.mark_ready(WORKER_ID)


_start_registry_worker()
WORKER_SATURATED = ("worker capacity saturated: the task was not started "
                    "(AGOS_WORKER_CAPACITY tasks are already running)")


def _identity_id(agent) -> str:
    if isinstance(agent, ExternalAgentAdapter):
        return f"external:{agent.name}"
    return agent_registry.executor_id(agent, getattr(agent, "specialization", "?"), id(agent))


def _ensure_identity(agent, specialization: str = "?") -> str:
    """The identity of an executor, registered on first use (external agents,
    or a civilization replaced at runtime) and re-enabled if it was disabled."""
    aid = _identity_id(agent)
    try:
        ident = AGENT_REGISTRY.agent(aid)
    except agent_registry.UnknownAgent:
        spec = getattr(agent, "specialization", None) or specialization
        AGENT_REGISTRY.register_agent(aid, aid, spec, version="external"
                                      if isinstance(agent, ExternalAgentAdapter) else "runtime")
        return aid
    if ident.state == "disabled":
        AGENT_REGISTRY.enable_agent(aid)
    return aid


def _registry_metrics() -> None:
    from agos_kernel.infrastructure.metrics import get_metrics
    snap = AGENT_REGISTRY.snapshot()
    m = get_metrics()
    m.set_gauge("registry_leases_active", snap["leases_active"])
    m.set_gauge("registry_agents", snap["agents"])
    m.set_gauge("registry_saturation", snap["saturation"] or 0.0)


def _stop_registry_worker() -> None:
    """Shutdown: stop this worker; every task still holding a lease ends
    ``failed`` with a fixed reason instead of staying ``running``."""
    try:
        orphans = AGENT_REGISTRY.stop(WORKER_ID, "worker stopped at shutdown")
    except agent_registry.RegistryError:
        return
    for tid in orphans.task_ids:
        rec = TASKS.get(tid) or {}
        _end_unfinished(tid, rec.get("specialization", "?"), "task cancelled before completion: worker stopped")
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
# SSE admission is SHARED across processes on this host (log 299 residual):
# one lease per subscription in a host-local SQLite file, renewed by an
# independent periodic loop, released on disconnect, expired by TTL when a
# worker dies. Loud init — a broken store must stop the server (admission
# never silently degrades to process-local caps).
SSE_ADMISSION = SSELeaseStore(_sse_admission_db_path())
EVENT_BUS = EventBus(SSE_LIMITS, on_metric=_sse_metric, admission=SSE_ADMISSION)


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

# R-163 (D-082) — durable task queue configuration.
# Lease: how long a claim owns a task before the reaper may recover it
# (worker death). Renewal: the reaper extends live leases every reap pass.
TASK_LEASE_S = max(1.0, float(os.environ.get("AGOS_TASK_LEASE_S", "60") or 60))
TASK_REAP_S = max(0.1, float(os.environ.get("AGOS_TASK_REAP_S", "5") or 5))
TASK_MAX_ATTEMPTS = agent_registry.positive_int_env("AGOS_TASK_MAX_ATTEMPTS", 3, 1, 100)
TASK_DISPATCH_BATCH = agent_registry.positive_int_env("AGOS_TASK_DISPATCH_BATCH", 50, 1, 1000)
TASK_DISPATCH_IDLE_S = max(0.05, float(os.environ.get("AGOS_TASK_DISPATCH_IDLE_S", "0.2") or 0.2))

# R-164 (D-083) — admission limits and back-off, in BOTH modes (volatile and
# durable). "Pending" = accepted but not finished (queued/claimed/running/...).
# - AGOS_TASK_QUEUE_MAX: pending tasks for the whole service (0 = no cap).
#   Full -> 503 + Retry-After (server capacity, names no other tenant). In
#   the volatile mode the effective cap is never above MAX_TASKS_KEPT — the
#   memory table cannot hold more in-flight records than it keeps (before
#   R-164 the 501st accepted task pruned an in-flight record).
# - AGOS_TASK_QUEUE_MAX_PER_TENANT: pending tasks per tenant (0 = no cap).
#   Full -> 429 + Retry-After: one tenant cannot fill the queue for the rest.
# - AGOS_TASK_ACCEPT_RATE_PER_MIN: accepted tasks per tenant per 60 s
#   (0 = off — a rate quota is a product/owner policy, not an invariant).
# - AGOS_TASK_RETRY_AFTER_S: the Retry-After sent with a full-queue answer.
# - AGOS_TASK_TIMEOUT_S: execution deadline of one run (0 = none). A hung
#   run used to hold its worker slot forever — and, durable, have its lease
#   renewed forever, so it was never recovered either.
# - AGOS_TASK_RETRY_BASE_S / _CAP_S: jittered back-off before a deferred
#   (worker capacity full) or recovered (lease expired) task is dispatched
#   again — was a fixed 2.0 s / immediate.
TASK_QUEUE_MAX = agent_registry.positive_int_env("AGOS_TASK_QUEUE_MAX", 10000, 0, 10_000_000)
TASK_QUEUE_MAX_PER_TENANT = agent_registry.positive_int_env("AGOS_TASK_QUEUE_MAX_PER_TENANT", 1000, 0, 10_000_000)
TASK_ACCEPT_RATE_PER_MIN = agent_registry.positive_int_env("AGOS_TASK_ACCEPT_RATE_PER_MIN", 0, 0, 1_000_000)
TASK_RETRY_AFTER_S = agent_registry.positive_int_env("AGOS_TASK_RETRY_AFTER_S", 5, 1, 3600)
TASK_TIMEOUT_S = backpressure._float_env("AGOS_TASK_TIMEOUT_S", 600.0, 0.0, 86400.0)
TASK_RETRY_BASE_S = backpressure._float_env("AGOS_TASK_RETRY_BASE_S", 1.0, 0.01, 600.0)
TASK_RETRY_CAP_S = backpressure._float_env("AGOS_TASK_RETRY_CAP_S", 30.0, 0.01, 3600.0)
backpressure.validate_env()     # an invalid AGOS_BREAKER_* fails the start

# R-188 (D-102): the durable queue is now the DEFAULT — active whenever
# storage is configured. R-163 (D-082) shipped it OPT-IN; the red test
# tests/test_r188_task_loss.py proved the default volatile path LOSES a task
# on process death (the storage row is frozen non-terminal forever — no
# recovery exists), so keeping the lossy path as the default was a silent
# data-loss default. AGOS_TASK_QUEUE=0 remains the explicit opt-out for
# deployments that want the volatile path and its documented limits (e.g.
# no storage configured at all — then there is nothing durable to activate
# and the flag is moot). Owner-directed activation per the R-188 decision.
# Limits: this does NOT resurrect rows already stuck before the activation
# (they carry no lease data); side-effect duplication on recovery is a
# separate open item; recovery time is not yet measured.
def _queue_enabled() -> bool:
    return os.environ.get("AGOS_TASK_QUEUE", "1") == "1" and supabase_configured()

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
    """Keep at most MAX_TASKS_KEPT records. R-164: finished records go first
    (oldest first) — before, the oldest record was dropped whatever its
    status, so an in-flight task could lose its record while still running.
    Only when finished records are not enough does the oldest in-flight one
    go (possible only for durable mirrors, whose truth is the stored row: the
    volatile admission cap keeps in-flight records <= MAX_TASKS_KEPT)."""
    excess = len(TASKS) - MAX_TASKS_KEPT
    if excess <= 0:
        return
    by_age = sorted(TASKS, key=lambda t: TASKS[t]["created_at"])
    done = [t for t in by_age if TASKS[t].get("status") in TERMINAL_TASK_STATES]
    for tid in done[:excess]:
        TASKS.pop(tid, None)
    excess = len(TASKS) - MAX_TASKS_KEPT
    if excess > 0:
        for tid in [t for t in by_age if t in TASKS][:excess]:
            TASKS.pop(tid, None)


def _overloaded(status: int, detail: str, retry_after: float) -> HTTPException:
    """R-164: a refusal that says when to come back (error_detail v1 body +
    ``Retry-After`` header)."""
    return HTTPException(status_code=status, detail=detail,
                         headers={"Retry-After": str(max(1, math.ceil(retry_after)))})


_ACCEPTED_AT: Dict[str, List[float]] = defaultdict(list)


def _reserve_accept_slot(tenant_id: str) -> Optional[float]:
    """R-164: AGOS_TASK_ACCEPT_RATE_PER_MIN accepted tasks per tenant per
    60 s (sliding window). Check AND reservation in one synchronous step —
    no ``await`` between them, so concurrent requests on the event loop
    cannot all pass the check before any of them is counted (R-164 review).
    Returns the reservation (``None`` when the limit is off); the caller must
    ``_release_accept_slot`` it unless the task is finally accepted. A
    refused request leaves no trace and consumes no quota."""
    if not TASK_ACCEPT_RATE_PER_MIN:
        return None
    now = time.monotonic()
    window = [t for t in _ACCEPTED_AT[tenant_id] if t > now - 60.0]
    _ACCEPTED_AT[tenant_id] = window
    if len(window) >= TASK_ACCEPT_RATE_PER_MIN:
        raise _overloaded(429, f"task acceptance rate exceeded for this tenant: max "
                               f"{TASK_ACCEPT_RATE_PER_MIN} per 60s (AGOS_TASK_ACCEPT_RATE_PER_MIN)",
                          window[0] + 60.0 - now)
    window.append(now)
    return now


def _release_accept_slot(tenant_id: str, slot: Optional[float]) -> None:
    """Give a reservation back: the task was not accepted (admission refused,
    storage failed, the request turned out to be a replay, or it was
    cancelled). Removes exactly one entry with that timestamp."""
    if slot is None:
        return
    try:
        _ACCEPTED_AT[tenant_id].remove(slot)
    except ValueError:
        pass                # already pruned out of the window


def _volatile_admission(tenant_id: str) -> None:
    """R-164: queue caps for the volatile mode, from the memory table."""
    pending = [t for t in TASKS.values() if t.get("status") not in TERMINAL_TASK_STATES]
    mine = sum(1 for t in pending if t.get("tenant_id") == tenant_id)
    if TASK_QUEUE_MAX_PER_TENANT and mine >= TASK_QUEUE_MAX_PER_TENANT:
        raise _overloaded(429, f"task queue full for this tenant: {mine} pending task(s) "
                               f"(AGOS_TASK_QUEUE_MAX_PER_TENANT={TASK_QUEUE_MAX_PER_TENANT}); retry later",
                          TASK_RETRY_AFTER_S)
    cap = min(TASK_QUEUE_MAX or MAX_TASKS_KEPT, MAX_TASKS_KEPT)
    if len(pending) >= cap:
        raise _overloaded(503, "task queue full: the service is at capacity; retry later",
                          TASK_RETRY_AFTER_S)


async def _civilization_rate_limit(request: Request) -> None:
    """SEC-08: the /civilization/* routes use the same per-client limiter as
    every /api/* route (``rate_limit``) — the shared store, awaited."""
    await check_rate_limit(request.client.host if request.client else "unknown")


def _civilization_admit(tenant_id: str) -> None:
    """SEC-08: /civilization/execute|coordinate run synchronously and keep an
    in-flight record in TASKS — they pass the same admission as a volatile
    POST /api/tasks (accept rate, per-tenant and global caps) before writing
    it. Keeping in-flight records <= MAX_TASKS_KEPT is also what guarantees the
    pruner never evicts a running record (R-164). The accept slot is given
    back when admission refuses (a refused request leaves no trace)."""
    slot = _reserve_accept_slot(tenant_id)
    try:
        _volatile_admission(tenant_id)
    except BaseException:
        _release_accept_slot(tenant_id, slot)
        raise


async def _durable_admission(storage, tenant_id: str) -> None:
    """R-164: queue caps for the durable mode, counted in the store (the
    truth across restarts). Each count reads at most cap rows. A store that
    cannot answer refuses the task (503) — it could not be stored either."""
    if TASK_QUEUE_MAX_PER_TENANT:
        mine = await storage.count_pending_tasks(tenant_id, TASK_QUEUE_MAX_PER_TENANT)
        if mine is None:
            raise _storage_unavailable("storage unavailable: the task was not accepted")
        if mine >= TASK_QUEUE_MAX_PER_TENANT:
            raise _overloaded(429, f"task queue full for this tenant: {mine} pending task(s) "
                                   f"(AGOS_TASK_QUEUE_MAX_PER_TENANT={TASK_QUEUE_MAX_PER_TENANT}); retry later",
                              TASK_RETRY_AFTER_S)
    if TASK_QUEUE_MAX:
        total = await storage.count_pending_tasks(ALL_TENANTS, TASK_QUEUE_MAX)
        if total is None:
            raise _storage_unavailable("storage unavailable: the task was not accepted")
        if total >= TASK_QUEUE_MAX:
            raise _overloaded(503, "task queue full: the service is at capacity; retry later",
                              TASK_RETRY_AFTER_S)


def _storage_unavailable(detail: str) -> HTTPException:
    """R-164: 503 for a store that did not answer, with the storage
    breaker's own wait when it is open (else the default retry hint)."""
    brk = backpressure.breaker("storage")
    wait = brk.retry_after() if brk.is_open() else TASK_RETRY_AFTER_S
    return _overloaded(503, detail, wait)


async def _execute_bounded(agent, payload: dict) -> dict:
    """R-164: one run under AGOS_TASK_TIMEOUT_S. On timeout the agent
    coroutine is cancelled (a pool thread already running finishes on its
    own; its result is discarded) and the run ends as a FAILED outcome —
    never retried: a run that started may have had side effects."""
    if not TASK_TIMEOUT_S:
        return await agent.execute(payload)
    try:
        return await asyncio.wait_for(agent.execute(payload), timeout=TASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        telemetry.event("task", "timeout", outcome="fail")
        return {"ok": False, "agent": getattr(agent, "name", None), "elapsed_s": round(TASK_TIMEOUT_S, 3),
                "result": {"error": f"task timed out after {TASK_TIMEOUT_S:g}s (AGOS_TASK_TIMEOUT_S)"}}


# R-160 (task lifecycle contract v1): strong references to the background runs.
# asyncio keeps only a weak reference to a task, so an un-referenced
# create_task() result may be garbage-collected before it finishes (asyncio
# docs). The set holds each run until it is done.
_BACKGROUND_TASKS: set = set()

TERMINAL_TASK_STATES = ("completed", "failed")

# R-163 (D-082): internal queue state per task — NEVER served on the API (the
# public record keeps its four-value status vocabulary; task_queue maps).
# state: task_queue.TaskState value; claim_token: the storage fence; attempts:
# executions that reached running; max_attempts: bound before dead-letter.
_QUEUE_STATE: Dict[str, Dict[str, Any]] = {}

# R-163: durable runs keyed by task_id (cancel needs the asyncio handle).
_RUNNING_RUNS: Dict[str, asyncio.Task] = {}

# R-163: idempotency for the volatile mode (no storage): (tenant, key) -> id.
# Durable mode relies on the storage unique index instead.
_VOLATILE_IDEMPOTENT: Dict[tuple, str] = {}

# R-163: dispatcher/reaper loops (durable mode only) and their stop flag.
_QUEUE_LOOPS: List[asyncio.Task] = []
_QUEUE_STOP = False


def _end_unfinished(task_id: str, specialization: str, reason: str) -> None:
    """R-160: a task record never stays queued/running after its run exits —
    on cancellation (shutdown) or an unexpected error it ends ``failed`` with a
    FIXED reason (no exception text). R-163: when the record was already moved
    to ``cancelled`` by the cancel endpoint, the outcome says "cancelled".
    Synchronous on purpose: it must also work
    while the loop is cancelling. Only the memory record and the in-process SSE
    bus are updated; a storage row already written as ``running`` is not
    rewritten here (no await during cancellation) — documented gap G-160-2."""
    rec = TASKS.get(task_id)
    if rec is None or rec.get("status") in TERMINAL_TASK_STATES:
        return
    prior = rec.get("result") if isinstance(rec.get("result"), dict) else None
    cancelled = bool(prior) and isinstance(prior.get("result"), dict) \
        and prior["result"].get("error") == "cancelled"
    outcome = prior if cancelled else {"ok": False, "agent": rec.get("agent"), "specialization": specialization,
                                      "elapsed_s": 0, "result": {"error": reason}}
    rec.update(status="failed", result=outcome, finished_at=time.time())
    tenant_id = rec.get("tenant_id")
    if tenant_id is not None:
        EVENT_BUS.publish(tenant_id, {"type": "task_completed", "task_id": task_id,
                                      "agent": rec.get("agent"), "specialization": specialization,
                                      "ok": False, "elapsed_s": 0})


# R-188 (log 302): ``update_task`` FAILS SOFT — it catches every storage
# error (transport, timeout, the storage circuit's ``CircuitOpen``) and
# returns False, and the terminal-write call sites used to IGNORE that
# return value. Proven by the 500-task volatile load test (this batch):
# under the completion storm the storage breaker opened (5 failures / 10s
# reset) and the terminal write of a few tasks failed — their rows stayed
# ``running`` FOREVER (the effect had happened and the record lost it:
# no retry, no reconciliation in the volatile mode; the durable queue has
# its own). The terminal write is now retried with a bounded jittered
# budget — a single transient failure (a breaker trip, a blip) is absorbed
# inline. The budget is NOT a guarantee against a sustained outage: the
# backoff sleeps alone span ~7.5-11.25s with the defaults, which straddles
# (not outlasts) the storage breaker's 10s reset, and the storage calls add
# their own latency. When the budget is exhausted (a SUSTAINED outage:
# reproduced under verified external saturation — log 302, round 3), the
# write is no longer ORPHANED: it is DEFERRED to an in-memory pending map
# drained by a background loop task that keeps retrying (a bounded batch per
# round, every TERMINAL_WRITE_DRAIN_INTERVAL_S) until the write lands — so a
# living server converges on its own rows once storage recovers, instead
# of leaving them ``running`` forever. Honest limits (unchanged in
# kind): the deferral is IN-MEMORY — a server restart still loses the
# pending writes (the volatile mode's documented durability bound; the
# durable queue is the no-loss guarantee). The pending map has NO
# configured memory bound: entries accumulate for as long as the outage
# lasts across the server's lifetime, and each entry carries the task's
# result payload — a sustained outage is an unbounded memory liability,
# bounded only by the number of terminal tasks (documented, not solved
# here). Reconciliation after recovery is eventual (round-by-round),
# never immediate, and says nothing about exactly-once execution. The
# failure is CRITICAL-logged at defer time, never silent. The
# intermediate ``running`` write is not
# retried: its failure self-heals at the terminal write.
TASK_TERMINAL_WRITE_ATTEMPTS = max(1, int(os.getenv(
    "AGOS_TASK_TERMINAL_WRITE_ATTEMPTS", "5")))
TASK_TERMINAL_WRITE_MAX_WAIT_S = max(0.0, float(os.getenv(
    "AGOS_TASK_TERMINAL_WRITE_MAX_WAIT_S", "4")))

# The deferral map + drainer (see the block comment above). The map is
# mutated and drained ONLY on the event loop (the defer happens inside
# _write_task_terminal, the drainer is a loop task), so a plain dict is
# safe without a lock. Keyed by task_id — a re-defer of the same task
# overwrites (a task is terminal exactly once; the payload is the
# terminal write's exact arguments).
TERMINAL_WRITE_PENDING: Dict[str, Dict[str, Any]] = {}
TERMINAL_WRITE_DRAIN_INTERVAL_S = max(0.05, float(os.getenv(
    "AGOS_TASK_TERMINAL_WRITE_DRAIN_INTERVAL_S", "5")))
TERMINAL_WRITE_DRAIN_BATCH = max(1, int(os.getenv(
    "AGOS_TASK_TERMINAL_WRITE_DRAIN_BATCH", "25")))
_TERMINAL_WRITE_DRAIN_TASK: Optional[asyncio.Task] = None


async def _drain_terminal_writes() -> None:
    """Retry the DEFERRED terminal writes until they land (or the loop
    dies with the server). One bounded batch per round, attempted
    CONCURRENTLY (a saturated storage times out at ~30s per call — a
    serial round of 25 could take 12 minutes; concurrent, the round is
    bounded by the slowest call). Per-write failures are contained:
    an exception in ONE write is caught and logged for that write alone
    (the entry stays pending) — it must never end reconciliation for the
    rest; a failure ANYWHERE in a round restarts the loop after an
    interval instead of exiting, so a living server keeps reconciling
    while entries exist. FAIRNESS: a failed entry is moved to the BACK
    of the map for the next round — a persistently-failing head of the
    queue cannot starve the entries behind it. The task ends when the
    map is empty — it is not a forever-idle loop; the next defer starts
    a fresh one."""
    while True:
        try:
            while TERMINAL_WRITE_PENDING:
                await asyncio.sleep(TERMINAL_WRITE_DRAIN_INTERVAL_S)
                batch = list(TERMINAL_WRITE_PENDING.items())[:TERMINAL_WRITE_DRAIN_BATCH]

                async def _one(task_id: str, payload: Dict[str, Any]) -> bool:
                    try:
                        landed = await get_storage().update_task(
                            task_id, payload["status"], **payload["kw"])
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # one bad write must not end reconciliation for the
                        # others — the entry stays pending and is retried.
                        # R-149 redaction: the TYPE only, never the raw
                        # message or traceback (arbitrary adapter text is
                        # not ours to forward to the log)
                        logger.error(
                            "task %s deferred terminal write (%s) raised %s "
                            "during a drain round; it stays pending", task_id,
                            payload["status"], type(e).__name__)
                        landed = False
                    if landed:
                        # only remove OUR entry: if a NEWER deferral replaced
                        # it while this attempt was in flight, that payload
                        # is the one that must land — leave it untouched
                        if TERMINAL_WRITE_PENDING.get(task_id) is payload:
                            TERMINAL_WRITE_PENDING.pop(task_id, None)
                            logger.info(
                                "task %s terminal write (%s) landed via the "
                                "background drainer", task_id, payload["status"])
                        return True
                    # failed (False or raised): move to the BACK so a
                    # persistently-failing head cannot starve the rest —
                    # but never touch a NEWER deferral that replaced this
                    # entry while the attempt was in flight
                    if TERMINAL_WRITE_PENDING.get(task_id) is payload:
                        TERMINAL_WRITE_PENDING.pop(task_id, None)
                        TERMINAL_WRITE_PENDING[task_id] = payload
                    return False

                await asyncio.gather(*(
                    _one(task_id, payload) for task_id, payload in batch))
            return  # map empty — the drainer ends (the next defer restarts it)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # a round-level crash must never take the loop down NOR end
            # reconciliation: log it (the TYPE only — R-149 redaction) and
            # keep reconciling while entries exist (the map is the source
            # of truth, not this task)
            logger.error(
                "terminal-write drainer round failed (%s); the pending "
                "writes stay deferred and the drainer keeps retrying",
                type(e).__name__)
            await asyncio.sleep(TERMINAL_WRITE_DRAIN_INTERVAL_S)


def _ensure_terminal_write_drainer() -> None:
    """Start the drainer loop task if it is not already running (lazily —
    from the event loop; _write_task_terminal is always loop-side).
    A drainer is only "alive" for THIS loop: a task abandoned on a closed
    loop (tests run each app in its own loop) is never done() and would
    otherwise block a fresh drainer forever — so the loop identity, not
    just done(), decides."""
    global _TERMINAL_WRITE_DRAIN_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (e.g. import time) — the next defer retries
    t = _TERMINAL_WRITE_DRAIN_TASK
    if t is not None and not t.done() and t.get_loop() is loop:
        return
    _TERMINAL_WRITE_DRAIN_TASK = loop.create_task(_drain_terminal_writes())


async def _write_task_terminal(task_id: str, status: str, *, agent_name=None,
                               result=None, elapsed_s=None) -> bool:
    """Persist a task's TERMINAL state with a bounded, jittered retry (see
    the block comment above for why). Returns True when the row is written;
    False means the budget was exhausted — the write is then DEFERRED to the
    background drainer (retried until it lands or the server restarts),
    CRITICAL-logged by name: visible, never silent, never orphaned while
    the server lives."""
    delay = 0.5
    for attempt in range(1, TASK_TERMINAL_WRITE_ATTEMPTS + 1):
        try:
            if await get_storage().update_task(
                    task_id, status, agent_name=agent_name, result=result,
                    elapsed_s=elapsed_s):
                if attempt > 1:
                    logger.info("task %s terminal write (%s) succeeded on attempt %d",
                                task_id, status, attempt)
                return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # a storage adapter that RAISES (a code bug — the contract is a
            # soft False) must not escape and orphan the row: count the
            # attempt, log it (the TYPE only — R-149 redaction), and keep
            # the budget
            logger.error(
                "task %s terminal write (%s) RAISED %s on attempt %d; the "
                "attempt is spent and the budget continues", task_id, status,
                type(e).__name__, attempt)
        if attempt >= TASK_TERMINAL_WRITE_ATTEMPTS:
            break
        await asyncio.sleep(delay + random.uniform(0, delay * 0.5))
        delay = min(delay * 2, TASK_TERMINAL_WRITE_MAX_WAIT_S)
    # Sustained outage past the in-line budget: DEFER instead of orphan —
    # the drainer keeps retrying this exact write until it lands (or the
    # server restarts, which loses the in-memory deferral: the volatile
    # mode's documented durability bound — the durable queue is the
    # no-loss guarantee).
    TERMINAL_WRITE_PENDING[task_id] = {
        "status": status,
        "kw": {"agent_name": agent_name, "result": result,
               "elapsed_s": elapsed_s},
    }
    _ensure_terminal_write_drainer()
    logger.critical(
        "task %s terminal state %s was NOT persisted after %d attempts — "
        "DEFERRED to the background drainer (retried every %.1fs until it "
        "lands or the server restarts; a restart loses the in-memory "
        "deferral — the volatile mode's documented durability bound, the "
        "durable queue is the no-loss guarantee)",
        task_id, status, TASK_TERMINAL_WRITE_ATTEMPTS,
        TERMINAL_WRITE_DRAIN_INTERVAL_S)
    return False


async def _run_task(task_id: str, specialization: str, prompt: str, data: Any):
    # R-159: the background run keeps the trace of the request that created it
    # (asyncio copies the context) and is one `task` span bound to task_id.
    telemetry.bind_task(task_id)
    try:
        with telemetry.span("task", "run", specialization=specialization) as sp:
            await _run_task_traced(sp, task_id, specialization, prompt, data)
    except asyncio.CancelledError:
        _end_unfinished(task_id, specialization, "task cancelled before completion")
        raise
    except Exception as e:  # noqa: BLE001 — the run must end the record, not vanish
        logger.error("Task %s run failed: %s", task_id, type(e).__name__)
        _end_unfinished(task_id, specialization, "task failed: internal error")
    finally:
        # R-162: the lease ends with the run, whatever its outcome
        if AGENT_REGISTRY.lease(task_id) is not None:
            AGENT_REGISTRY.release(task_id)
        _registry_metrics()


async def _run_task_traced(sp, task_id: str, specialization: str, prompt: str, data: Any):
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
        sp.set(outcome="fail")
        storage = get_storage()
        await _write_task_terminal(task_id, "failed", result=outcome, elapsed_s=0)
        await storage.insert_event("task_completed", {"task_id": task_id, "agent": None,
                                                      "specialization": specialization, "ok": False},
                                   tenant_id=tenant_id)
        await _publish(tenant_id, {"type": "task_completed", "task_id": task_id, "agent": None,
                                   "specialization": specialization, "ok": False, "elapsed_s": 0})
        return
    # R-162: a lease binds this task to the agent identity on this worker.
    # At capacity the task ends failed with a fixed reason — never silently
    # queued (waiting/backpressure are R-163/R-164).
    try:
        # the process keeps serving after a lifespan shutdown (e.g. an embedding
        # app or tests): a task that starts then gets a NEW worker generation —
        # never the stopped one, whose tasks were already ended as orphans.
        _start_registry_worker()
        AGENT_REGISTRY.heartbeat(WORKER_ID)   # in-process: alive while it runs code
        AGENT_REGISTRY.acquire(task_id, _ensure_identity(agent, specialization), worker_id=WORKER_ID)
    except agent_registry.RegistryError as e:
        try:
            ready = AGENT_REGISTRY.worker(WORKER_ID).state == "ready"
        except agent_registry.RegistryError:
            ready = False
        reason = WORKER_SATURATED if isinstance(e, agent_registry.Saturated) and ready else "worker not available"
        outcome = {"ok": False, "agent": None, "specialization": specialization, "elapsed_s": 0,
                   "result": {"error": reason}}
        TASKS[task_id].update(status="failed", result=outcome, finished_at=time.time())
        sp.set(outcome="fail")
        storage = get_storage()
        await _write_task_terminal(task_id, "failed", result=outcome, elapsed_s=0)
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

    outcome = await _execute_bounded(agent, {"prompt": prompt, "data": data})   # R-164: deadline
    if TASKS[task_id].get("status") in TERMINAL_TASK_STATES or AGENT_REGISTRY.lease(task_id) is None:
        # R-162: the lease ended while the agent ran (worker stopped/removed):
        # the record was already ended as an orphan; a late result never
        # resurrects it.
        logger.warning("Task %s late result discarded: its lease had ended", task_id)
        sp.set(outcome="orphaned")
        return
    TASKS[task_id]["status"] = "completed" if outcome["ok"] else "failed"
    TASKS[task_id]["result"] = outcome
    TASKS[task_id]["finished_at"] = time.time()
    sp.set(outcome="ok" if outcome["ok"] else "fail")

    await _write_task_terminal(task_id, TASKS[task_id]["status"],
                              agent_name=agent.name, result=outcome,
                              elapsed_s=outcome.get("elapsed_s", 0))
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


# ============================================================================
# R-163 (D-082) — DURABLE TASK QUEUE: dispatcher, reaper, fenced execution
# ============================================================================
# Only active when storage is configured (durable mode). Without storage the
# volatile path above is unchanged and its limits are documented (no restart
# recovery without a durable store — see OPERATIONS.md).

_QUEUE_BACKOFF: Dict[str, float] = {}
# R-164: how many times a task was handed back for capacity (back-off growth)
_QUEUE_DEFERRALS: Dict[str, int] = {}
# R-164: volatile runs by task id — so the cancel endpoint can cancel a
# RUNNING volatile task too (before, only its record moved to cancelled and
# the agent kept running to the end).
_VOLATILE_RUNS: Dict[str, asyncio.Task] = {}


def _lease_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=TASK_LEASE_S)).isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _release_registry_lease(task_id: str) -> None:
    """R-162: the registry lease ends with the run, whatever its outcome."""
    if AGENT_REGISTRY.lease(task_id) is not None:
        AGENT_REGISTRY.release(task_id)


def _rebuild_task_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure the in-memory record exists for a durable task — rebuilt from
    the storage row after a restart or a claim of a task whose creator's
    process is gone. The served ``status`` is the public mapping (D-082)."""
    tid = str(row.get("task_id"))
    if tid in TASKS:
        return TASKS[tid]
    created = row.get("created_at")
    try:
        created_s = datetime.fromisoformat(str(created)).timestamp() if created else time.time()
    except ValueError:
        created_s = time.time()
    rec = TASKS.setdefault(tid, {
        "id": tid,
        "specialization": row.get("specialization"),
        "prompt": (row.get("prompt") or "")[:500],
        "status": task_queue.public_status(row.get("status") or "queued"),
        "created_at": created_s,
        "result": row.get("result"),
        "tenant_id": row.get("tenant_id"),
    })
    _prune_tasks()
    return rec


async def _finish_durable(task_id: str, specialization: str, claim_token: str,
                          outcome: Dict[str, Any], sp, agent=None) -> None:
    """End a durable task: memory record first (visible even if the store is
    down), then the FENCED storage write (only the claim owner can end it),
    then events/SSE — and a bounded reconciliation retry when the store itself
    failed (no silent loss: the record stays terminal in memory and the write
    is retried before the lease is given up)."""
    rec = TASKS.get(task_id)
    status = "completed" if outcome.get("ok") else "failed"
    if rec is not None:
        rec.update(status=status, result=outcome, finished_at=time.time())
    sp.set(outcome="ok" if outcome.get("ok") else "fail")
    storage = get_storage()
    confirmed = await storage.finish_task(task_id, claim_token, status,
                                          agent_name=agent.name if agent is not None else None,
                                          result=outcome, elapsed_s=outcome.get("elapsed_s", 0))
    if confirmed is None:
        # store failure (not a lost claim): reconcile with bounded retries —
        # while the lease is ours the reaper will not recover the row
        for _ in range(3):
            await asyncio.sleep(0.5)
            confirmed = await storage.finish_task(task_id, claim_token, status,
                                                  agent_name=agent.name if agent is not None else None,
                                                  result=outcome, elapsed_s=outcome.get("elapsed_s", 0))
            if confirmed is not False:
                break
    _QUEUE_STATE.pop(task_id, None)
    _QUEUE_DEFERRALS.pop(task_id, None)
    _QUEUE_BACKOFF.pop(task_id, None)
    if confirmed is not True:
        # 0 rows matched: the claim is not ours anymore (recovered/cancelled)
        # — a late result is discarded, never resurrected (R-162 semantics)
        logger.warning("Task %s result discarded: claim no longer held", task_id)
        sp.set(outcome="orphaned")
        return
    if agent is not None:
        await storage.upsert_agent_stats(
            agent.name, specialization,
            agent.stats["tasks_completed"], agent.stats["tasks_failed"], agent.stats["total_time_s"]
        )
    tenant_id = rec.get("tenant_id") if rec is not None else None
    await storage.insert_event("task_completed", {
        "task_id": task_id, "agent": agent.name if agent is not None else None,
        "specialization": specialization, "ok": bool(outcome.get("ok"))
    }, tenant_id=tenant_id)
    if tenant_id is not None:
        await _publish(tenant_id, {
            "type": "task_completed", "task_id": task_id,
            "agent": agent.name if agent is not None else None,
            "specialization": specialization, "ok": bool(outcome.get("ok")),
            "elapsed_s": outcome.get("elapsed_s", 0),
        })


async def _defer(task_id: str, claim_token: str, sp, storage, rec,
                  reason: str) -> None:
    """R-188 recipient stability: an EXPLICIT wait — the claim goes back
    WITHOUT consuming an attempt (the pin checks run while the row is still
    `claimed`, so the release really happens), the task stays cancellable,
    and the reason is observable (the record's ``wait_reason`` + a log
    line). The wait is UNBOUNDED by design: a pinned recipient that was
    removed or rebound never silently reroutes to another recipient. The
    deferral uses the same jittered, growing back-off as the capacity path
    (R-164).

    The record (and the queue state) is touched ONLY when the release is
    CONFIRMED: an unconfirmed release means the claim was lost (a newer
    claim or a CANCELLATION may own the row now) — touching the record
    then could resurrect a cancelled task's public state or clobber a newer
    claim's state, so nothing is modified beyond the log and the span."""
    confirmed = await storage.release_task(task_id, claim_token, "queued") is True
    if not confirmed:
        logger.warning("Task %s deferral not confirmed by the store; the record "
                      "is left untouched (a newer claim or a cancellation may own the row)",
                       task_id)
        sp.set(outcome="deferred")
        return
    n = _QUEUE_DEFERRALS[task_id] = _QUEUE_DEFERRALS.get(task_id, 0) + 1
    _QUEUE_BACKOFF[task_id] = time.time() + backpressure.backoff_delay(
        n, TASK_RETRY_BASE_S, TASK_RETRY_CAP_S)
    _QUEUE_STATE.pop(task_id, None)
    if rec is not None:
        rec["status"] = "queued"
        rec["wait_reason"] = reason
    logger.info("Task %s deferred: %s", task_id, reason)
    sp.set(outcome="deferred")


async def _defer_capacity(task_id: str, claim_token: str, sp, storage, rec) -> None:
    """R-164: the registry is at capacity — give the claim back WITHOUT
    consuming an execution attempt (the row is still ``claimed``, so the
    release really happens), with the same jittered, growing back-off as
    the recipient-wait path.

    R-188 review round 2: the SAME confirmed-release rule as ``_defer`` — the
    counters, the back-off, the queue state and the record are touched ONLY
    when the release is CONFIRMED. An unconfirmed release means the claim was
    lost (a newer claim or a CANCELLATION may own the row now); touching any
    of these could resurrect a cancelled task's public state or clobber a
    newer claim's back-off, so nothing is modified beyond the log and the
    span."""
    if await storage.release_task(task_id, claim_token, "queued") is not True:
        logger.warning("Task %s capacity deferral not confirmed by the store; "
                      "the record and the queue state are left untouched "
                      "(a newer claim or a cancellation may own the row)", task_id)
        sp.set(outcome="deferred")
        return
    n = _QUEUE_DEFERRALS[task_id] = _QUEUE_DEFERRALS.get(task_id, 0) + 1
    _QUEUE_BACKOFF[task_id] = time.time() + backpressure.backoff_delay(
        n, TASK_RETRY_BASE_S, TASK_RETRY_CAP_S)
    _QUEUE_STATE.pop(task_id, None)
    if rec is not None:
        rec["status"] = "queued"
    sp.set(outcome="deferred")


async def _run_task_durable_traced(sp, task_id: str, specialization: str, prompt: str,
                                  data: Any, claim_token: str,
                                  row_payload: Any = None) -> None:
    """The durable execution path (R-163). Differences from the volatile path:

    - SEC-06: the dispatcher's context carries NO request consent — the
      original request's ``allow_public_llm`` does not survive into it, so an
      agent needing the public (keyless) LLM fails closed with the fixed
      no-consent reason (documented D-082 limit: recovered/re-dispatched tasks
      never resurrect consent).
    - The storage row is the fenced source of truth: claimed -> running
      (attempt counted) -> terminal, all conditional on the claim token.
    - Registry saturation gives the claim back (queued, no attempt) instead
      of failing the task — the dispatcher retries when capacity frees.
    """
    info = _QUEUE_STATE.get(task_id) or {}
    rec = TASKS.get(task_id)
    tenant_id = info.get("tenant_id") or (rec or {}).get("tenant_id")
    if tenant_id is None:
        sp.set(outcome="fail")
        return
    tenancy.set_tenant(tenant_id)
    storage = get_storage()
    attempts = int(info.get("attempts") or 0) + 1

    async def _start() -> bool:
        if not await storage.start_task(task_id, claim_token, attempts):
            # the claim is gone (recovered/cancelled) or the store is down —
            # the task must NOT run: the reaper/owner will decide its fate
            logger.warning("Task %s not started: claim not held", task_id)
            _QUEUE_STATE.pop(task_id, None)
            sp.set(outcome="orphaned")
            return False
        info["state"], info["attempts"] = "running", attempts
        if rec is not None:
            rec["status"] = "running"
        return True

    # R-164: the worker slot is taken BEFORE the row moves to running. Before
    # R-164 the row was already running (attempt counted) when the registry
    # said Saturated, so release_task (claimed -> queued only) silently
    # failed: the "deferred" task waited out a whole lease, lost an attempt
    # and could dead-letter without ever running.
    # R-188 recipient stability (a redelivery must reach the SAME external
    # recipient): the pin travels in the row's payload, so it survives the
    # coordinator's death. Read BEFORE the general selection and BEFORE the
    # first external send — a pinned task never enters the no-agent failure
    # path: it waits for ITS recipient.
    pin_state, pin = _durable_recipient(row_payload)
    agent = None
    if pin_state == "invalid" or (pin_state == "valid" and specialization != "external"):
        # an invalid (or non-external) pin stops the run EXPLICITLY — it is
        # never silently treated as "no pin" (that would pick another
        # recipient and duplicate the effect)
        await _defer(task_id, claim_token, sp, storage, rec, "recipient_pinned_invalid")
        return
    if pin_state == "valid":
        pinned = next((a for a in EXTERNAL_AGENTS if a.name == pin["name"]), None)
        if pinned is None:
            # explicit WAIT (no attempt consumed, cancellable): the recipient
            # was removed or disabled — never a silent move to another one
            await _defer(task_id, claim_token, sp, storage, rec, "recipient_pinned_absent")
            return
        if (_pin_endpoint(pinned.url) != pin["endpoint"]
                or _pin_fingerprint(pinned.url) != pin["fingerprint"]):
            # the name exists but its DESTINATION changed (a same-origin path
            # change included — two paths may be different services with
            # independent idempotency stores): a CONFLICT, never a permission
            # to send to the new destination
            await _defer(task_id, claim_token, sp, storage, rec, "recipient_pinned_conflict")
            return
        agent = pinned
    else:
        try:
            agent = _pick_agent(specialization)
        except HTTPException as e:
            if not await _start():
                return
            reason = NO_EXTERNAL_AGENT if specialization == "external" else str(e.detail)
            await _finish_durable(task_id, specialization, claim_token,
                                  {"ok": False, "agent": None, "specialization": specialization,
                                   "elapsed_s": 0, "result": {"error": reason}}, sp, agent=None)
            return
    if (pin_state == "absent" and specialization == "external" and agent is not None
            and isinstance(row_payload, dict) and "_agos_prompt" in row_payload):
        # first dispatch: pin the chosen recipient BEFORE the first send. The
        # write is fenced by the current claim (guard + representation + the
        # no-rebind filter) — if it does not durably land, the run sends
        # NOTHING: an unpinned redelivery could reach a different recipient
        # that never saw the idempotency key and re-apply the effect.
        endpoint = _pin_endpoint(agent.url)
        if endpoint is None:
            # FAIL-CLOSED: the destination does not parse to a clean public
            # form (never the raw URL, never an unverifiable destination) —
            # no pin, no send
            await _defer(task_id, claim_token, sp, storage, rec, "recipient_pin_unsaved")
            return
        merged = dict(row_payload)
        merged["_agos_recipient"] = {"name": agent.name, "endpoint": endpoint,
                                     "fingerprint": _pin_fingerprint(agent.url)}
        pinned_ok = await storage.pin_task_recipient(task_id, claim_token, merged)
        if pinned_ok is not True:
            await _defer(task_id, claim_token, sp, storage, rec, "recipient_pin_unsaved")
            return
        # a foreign payload (no internal envelope) is never pinned: it is
        # outside the internal envelope's guarantees (documented limit)
    try:
        _start_registry_worker()
        AGENT_REGISTRY.heartbeat(WORKER_ID)
        AGENT_REGISTRY.acquire(task_id, _ensure_identity(agent, specialization), worker_id=WORKER_ID)
    except agent_registry.RegistryError as e:
        if isinstance(e, agent_registry.Saturated):
            # capacity: give the claim back without consuming an attempt —
            # the row is still `claimed`, so the release really happens
            await _defer_capacity(task_id, claim_token, sp, storage, rec)
            return
        if not await _start():
            return
        await _finish_durable(task_id, specialization, claim_token,
                              {"ok": False, "agent": None, "specialization": specialization,
                               "elapsed_s": 0, "result": {"error": "worker not available"}}, sp, agent=None)
        return
    if not await _start():
        _release_registry_lease(task_id)
        return
    if rec is not None:
        rec["agent"] = agent.name
        rec.pop("wait_reason", None)  # R-188: the wait ended — the run is live
    await _publish(tenant_id, {"type": "task_started", "task_id": task_id, "agent": agent.name,
                               "specialization": specialization})
    # R-188 criterion (b) — PARTIAL, CONDITIONAL: the task's identity travels
    # with the execution; the external adapter sends it as the idempotency
    # key. An at-least-once redelivery cannot re-apply the effect AT A
    # RECIPIENT THAT HONORS the key (the standard Idempotency-Key
    # convention): the delivery may repeat, the application does not. A
    # recipient that ignores the key stays at-least-once (contract limit —
    # documented in log 302; not claimed beyond it). The key is the task_id
    # (stable across attempts by design), never the claim token or the
    # attempt number (both differ across processes). Recipient stability
    # across processes is NOT guaranteed (see the honest limits) — UNTIL the
    # R-188 pin: since this batch the first dispatch pins the recipient
    # (name + endpoint binding) in the row BEFORE the first send, and a
    # redelivery honors the pin (same recipient, same key — the effect is
    # not re-applied there). A recipient that ignores the key stays
    # at-least-once (contract limit — unchanged).
    outcome = await _execute_bounded(agent, {"prompt": prompt, "data": data,
                                            "task_id": task_id})   # R-164: deadline
    if (TASKS.get(task_id) or {}).get("status") in TERMINAL_TASK_STATES \
            or AGENT_REGISTRY.lease(task_id) is None:
        logger.warning("Task %s late result discarded: its lease had ended", task_id)
        sp.set(outcome="orphaned")
        return
    await _finish_durable(task_id, specialization, claim_token, outcome, sp, agent=agent)


async def _run_task_durable(task_id: str, specialization: str, prompt: str,
                           data: Any, claim_token: str,
                           trace_id: Optional[str] = None,
                           row_payload: Any = None) -> None:
    if trace_id:
        # R-159/R-188: the run CONTINUES the accept request's trace (stored
        # in the row's payload) — the request and the run are ONE trace.
        telemetry.adopt_trace(trace_id, task_id)
    else:
        # R-159/R-188: no valid internal trace to continue — start a FRESH
        # trace. Never inherit the dispatcher's ambient context: the loops
        # can be started from a REQUEST context via the self-healing start,
        # and a leaked ambient trace would silently join unrelated requests
        # to this run. Legacy rows (no trace_id) get their own trace as before.
        telemetry.start_trace(task_id)
    try:
        with telemetry.span("task", "run", specialization=specialization) as sp:
            await _run_task_durable_traced(sp, task_id, specialization, prompt, data,
                                           claim_token, row_payload=row_payload)
    except asyncio.CancelledError:
        # the run was cancelled (endpoint or shutdown): the caller already
        # moved the record and the row to cancelled — only release the lease
        rec = TASKS.get(task_id)
        if rec is not None and rec.get("status") not in TERMINAL_TASK_STATES:
            rec.update(status="failed", result=task_queue.cancelled_outcome(rec.get("agent"), specialization),
                       finished_at=time.time())
        raise
    except Exception as e:  # noqa: BLE001 — the run must end the record, not vanish
        logger.error("Task %s durable run failed: %s", task_id, type(e).__name__)
        rec = TASKS.get(task_id)
        if rec is not None and rec.get("status") not in TERMINAL_TASK_STATES:
            rec.update(status="failed",
                       result={"ok": False, "agent": rec.get("agent"), "specialization": specialization,
                               "elapsed_s": 0, "result": {"error": "task failed: internal error"}},
                       finished_at=time.time())
    finally:
        _RUNNING_RUNS.pop(task_id, None)
        _release_registry_lease(task_id)
        _registry_metrics()


def _durable_payload(prompt: str, data: Any) -> Dict[str, Any]:
    """R-163: the durable row is the execution source of truth, and
    insert_task stores only ``prompt[:500]`` — so the FULL prompt travels
    inside the payload under the reserved ``_agos_prompt`` key (the dispatcher
    unwraps it). Without this, a durable task with a prompt longer than 500
    chars would execute on a truncated input — silent data loss, not a logging
    bound.

    R-159/R-188: the accept request's trace_id travels too, under the
    reserved ``_agos_trace_id`` key — the dispatcher adopts it before running
    (``_durable_trace_id``), so the request and the run stay ONE trace
    across the queue boundary."""
    payload: Dict[str, Any] = {"_agos_prompt": prompt, "data": data}
    ctx = telemetry.current()
    if ctx is not None:
        payload["_agos_trace_id"] = ctx.trace_id
    return payload


_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")  # telemetry.new_trace_id(): token_hex(16)
# R-188: the recipient pin binds the agent's NAME (any non-empty string the
# registration contract accepts — a plain pydantic str with no pattern; the
# pin's validation mirrors THAT contract, not telemetry's redaction
# pattern, so a legally registered name is always pinnable) plus the
# destination's PUBLIC identity (see _pin_endpoint).
_RECIPIENT_NAME_MAX = 200
# R-188 review round 4: the fingerprint is a KEYED HMAC — the key is a
# DEPLOYMENT SECRET, never a literal in the source. A committed default
# key is public to everyone who can read the repository, and "keyed" against
# a publicly known key is not keyed at all: anyone could recompute the
# fingerprint of a guessed URL (credentials included) offline against the
# pin, which is readable through the task API. The key therefore comes from
# the deployment's own secret material, in order:
#   1. AGOS_PIN_FINGERPRINT_KEY (env) — the explicit deployment secret. A
#      multi-host deployment MUST set the SAME value on every host (a pin is
#      only as stable as the key is shared; a diverging key surfaces existing
#      pins as recipient_pinned_conflict — fail-safe, documented).
#   2. AGOS_PIN_FINGERPRINT_KEY_FILE (env) or, by default, a host-local file
#      created with a fresh random key on first use (mode 0600) OUTSIDE the
#      source tree. It is stable across processes and restarts on the same
#      host — the host-local recovery scope. If the file is lost (an
#      ephemeral filesystem), existing pins surface as conflicts — fail-safe:
#      the operator re-registers the recipient (a new pin) or sets the env
#      var to a stable value.
# If neither source is available (a read-only location and no env var), the
# server refuses to START with a loud error rather than pin tasks with an
# unverifiable key (fail-closed, like the rest of the pin path).
def _load_pin_fingerprint_key() -> bytes:
    env_key = os.environ.get("AGOS_PIN_FINGERPRINT_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")
    path = os.environ.get("AGOS_PIN_FINGERPRINT_KEY_FILE", "").strip() or os.path.join(
        tempfile.gettempdir(), "agos_pin_fingerprint_key")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if data:
            return data
        raise RuntimeError(
            f"AGOS pin fingerprint key file {path!r} is empty — remove it or set "
            "AGOS_PIN_FINGERPRINT_KEY")
    except FileNotFoundError:
        pass  # first use on this host — create it below
    except OSError as e:
        raise RuntimeError(
            f"AGOS pin fingerprint key file {path!r} is unreadable ({type(e).__name__}) — "
            "set AGOS_PIN_FINGERPRINT_KEY or AGOS_PIN_FINGERPRINT_KEY_FILE") from e
    fresh = secrets.token_bytes(32)  # never a source literal — random per host
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # another process (a second worker) won the creation race — read what
        # it wrote (the SAME host-local key — the point of the file)
        with open(path, "rb") as fh:
            data = fh.read()
        if data:
            return data
        raise RuntimeError(
            f"AGOS pin fingerprint key file {path!r} was created empty by a "
            "concurrent process — remove it or set AGOS_PIN_FINGERPRINT_KEY")
    except OSError as e:
        raise RuntimeError(
            f"AGOS pin fingerprint key: cannot create {path!r} ({type(e).__name__}) — "
            "set AGOS_PIN_FINGERPRINT_KEY or AGOS_PIN_FINGERPRINT_KEY_FILE to a "
            "writable secret location outside the source tree") from e
    with os.fdopen(fd, "wb") as fh:
        fh.write(fresh)
        fh.flush()
        os.fsync(fh.fileno())
    return fresh


# loaded ONCE at startup (fail-closed: an unavailable key source is a loud
# STARTUP error, never a silent pin with an unverifiable key). Tests may
# monkeypatch this cached value; the loader stays pure for direct testing.
_PIN_FINGERPRINT_KEY: bytes = _load_pin_fingerprint_key()
# R-188: the pin binds the recipient's PUBLIC identity only: name + the
# URL's public form + a NON-REVEALING keyed fingerprint of the FULL
# destination. An API key is never pinned, and neither is the raw URL (it
# may carry credentials in userinfo/query/path — R-159 — and the payload is
# readable through the task API).
_PIN_KEYS = frozenset({"name", "endpoint", "fingerprint"})


def _durable_trace_id(row: Dict[str, Any]) -> Optional[str]:
    """The accept request's trace_id from the row's payload. Accepted ONLY
    from the KNOWN INTERNAL ENVELOPE that ``_durable_payload`` writes (its
    reserved ``_agos_prompt`` key is always present — a raw/foreign payload
    that merely matches the id format is rejected) AND in the internal
    trace-id format (telemetry.new_trace_id = token_hex(16) → 32 lowercase
    hex chars). A foreign or sensitive value under the reserved key is
    ignored and the run starts its own trace — payload data must never
    become an emitted trace_id (R-159's no-leak guarantee). None for legacy
    rows with raw-data payloads."""
    payload = row.get("payload")
    if isinstance(payload, dict) and "_agos_prompt" in payload:
        t = payload.get("_agos_trace_id")
        if isinstance(t, str) and _TRACE_ID_RE.fullmatch(t):
            return t
    return None


def _pin_endpoint(url: str) -> Optional[str]:
    """The pin's destination identity: the URL's PUBLIC form —
    scheme://host[:port], SEC-07's normalization of the external agents' url.
    The RAW URL may carry credentials in userinfo/query/path (R-159) and the
    payload is readable through the task API, so the pin NEVER stores the raw
    URL. The PUBLIC FORM alone does not prove the destination's identity
    (two paths on the same origin may be different services with independent
    idempotency stores) — that is what the fingerprint (``_pin_fingerprint``)
    is for; the public form is kept for observability.

    FAIL-CLOSED: any parse problem (an invalid IPv6 literal, a non-integer
    port, a missing host, a non-http(s) scheme) answers None — the raw URL
    is NEVER returned, and the caller defers without pinning (zero external
    sends) rather than store an unverifiable destination."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return None  # fail-closed — never the raw URL
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname  # userinfo is NOT part of the public identity
    if port is not None:  # an explicit port survives
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    else:
        netloc = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{netloc}"


def _pin_fingerprint(url: str) -> str:
    """The pin's destination PROOF: a keyed fingerprint (HMAC-SHA256) of the
    FULL raw destination URL — path and query included, because two paths on
    the same origin may be different destination services with independent
    idempotency stores, and a destination change must surface as a CONFLICT,
    not as "the same recipient".

    KEYED, not bare: the pin (and the task's payload) is readable through the
    task API — a bare hash of a URL that carries credentials would let
    anyone test password guesses offline against that payload. The HMAC key
    is a DEPLOYMENT SECRET (see ``_load_pin_fingerprint_key`` — the env var
    or a host-local key file, never a source literal): an attacker who can
    read the payload but not the deployment's secret material cannot verify
    a guess against the fingerprint. The key is stable across processes and
    restarts on the same host (recovery); a multi-host deployment must set
    the SAME env value on every host. The raw URL (credentials included) is
    still never stored.

    The fingerprint still does not "recover" the credentials — it proves
    destination identity without storing the raw URL."""
    return hmac.new(_PIN_FINGERPRINT_KEY, url.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _durable_recipient(payload: Any) -> tuple:
    """R-188 recipient stability: the pinned external recipient, accepted
    ONLY from the KNOWN INTERNAL ENVELOPE that ``_durable_payload`` writes
    (its reserved ``_agos_prompt`` key must be present — a raw/foreign
    payload is never treated as carrying a pin). Returns a (state, pin)
    tuple:

    - ``("absent", None)`` — no pin: the first dispatch picks the
      recipient and PINS it (via ``storage.pin_task_recipient``) before the
      first external send;
    - ``("valid", {"name", "endpoint", "fingerprint"})`` — a redelivery
      must honor this binding: same name AND same destination (the URL's
      public form AND its fingerprint — a same-origin path change is a
      destination change). A name whose destination changed is a CONFLICT
      (defer, never a permission to send elsewhere);
    - ``("invalid", None)`` — a pin is present but malformed (a bad
      structure, an unparsable endpoint, a path/query/fragment/userinfo in
      the endpoint, a bad fingerprint): the run stops EXPLICITLY (defer,
      zero sends) instead of silently treating it as absent and picking
      another recipient. FAIL-CLOSED: an endpoint that does not PARSE
      cleanly is invalid — the raw value is never echoed or honored.

    The pin binds the recipient's public identity only (name + the URL's
    public form + a KEYED fingerprint of the full destination — HMAC-SHA256
    with a fixed key that is never exposed through the task API; a bare hash
    would let anyone who can read the pin test credential guesses offline);
    an API key is never pinned, and neither is the raw URL. The name is validated
    against the REGISTRATION contract (any non-empty string ≤
    _RECIPIENT_NAME_MAX — the registration endpoint enforces the SAME
    contract), not telemetry's redaction pattern. A pin on a non-external
    row is not honored either — the system only pins external recipients."""
    if not (isinstance(payload, dict) and "_agos_prompt" in payload
            and "_agos_recipient" in payload):
        return ("absent", None)
    pin = payload.get("_agos_recipient")
    if not (isinstance(pin, dict) and set(pin) == _PIN_KEYS
            and isinstance(pin["name"], str) and pin["name"]
            and len(pin["name"]) <= _RECIPIENT_NAME_MAX):
        return ("invalid", None)
    # FAIL-CLOSED parsing: an endpoint that raises or does not parse to a
    # clean scheme://host[:port] (no path/query/fragment/userinfo) is
    # invalid — never honored, never echoed
    try:
        ep = urllib.parse.urlsplit(pin["endpoint"]) \
            if isinstance(pin["endpoint"], str) else None
        if ep is not None:
            ep.port  # a malformed port raises ValueError -> invalid (fail-closed)
    except ValueError:
        ep = None
    endpoint_ok = bool(
        isinstance(pin["endpoint"], str) and ep is not None
        and ep.scheme in ("http", "https") and ep.netloc and not ep.path
        and not ep.query and not ep.fragment
        and ep.username is None and ep.password is None
        and len(pin["endpoint"]) <= 2048)
    fingerprint_ok = bool(
        isinstance(pin.get("fingerprint"), str)
        and re.fullmatch(r"[0-9a-f]{64}", pin["fingerprint"]))
    if not (endpoint_ok and fingerprint_ok):
        return ("invalid", None)
    return ("valid", {"name": pin["name"], "endpoint": pin["endpoint"],
                      "fingerprint": pin["fingerprint"]})


def _unwrap_durable_payload(row: Dict[str, Any]) -> tuple:
    """-> (full prompt, data). Rows written before this wrapping (or by the
    storage tests) carry the raw data as payload — they fall back to the
    stored (truncated) prompt."""
    payload = row.get("payload")
    if isinstance(payload, dict) and "_agos_prompt" in payload:
        wrapped = dict(payload)
        prompt = str(wrapped.pop("_agos_prompt"))
        return prompt, wrapped.get("data")
    return str(row.get("prompt") or ""), payload


async def _queue_dispatcher() -> None:
    """Claims queued rows (fair across tenants: fewest in-flight first, oldest
    within a tenant) and runs them. Durable mode only."""
    storage = get_storage()
    logger.info("R-163 durable queue dispatcher started (lease=%ss, reap=%ss)", TASK_LEASE_S, TASK_REAP_S)
    while not _QUEUE_STOP:
        brk = backpressure.breaker("storage")
        if brk.is_open():
            # R-164: the store is failing — do not poll it every idle tick;
            # come back when the breaker allows a probe (bounded pause).
            await asyncio.sleep(min(5.0, max(TASK_DISPATCH_IDLE_S, brk.retry_after())))
            continue
        try:
            if len(_RUNNING_RUNS) >= WORKER_CAPACITY:
                await asyncio.sleep(TASK_DISPATCH_IDLE_S)
                continue
            rows = await _scan_queued(storage)
        except Exception as e:  # noqa: BLE001 — a failed scan must not kill the loop
            logger.warning("queue dispatcher scan failed: %s", type(e).__name__)
            rows = None
        if not rows:
            await asyncio.sleep(TASK_DISPATCH_IDLE_S)
            continue
        inflight = _inflight_by_tenant()
        now = time.time()
        claimed_any = False
        for row in _fair_order(rows, inflight):
            if _QUEUE_STOP or len(_RUNNING_RUNS) >= WORKER_CAPACITY:
                break       # R-164: capacity is checked per claim, not per pass
            tid = str(row.get("task_id"))
            if tid in _RUNNING_RUNS or _QUEUE_BACKOFF.get(tid, 0) > now:
                continue
            if _QUEUE_STATE.get(tid, {}).get("state") in ("claimed", "running"):
                continue
            token = uuid.uuid4().hex
            try:
                claimed = await storage.claim_task(tid, token, _lease_deadline())
            except Exception as e:  # noqa: BLE001
                logger.warning("claim of %s failed: %s", tid, type(e).__name__)
                claimed = None
            if claimed is None:
                continue
            _QUEUE_STATE[tid] = {
                "task_id": tid, "tenant_id": claimed.get("tenant_id"),
                "state": "claimed", "claim_token": token,
                "attempts": int(claimed.get("attempts") or 0),
                "max_attempts": int(claimed.get("max_attempts") or TASK_MAX_ATTEMPTS),
            }
            _rebuild_task_record(claimed)
            full_prompt, data = _unwrap_durable_payload(claimed)
            run = asyncio.create_task(_run_task_durable(tid, str(claimed.get("specialization")),
                                                        full_prompt, data, token,
                                                        trace_id=_durable_trace_id(claimed),
                                                        row_payload=claimed.get("payload")))
            _RUNNING_RUNS[tid] = run
            _BACKGROUND_TASKS.add(run)  # R-160: keep a strong reference
            run.add_done_callback(_BACKGROUND_TASKS.discard)
            run.add_done_callback(lambda _r, tid=tid: _RUNNING_RUNS.pop(tid, None))
            inflight[str(claimed.get("tenant_id"))] = inflight.get(str(claimed.get("tenant_id")), 0) + 1
            claimed_any = True
        if not claimed_any:
            # every visible row is backing off / already ours: wait a tick
            # instead of re-scanning in a hot loop
            await asyncio.sleep(TASK_DISPATCH_IDLE_S)


def _inflight_by_tenant() -> Dict[str, int]:
    inflight: Dict[str, int] = defaultdict(int)
    for info in _QUEUE_STATE.values():
        if info.get("state") in ("claimed", "running"):
            inflight[str(info.get("tenant_id"))] += 1
    return inflight


async def _scan_queued(storage) -> Optional[List[Dict[str, Any]]]:
    """R-164 fairness: one scan returns the OLDEST ``TASK_DISPATCH_BATCH``
    queued rows — so before R-164 a tenant with a backlog deeper than one
    batch filled every batch, and a newer tenant's task waited until that
    whole backlog drained (the in-batch "fewest in-flight first" order cannot
    help a row that is not in the batch). When the batch is full there may be
    hidden tenants: a second scan EXCLUDES every tenant already seen, and the
    dispatcher then interleaves tenants (``_fair_order``). One extra read,
    only when a batch comes back full."""
    now = time.time()
    for tid in [t for t, until in _QUEUE_BACKOFF.items() if until <= now]:
        _QUEUE_BACKOFF.pop(tid, None)          # bounded: expired entries go
    rows = await storage.list_queued_tasks(TASK_DISPATCH_BATCH)
    if not rows or len(rows) < TASK_DISPATCH_BATCH:
        return rows
    seen_tenants = {str(r.get("tenant_id")) for r in rows} - {"None"}
    if not seen_tenants:
        return rows
    others = await storage.list_queued_tasks(TASK_DISPATCH_BATCH, exclude_tenants=sorted(seen_tenants)[:100])
    if not others:
        return rows
    seen = {str(r.get("task_id")) for r in rows}
    return list(rows) + [r for r in others if str(r.get("task_id")) not in seen]


def _fair_order(rows: List[Dict[str, Any]], inflight: Dict[str, int]) -> List[Dict[str, Any]]:
    """R-164: round-robin across tenants — a row's turn is its tenant's
    in-flight count plus its rank among that tenant's candidates (oldest
    first); ties go to the older row. Tenant A's 2nd row comes after every
    other tenant's 1st, whatever A's backlog depth."""
    rank: Dict[str, int] = defaultdict(int)
    keyed = []
    for r in sorted(rows, key=lambda r: str(r.get("created_at") or "")):
        t = str(r.get("tenant_id"))
        keyed.append(((inflight.get(t, 0) + rank[t], str(r.get("created_at") or "")), r))
        rank[t] += 1
    return [r for _, r in sorted(keyed, key=lambda kr: kr[0])]


async def _queue_reaper() -> None:
    """Renews the lease of every live run (heartbeat) and recovers rows whose
    lease expired (worker death / restart). Each recovery is ONE atomic
    conditional transition — claimed/running -> queued directly, or ->
    dead_letter once the attempts budget is exhausted — so a crash mid-recovery
    can never strand a row in an intermediate state."""
    storage = get_storage()
    logger.info("R-163 durable queue reaper started")
    while not _QUEUE_STOP:
        try:
            for tid, info in list(_QUEUE_STATE.items()):
                if tid in _RUNNING_RUNS and info.get("state") in ("claimed", "running"):
                    try:
                        await storage.renew_task_lease(tid, str(info.get("claim_token")), _lease_deadline())
                    except Exception as e:  # noqa: BLE001
                        logger.warning("lease renewal of %s failed: %s", tid, type(e).__name__)
            now_iso = _now_iso()
            rows = await storage.list_expired_lease_tasks(TASK_DISPATCH_BATCH)
            for row in rows or []:
                tid = str(row.get("task_id"))
                if tid in _RUNNING_RUNS:
                    continue  # live here and renewed above — never steal
                attempts = int(row.get("attempts") or 0)
                max_attempts = int(row.get("max_attempts") or TASK_MAX_ATTEMPTS)
                after = task_queue.outcome_after_expiry(attempts, max_attempts)
                rec = TASKS.get(tid)
                outcome = task_queue.dead_letter_outcome(
                    rec.get("agent") if rec else None,
                    str(row.get("specialization") or ""), attempts, max_attempts)
                # ONE atomic conditional transition per row (no retrying
                # intermediate): a crash between two writes can never strand
                # the row — it is either recovered in this single PATCH or left
                # as-is for the next cycle.
                if after is task_queue.TaskState.DEAD_LETTER:
                    moved = await storage.transition_task(tid, ("claimed", "running"), "dead_letter",
                                                          lease_expired_before=now_iso,
                                                          result=outcome, elapsed_s=0)
                else:
                    moved = await storage.transition_task(tid, ("claimed", "running"), "queued",
                                                          lease_expired_before=now_iso)
                if not moved:
                    continue
                _QUEUE_STATE.pop(tid, None)
                if after is not task_queue.TaskState.DEAD_LETTER:
                    # R-164: bounded, jittered back-off before the next
                    # attempt (was immediate: a task that kills its worker
                    # was re-run at once, max_attempts times in a row)
                    _QUEUE_BACKOFF[tid] = time.time() + backpressure.backoff_delay(
                        max(1, attempts), TASK_RETRY_BASE_S, TASK_RETRY_CAP_S)
                else:
                    _QUEUE_BACKOFF.pop(tid, None)
                    _QUEUE_DEFERRALS.pop(tid, None)
                    telemetry.event("task", "dead_letter", outcome="fail")
                if rec is not None and rec.get("status") not in TERMINAL_TASK_STATES:
                    if after is task_queue.TaskState.DEAD_LETTER:
                        rec.update(status="failed", result=outcome, finished_at=time.time())
                    else:
                        rec.update(status="queued", result=None, finished_at=None)
                if after is task_queue.TaskState.DEAD_LETTER:
                    tenant_id = row.get("tenant_id")
                    if tenant_id is not None:
                        await storage.insert_event("task_completed", {
                            "task_id": tid, "agent": None,
                            "specialization": row.get("specialization"), "ok": False,
                        }, tenant_id=tenant_id)
                        await _publish(tenant_id, {"type": "task_completed", "task_id": tid,
                                                   "agent": None, "specialization": row.get("specialization"),
                                                   "ok": False, "elapsed_s": 0})
        except Exception as e:  # noqa: BLE001 — a failed pass must not kill the loop
            logger.warning("queue reaper pass failed: %s", type(e).__name__)
        await asyncio.sleep(TASK_REAP_S)


def _start_queue_loops() -> None:
    """Durable mode: dispatcher + reaper, and one immediate recovery pass is
    simply the reaper's first pass (expired leases from a dead process)."""
    global _QUEUE_STOP
    if not _queue_enabled() or _QUEUE_LOOPS:
        return
    _QUEUE_STOP = False
    _QUEUE_LOOPS.append(asyncio.create_task(_queue_dispatcher()))
    _QUEUE_LOOPS.append(asyncio.create_task(_queue_reaper()))


def _stop_queue_loops() -> None:
    global _QUEUE_STOP
    _QUEUE_STOP = True
    for loop in _QUEUE_LOOPS:
        loop.cancel()
    _QUEUE_LOOPS.clear()


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
    # R-163 (D-082): optional idempotency key — one task per (tenant, key). A
    # duplicate POST with the same key replays the existing task (no second
    # run, no second side effect). Max 128 chars; ignored when empty.
    idempotency_key: Optional[str] = Field(None, max_length=128,
                                           description="Idempotency key: a duplicate request replays the same task")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    # SEC-06 (R-145): same explicit per-request consent as TaskRequest.
    allow_public_llm: bool = Field(False, strict=True,
                                  description="Consent to send this message's text to the external public LLM provider")


class ExternalAgentRequest(BaseModel):
    # R-188 review round 2: the name contract is UNIFIED with the recipient
    # pin's validation (a non-empty string of at most 200 characters) — a
    # legally registered name is always pinnable, and a pinned name can never
    # be something registration would refuse (which would turn a legal task's
    # pin "invalid" at recovery). 422 outside the range.
    name: str = Field(..., min_length=1, max_length=200)
    url: str
    model: str = ""
    api_key: str = ""


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api")
async def api_root(request: Request):
    """Root API endpoint - returns API information."""
    await check_rate_limit(request.client.host if request.client else "unknown")
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
        # R-157 (D-069): the rule is shared with /health's civilization check
        # (civilization_status), so both views judge the same live object.
        st = civilization_status(CIVILIZATION)
        if not st["ready"]:
            return not_ready(st["reason"])

        return {
            "status": "ready",
            "agents": st["agents"],
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

        # R-164 (D-083): backpressure — per-pool load, per-service breakers
        # (state 0 closed / 1 half-open / 2 open) and pending tasks in memory.
        for name, ld in pools.load().items():
            metrics.set_gauge("pool_threads", ld["size"], {"pool": name})
            metrics.set_gauge("pool_waiting", ld["waiting"], {"pool": name})
            metrics.set_gauge("pool_busy_rejections", ld["busy_rejections"], {"pool": name})
        for name, b in backpressure.snapshot().items():
            metrics.set_gauge("breaker_state", backpressure.STATE_CODE[b["state"]], {"service": name})
            metrics.set_gauge("breaker_opened", b["opened"], {"service": name})
            metrics.set_gauge("breaker_rejected", b["rejected"], {"service": name})
            # R-164 review: outcomes ignored because the call was admitted
            # before the breaker changed state (late successes after a
            # Retry-After hold, non-probe answers in half-open)
            metrics.set_gauge("breaker_stale_outcomes", b["stale"], {"service": name})
        metrics.set_gauge("tasks_pending", sum(1 for t in TASKS.values()
                                               if t.get("status") not in TERMINAL_TASK_STATES))
        
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
    # R-163 (D-082) — durable mode: the storage row is the source of truth. The
    # task is persisted BEFORE it is accepted (503 when the store is down: no
    # silent loss — an unaccepted task has no side effect at all), and the
    # dispatcher (not this request) claims and runs it. The request's consent
    # context does NOT follow the task into the dispatcher (SEC-06 fail-closed,
    # D-082): a durable run that needs the public LLM fails with the fixed
    # no-consent reason instead of resurrecting consent.
    if supabase_configured() and _queue_enabled():
        # R-188: self-healing start — the loops normally start in the app
        # lifespan, but if storage was configured AFTER the app started (tests
        # monkeypatching, late config) the durable accept would insert a row
        # no dispatcher ever claims. Idempotent: a no-op when already running.
        _start_queue_loops()
        storage = get_storage()
        idem = (req.idempotency_key or "").strip() or None
        if idem is not None:
            existing = await storage.find_task_by_idempotency_key(idem, scope=principal.tenant_id)
            if existing is not None:
                # idempotent replay: the SAME task, its CURRENT public status,
                # and no second run or side effect. R-159: the replay is
                # attributed to the EXISTING task — the newly generated id is
                # discarded here and never bound to the trace.
                telemetry.bind_task(str(existing.get("task_id")))
                _rebuild_task_record(existing)
                return {"task_id": str(existing.get("task_id")),
                        "status": task_queue.public_status(existing.get("status") or "queued"),
                        "duplicate": True}
        # R-159: bind the NEW task to the accept trace only AFTER the replay
        # exclusion (a replay binds the existing task instead) and BEFORE any
        # storage write — the admission/insert spans carry the task_id (the
        # same ordering as the volatile path), so the whole accept trace is
        # this task's.
        telemetry.bind_task(task_id)
        # R-164: admission AFTER the idempotent replay (a replay is never
        # refused — it creates nothing) and BEFORE anything is written. The
        # rate slot is reserved before the first await and given back on
        # every path that does not accept a NEW task (R-164 review).
        slot = _reserve_accept_slot(principal.tenant_id)
        accepted = False
        try:
            await _durable_admission(storage, principal.tenant_id)
            persisted = await storage.insert_task(
                task_id, req.specialization, req.prompt, "queued",
                tenant_id=principal.tenant_id, payload=_durable_payload(req.prompt, req.data),
                idempotency_key=idem, max_attempts=TASK_MAX_ATTEMPTS)
            if not persisted:
                # the unique (tenant, key) index may have raced us: replay it
                # (a replay consumes no quota — the finally gives it back)
                if idem is not None:
                    existing = await storage.find_task_by_idempotency_key(idem, scope=principal.tenant_id)
                    if existing is not None:
                        _rebuild_task_record(existing)
                        return {"task_id": str(existing.get("task_id")),
                                "status": task_queue.public_status(existing.get("status") or "queued"),
                                "duplicate": True}
                raise _storage_unavailable("storage unavailable: the task was not accepted")
            accepted = True     # the row exists: the slot is spent from here on
        finally:
            if not accepted:
                _release_accept_slot(principal.tenant_id, slot)
        TASKS[task_id] = {
            "id": task_id,
            "specialization": req.specialization,
            "prompt": req.prompt[:500],
            "status": "queued",
            "created_at": time.time(),
            "result": None,
            "tenant_id": principal.tenant_id,
        }
        _QUEUE_STATE[task_id] = {"task_id": task_id, "tenant_id": principal.tenant_id,
                                "state": "queued", "claim_token": None,
                                "attempts": 0, "max_attempts": TASK_MAX_ATTEMPTS}
        _prune_tasks()
        telemetry.event("task", "queued", specialization=req.specialization)
        await storage.insert_event("task_created", {"task_id": task_id, "specialization": req.specialization},
                                   tenant_id=principal.tenant_id)
        await _publish(principal.tenant_id, {"type": "task_created", "task_id": task_id,
                                             "specialization": req.specialization})
        logger.info(f"Task created: {task_id} for specialization: {req.specialization} (durable)")
        return {"task_id": task_id, "status": "queued"}
    # volatile mode (no storage): the pre-R-163 behaviour — direct execution,
    # the request's consent follows the run, and restart recovery is not
    # possible (documented limit, OPERATIONS.md). Idempotency is in-memory.
    idem = (req.idempotency_key or "").strip() or None
    if idem is not None:
        replay = _VOLATILE_IDEMPOTENT.get((principal.tenant_id, idem))
        if replay is not None and replay in TASKS:
            return {"task_id": replay, "status": TASKS[replay]["status"], "duplicate": True}
    # R-164: admission after the replay check, before any side effect (a
    # refused task leaves no record, idempotency entry, row, event or run).
    slot = _reserve_accept_slot(principal.tenant_id)
    try:
        _volatile_admission(principal.tenant_id)
    except BaseException:
        _release_accept_slot(principal.tenant_id, slot)
        raise
    if idem is not None:
        _VOLATILE_IDEMPOTENT[(principal.tenant_id, idem)] = task_id
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
    telemetry.bind_task(task_id)
    telemetry.event("task", "queued", specialization=req.specialization)
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
        run = asyncio.create_task(_run_task(task_id, req.specialization, req.prompt, req.data))
        _BACKGROUND_TASKS.add(run)              # R-160: keep a strong reference
        run.add_done_callback(_BACKGROUND_TASKS.discard)
        _VOLATILE_RUNS[task_id] = run           # R-164: cancellable while running
        run.add_done_callback(lambda _r, tid=task_id: _VOLATILE_RUNS.pop(tid, None))
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
            # R-163: serve the public status mapping for internal queue states
            for row in db_tasks:
                if row.get("status") not in ("queued", "running", "completed", "failed"):
                    row["status"] = task_queue.public_status(row.get("status") or "queued")
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
            # key) and ``source`` — no other field is added or invented.
            # R-163: internal queue states (claimed/retrying/...) are served
            # through the public mapping (D-082) — the API vocabulary is intact
            row = dict(row)
            if row.get("status") not in ("queued", "running", "completed", "failed"):
                row["status"] = task_queue.public_status(row.get("status") or "queued")
            return {**row, "id": row["task_id"], "source": "database"}
    raise HTTPException(404, "task not found")


@app.post("/api/tasks/{task_id}/cancel")
@rate_limit
async def cancel_task(request: Request, task_id: str,
                      api_key: Optional[str] = Security(api_key_header)):
    """R-163: cancel a task of the caller's tenant (SEC-02: another tenant's
    task answers exactly like a missing one — 404). Terminal tasks answer 409
    (the record is kept as it ended). Non-terminal: the record and the durable
    row move to ``cancelled`` (served as ``failed`` with result error
    "cancelled" — D-082 public mapping), a live run is cancelled, and the fenced
    write guarantees a late result can never resurrect it."""
    principal = require_principal(api_key)
    scope = principal.tenant_id  # never all_tenants: no admin cross-tenant cancel
    rec = TASKS.get(task_id)
    row = None
    if rec is not None and not _visible_task(rec, scope):
        # SEC-02: another tenant's task answers exactly like a missing one
        raise HTTPException(404, "task not found")
    if rec is None and supabase_configured():
        row = await get_storage().get_task(task_id, scope=scope)
    if rec is None and row is None:
        raise HTTPException(404, "task not found")
    current = (rec or row or {}).get("status")
    try:
        internal = task_queue.state(current)
    except ValueError:
        internal = task_queue.TaskState.QUEUED  # unknown status: treat as pending
    if task_queue.is_terminal(internal):
        raise HTTPException(409, f"task already finished: {task_queue.public_status(internal)}")
    if rec is None:
        rec = _rebuild_task_record(row or {"task_id": task_id, "status": "cancelled",
                                           "tenant_id": scope})
    outcome = task_queue.cancelled_outcome(rec.get("agent"), rec.get("specialization") or "")
    storage = get_storage()
    if supabase_configured():
        # R-188: the outcome and the finish time travel WITH the guarded
        # transition (same atomic CAS) — before, the row carried only the
        # bare `cancelled` status, so a FRESH process rebuilding the record
        # from the row served `failed` with NO result and NO finished_at:
        # the public representation (D-082: failed + error "cancelled") was
        # stable only in the cancelling process and lost across a restart.
        moved = await storage.transition_task(
            task_id, ("accepted", "queued", "claimed", "running", "waiting", "retrying"),
            "cancelled", result=outcome, finished_at="now()")
        if moved is None:
            # the request itself failed (store down): the cancel is NOT
            # confirmed — the row may be re-dispatched later. Answering 409
            # here would claim a finish that never happened.
            raise _storage_unavailable("storage unavailable: cancel not confirmed — the task may still run")
        if moved is False:
            # someone else finished/recovered it between the read and this write
            raise HTTPException(409, "task already finished")
    rec.update(status="failed", result=outcome, finished_at=time.time())
    _QUEUE_STATE.pop(task_id, None)
    _QUEUE_BACKOFF.pop(task_id, None)
    _QUEUE_DEFERRALS.pop(task_id, None)
    # R-164: cancel propagates to the live run in BOTH modes (the volatile
    # run used to keep executing after its record said "cancelled"). A pool
    # thread already inside a blocking call finishes on its own; its result
    # is discarded — the record is terminal and is never resurrected.
    for runs in (_RUNNING_RUNS, _VOLATILE_RUNS):
        run = runs.get(task_id)
        if run is not None and not run.done():
            run.cancel()
    tenant_id = rec.get("tenant_id")
    if tenant_id is not None:
        await storage.insert_event("task_completed", {
            "task_id": task_id, "agent": rec.get("agent"),
            "specialization": rec.get("specialization"), "ok": False,
        }, tenant_id=tenant_id)
        await _publish(tenant_id, {"type": "task_completed", "task_id": task_id,
                                  "agent": rec.get("agent"), "specialization": rec.get("specialization"),
                                  "ok": False, "elapsed_s": 0})
    return {"task_id": task_id, "status": "failed", "cancelled": True, "result": outcome}


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
    except SSEAdmissionError:
        # fail-closed: the shared admission store is unavailable — admission
        # is REFUSED (503), never degraded to process-local caps.
        logger.warning("sse: admission store unavailable — refusing (503)")
        raise HTTPException(503, "admission temporarily unavailable",
                             headers={"Retry-After": "5"})

    def release() -> None:
        sub.bus.close(sub)  # the bus it was opened on (idempotent; frees the lease)

    def _lease_lost() -> None:
        # Confirmed lease loss (or an outage we can no longer bridge): the
        # stream must END — capacity is never held on an unprovable lease.
        # The wake event unblocks a generator parked in sub.next() so it
        # notices sub.closed and returns without yielding anything new.
        sub.bus.close(sub)
        try:
            sub.offer({"type": "_lease_wake"})
        except Exception:  # pragma: no cover — best-effort wake
            pass

    async def generate():
        renew = None
        if sub.lease_id is not None:
            # INDEPENDENT periodic renewal (must not depend on heartbeat
            # output: heartbeats may not fire under a continuous stream of
            # events or a stalled reader).
            renew = asyncio.create_task(_sse_renewal_loop(
                sub.lease_id, SSE_ADMISSION, SSE_LIMITS.lease_ttl_s,
                SSE_LIMITS.lease_renewal_s, on_loss=_lease_lost))
        try:
            while True:
                if sub.closed:
                    return  # lease lost / stream closed — end before yielding
                left = sub.bus.lifetime_left(sub)
                if left <= 0:
                    return  # max lifetime: end the stream; the client reconnects
                event = await sub.next(timeout=min(sub.bus.limits.heartbeat_s, left))
                if sub.closed:
                    return  # lease lost while parked in next()
                if event is None:
                    if sub.bus.lifetime_left(sub) <= 0:
                        return
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        finally:
            if renew is not None:
                renew.cancel()
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
        outcome = await _execute_bounded(agent, {"prompt": req.message})   # R-164: deadline
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
    # R-159: the chat's task_id exists only after execution; the agent/tool/
    # memory/llm spans above share this request's trace_id, the storage spans
    # below also carry the task_id.
    telemetry.bind_task(task_id)
    telemetry.event("task", "recorded", outcome="ok" if outcome["ok"] else "fail", specialization=spec)

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
    # R-159: the URL may carry userinfo/query credentials — log scheme+host only.
    _u = urllib.parse.urlsplit(req.url)
    logger.warning("External agent registered: %s at %s://%s", req.name, _u.scheme, _u.hostname or "?")
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
            try:   # R-162: no new lease for the removed agent; running ones finish
                AGENT_REGISTRY.disable_agent(_identity_id(a))
            except agent_registry.UnknownAgent:
                pass
            logger.warning(f"External agent removed: {name}")
            return {"removed": name}
    raise HTTPException(404, "external agent not found")


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Civilization auto-routing endpoints (no user-picked specialization)
# ---------------------------------------------------------------------------
try:
    from agent_civilization.civilization_routes import register_civilization_routes
    register_civilization_routes(app, CIVILIZATION, TASKS, _prune_tasks, verify_api_key, require_principal,
                                 rate_limiter=_civilization_rate_limit, admit=_civilization_admit)
    logger.info("Civilization routes registered successfully")
except ImportError as e:
    logger.warning("Civilization routes not available: %s (%s)", type(e).__name__, e.name)  # R-160 (G-159-1)

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
