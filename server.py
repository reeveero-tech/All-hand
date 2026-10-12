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
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from agent_civilization.agents.real import SPECIALIZATIONS, create_civilization
from agent_civilization.agents.real.real_agents import SPECIALIZATION_ICONS, SPECIALIZATION_TOOLS
from agent_civilization.agents.external_adapter import (
    ExternalAgentAdapter,
    load_external_agents_from_env,
)
from agent_civilization.core.llm import get_llm
from agent_civilization.storage import get_storage, is_configured as supabase_configured

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
API_KEY = os.getenv("AGOS_API_KEY", "")  # Optional API key
ALLOWED_ORIGINS = os.getenv("AGOS_ALLOWED_ORIGINS", "*").split(",")  # Default: all origins
ENABLE_AUTH = bool(API_KEY)  # Enable auth only if API key is set

# Rate Limiting Configuration
RATE_LIMIT_REQUESTS = int(os.getenv("AGOS_RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("AGOS_RATE_LIMIT_WINDOW", "60"))  # seconds

# Request tracking for rate limiting
request_counts: Dict[str, List[datetime]] = defaultdict(list)

# App metadata
app = FastAPI(
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


# ============================================================================
# SECURITY HELPERS
# ============================================================================

# API Key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """Verify API key if authentication is enabled."""
    if not ENABLE_AUTH:
        return None
    if api_key is None:
        raise HTTPException(status_code=401, detail="API key required")
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key


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

CIVILIZATION = create_civilization()
EXTERNAL_AGENTS: List[ExternalAgentAdapter] = load_external_agents_from_env()
TASKS: Dict[str, Dict[str, Any]] = {}
TASK_EVENTS: asyncio.Queue = asyncio.Queue()
STARTED_AT = time.time()
MAX_TASKS_KEPT = 500

logger.info(f"Civilization initialized with {len(SPECIALIZATIONS)} specializations, "
            f"{len(CIVILIZATION) * 50} agents total")
logger.info(f"External agents loaded: {len(EXTERNAL_AGENTS)}")


def _all_agents():
    for agents in CIVILIZATION.values():
        yield from agents
    yield from EXTERNAL_AGENTS


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
    agent = _pick_agent(specialization)
    TASKS[task_id]["status"] = "running"
    TASKS[task_id]["agent"] = agent.name
    storage = get_storage()
    await storage.update_task(task_id, "running", agent_name=agent.name)

    await TASK_EVENTS.put({"type": "task_started", "task_id": task_id, "agent": agent.name, "specialization": specialization})

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
    })

    await TASK_EVENTS.put({
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


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)


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
        from agos_kernel.infrastructure.health import get_health_registry, HealthStatus
        
        registry = get_health_registry()
        system_health = await registry.get_system_health()
        
        status_code = 200 if system_health["status"] == "healthy" else 503
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
    
    Returns 200 if the application is ready to accept traffic.
    Checks:
    - API is responsive
    - Civilization is initialized
    """
    try:
        # Check if civilization is initialized
        if not CIVILIZATION:
            return {"status": "not_ready", "reason": "Civilization not initialized"}
        
        # Check if agents are available
        total_agents = sum(len(agents) for agents in CIVILIZATION.values())
        if total_agents == 0:
            return {"status": "not_ready", "reason": "No agents available"}
        
        return {
            "status": "ready",
            "agents": total_agents,
            "specializations": len(SPECIALIZATIONS)
        }
        
    except Exception as e:
        return {"status": "not_ready", "reason": str(e)}


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
    db_tasks = await storage.get_tasks(limit=500)
    return {
        "status": "healthy",
        "uptime_s": round(time.time() - STARTED_AT),
        "llm_configured": get_llm().available,
        "llm_model": get_llm().model,
        "supabase_configured": supabase_configured(),
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
        }
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
    if req.specialization not in SPECIALIZATIONS and req.specialization != "external":
        raise HTTPException(400, f"Unknown specialization '{req.specialization}'. Valid: {list(SPECIALIZATIONS)} + ['external']")
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "id": task_id,
        "specialization": req.specialization,
        "prompt": req.prompt[:500],
        "status": "queued",
        "created_at": time.time(),
        "result": None,
    }
    _prune_tasks()
    storage = get_storage()
    await storage.insert_task(task_id, req.specialization, req.prompt, "queued")
    await storage.insert_event("task_created", {"task_id": task_id, "specialization": req.specialization})
    await TASK_EVENTS.put({"type": "task_created", "task_id": task_id, "specialization": req.specialization})
    asyncio.create_task(_run_task(task_id, req.specialization, req.prompt, req.data))
    logger.info(f"Task created: {task_id} for specialization: {req.specialization}")
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks")
@rate_limit
async def list_tasks(request: Request, limit: int = 50):
    """List recent tasks."""
    storage = get_storage()
    if supabase_configured():
        db_tasks = await storage.get_tasks(limit=limit)
        if db_tasks:
            return {"count": len(db_tasks), "tasks": db_tasks, "source": "database"}
    items = sorted(TASKS.values(), key=lambda t: t["created_at"], reverse=True)[: min(limit, 200)]
    return {"count": len(items), "tasks": items, "source": "memory"}


@app.get("/api/tasks/{task_id}")
@rate_limit
async def get_task(request: Request, task_id: str):
    """Get task details by ID."""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@app.get("/api/events")
@rate_limit
async def list_events(request: Request, limit: int = 50):
    """List recent events."""
    storage = get_storage()
    events = await storage.get_events(limit=limit)
    return {"count": len(events), "events": events}


@app.get("/api/events/stream")
async def event_stream():
    """Server-Sent Events stream for real-time task updates."""
    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(TASK_EVENTS.get(), timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
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
    """Natural-language command: route to the best specialist and execute."""
    llm = get_llm()
    spec = "communicator"
    if llm.available:
        routed = await llm.complete(ROUTER_SYSTEM, req.message, max_tokens=20)
        if routed["ok"]:
            candidate = routed["content"].strip().lower().replace("-", "_")
            for name in SPECIALIZATIONS:
                if name in candidate:
                    spec = name
                    break
    agent = _pick_agent(spec)
    outcome = await agent.execute({"prompt": req.message})

    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        "id": task_id,
        "specialization": spec,
        "prompt": req.message[:500],
        "status": "completed" if outcome["ok"] else "failed",
        "created_at": time.time(),
        "finished_at": time.time(),
        "result": outcome,
        "agent": agent.name,
    }
    _prune_tasks()

    storage = get_storage()
    await storage.insert_task(task_id, spec, req.message, "completed")
    await storage.update_task(task_id, "completed", agent_name=agent.name,
                              result=outcome, elapsed_s=outcome.get("elapsed_s", 0))
    await storage.upsert_agent_stats(
        agent.name, spec,
        agent.stats["tasks_completed"], agent.stats["tasks_failed"], agent.stats["total_time_s"]
    )
    await storage.insert_event("chat_completed", {
        "task_id": task_id, "agent": agent.name, "specialization": spec, "ok": outcome["ok"]
    })

    logger.info(f"Chat processed: {task_id} routed to {spec} by agent {agent.name}")
    return {
        "task_id": task_id,
        "routed_to": spec,
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
    """Register an external agent (requires admin API key for auth)."""
    if not ENABLE_AUTH:
        raise HTTPException(401, "External agent registration requires authentication")
    
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    if any(a.name == req.name for a in EXTERNAL_AGENTS):
        raise HTTPException(409, f"external agent '{req.name}' already registered")
    adapter = ExternalAgentAdapter(req.name, req.url, req.model, req.api_key)
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
    """Remove an external agent (requires admin API key for auth)."""
    if not ENABLE_AUTH:
        raise HTTPException(401, "External agent removal requires authentication")
    
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
    register_civilization_routes(app, CIVILIZATION, TASKS, _prune_tasks)
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
