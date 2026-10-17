"""
Health Check System

Provides comprehensive health monitoring for all system components.
Features:
- Component-level health checks
- Aggregated system health
- Readiness and liveness probes
- Detailed status reporting
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("agos.infrastructure.health")


class HealthStatus(str, Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    component: str
    status: HealthStatus
    message: str = ""
    duration_ms: float = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 2),
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }


class HealthCheck:
    """
    Health check for a system component.
    
    Usage:
        async def check_database():
            return await db.ping()
        
        health_check = HealthCheck(
            name="database",
            check_fn=check_database,
            timeout=5,
        )
        result = await health_check.run()
    """
    
    def __init__(
        self,
        name: str,
        check_fn: Callable[[], Any],
        timeout: float = 5.0,
        critical: bool = True,
        tags: Optional[List[str]] = None,
    ):
        """
        Initialize health check.
        
        Args:
            name: Component name
            check_fn: Async function that performs the check
            timeout: Maximum time for check in seconds
            critical: Whether component is critical for overall health
            tags: Optional tags for categorization
        """
        self.name = name
        self.check_fn = check_fn
        self.timeout = timeout
        self.critical = critical
        self.tags = tags or []
        
        self._last_result: Optional[HealthCheckResult] = None
        self._last_check_time: Optional[datetime] = None
    
    async def run(self) -> HealthCheckResult:
        """Run the health check."""
        start_time = time.time()
        self._last_check_time = datetime.utcnow()
        
        try:
            result = await asyncio.wait_for(
                self.check_fn(),
                timeout=self.timeout,
            )
            
            duration_ms = (time.time() - start_time) * 1000
            
            if result is True or result is None:
                status = HealthStatus.HEALTHY
                message = "OK"
            elif isinstance(result, dict):
                status = HealthStatus(result.get("status", "healthy"))
                message = result.get("message", "OK")
            else:
                status = HealthStatus.HEALTHY
                message = str(result)
            
            self._last_result = HealthCheckResult(
                component=self.name,
                status=status,
                message=message,
                duration_ms=duration_ms,
                details={"result": result} if not isinstance(result, dict) else result,
            )
            
        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            self._last_result = HealthCheckResult(
                component=self.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout after {self.timeout}s",
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._last_result = HealthCheckResult(
                component=self.name,
                status=HealthStatus.UNHEALTHY,
                # R-160 (G-159-1): /health is public — type only, never the
                # raw exception text (it can carry URLs, hosts, credentials).
                message=f"Error: {type(e).__name__}",
                duration_ms=duration_ms,
                details={"error_type": type(e).__name__},
            )
        
        return self._last_result
    
    @property
    def last_result(self) -> Optional[HealthCheckResult]:
        """Get last check result."""
        return self._last_result
    
    @property
    def is_healthy(self) -> bool:
        """Check if component is healthy."""
        return self._last_result is not None and self._last_result.status == HealthStatus.HEALTHY


class HealthCheckRegistry:
    """
    Registry for all health checks.
    
    Manages health checks and provides aggregated health status.
    """
    
    def __init__(self):
        """Initialize registry."""
        self._checks: Dict[str, HealthCheck] = {}
        self._start_time = time.time()
        logger.info("HealthCheckRegistry initialized")
    
    def register(self, check: HealthCheck) -> None:
        """Register a health check."""
        self._checks[check.name] = check
        logger.debug(f"Registered health check: {check.name}")
    
    def unregister(self, name: str) -> None:
        """Unregister a health check."""
        if name in self._checks:
            del self._checks[name]
            logger.debug(f"Unregistered health check: {name}")
    
    def get(self, name: str) -> Optional[HealthCheck]:
        """Get a health check by name."""
        return self._checks.get(name)
    
    async def run_all(self) -> Dict[str, HealthCheckResult]:
        """Run all health checks."""
        tasks = [check.run() for check in self._checks.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {
            name: result if not isinstance(result, Exception) else HealthCheckResult(
                component=name,
                status=HealthStatus.UNKNOWN,
                message=f"Exception: {str(result)}",
            )
            for name, result in zip(self._checks.keys(), results)
        }
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get overall system health status."""
        results = await self.run_all()
        
        # Calculate overall status
        statuses = [r.status for r in results.values()]
        
        if HealthStatus.UNHEALTHY in statuses:
            overall = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall = HealthStatus.DEGRADED
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            overall = HealthStatus.HEALTHY
        else:
            overall = HealthStatus.DEGRADED
        
        # Check critical components
        critical_healthy = all(
            results[check.name].status == HealthStatus.HEALTHY
            for check in self._checks.values()
            if check.critical
        )
        
        if not critical_healthy:
            overall = HealthStatus.UNHEALTHY
        
        return {
            "status": overall.value,
            "uptime_seconds": round(time.time() - self._start_time, 2),
            "checks": {name: result.to_dict() for name, result in results.items()},
            "summary": {
                "total": len(results),
                "healthy": sum(1 for r in results.values() if r.status == HealthStatus.HEALTHY),
                "degraded": sum(1 for r in results.values() if r.status == HealthStatus.DEGRADED),
                "unhealthy": sum(1 for r in results.values() if r.status == HealthStatus.UNHEALTHY),
                "unknown": sum(1 for r in results.values() if r.status == HealthStatus.UNKNOWN),
            },
        }


# Default health checks
async def check_api() -> Dict[str, Any]:
    """Check API availability."""
    return {"status": "healthy", "message": "API is responding"}

# R-157 (D-069): one source of truth for "is the civilization serving?".
# server.py registers its live civilization here at startup, so /health (this
# registry) and /health/ready / /api/system/status judge the SAME object.
# Before R-157 this check built its own default civilization on every call
# (1,000 agents whatever AGENTS_PER_SPEC said) and could answer "healthy"
# while /health/ready answered 503 (docs/audit/M8/logs/raw/077_*).
_civilization_provider: Optional[Callable[[], Any]] = None


def set_civilization_provider(provider: Optional[Callable[[], Any]]) -> None:
    """Register (or clear with ``None``) the callable returning the live
    civilization map ``{specialization: [agent, ...]}``."""
    global _civilization_provider
    _civilization_provider = provider


def civilization_status(civilization: Any) -> Dict[str, Any]:
    """The readiness rule shared by /health and /health/ready.

    Returns ``{"ready", "reason", "agents"}``; ``reason`` is the exact text
    /health/ready has always returned for the two not-ready states. May raise
    if ``civilization`` is broken — callers map that to a fixed reason."""
    if not civilization:
        return {"ready": False, "reason": "Civilization not initialized", "agents": 0}
    total = sum(len(agents) for agents in civilization.values())
    if total == 0:
        return {"ready": False, "reason": "No agents available", "agents": 0}
    return {"ready": True, "reason": None, "agents": total}


async def check_civilization() -> Dict[str, Any]:
    """Check civilization system.

    With a registered provider (server.py) the live civilization is judged by
    ``civilization_status``. Without one (health used standalone) the legacy
    behaviour is kept unchanged: build the default civilization lazily via
    `create_civilization()` so health can run independently of the server.
    """
    provider = _civilization_provider
    if provider is not None:
        try:
            st = civilization_status(provider())
        except Exception as e:  # noqa: BLE001
            # fixed reason, type-only log — same rule as /health/ready (R-149)
            logger.warning("civilization health check failed: %s", type(e).__name__)
            return {"status": "unhealthy", "message": "readiness check failed"}
        if not st["ready"]:
            return {"status": "unhealthy", "message": st["reason"], "agent_count": 0}
        return {
            "status": "healthy",
            "message": f"Civilization active with {st['agents']} agents",
            "agent_count": st["agents"],
        }
    try:
        from agent_civilization.agents.real import create_civilization
        civilization = create_civilization()
        agent_count = sum(len(agents) for agents in civilization.values())
        return {
            "status": "healthy",
            "message": f"Civilization active with {agent_count} agents",
            "agent_count": agent_count,
        }
    except Exception as e:
        return {"status": "unhealthy", "message": f"Error: {type(e).__name__}"}  # R-160 (G-159-1, G-157-2)

async def check_storage() -> Dict[str, Any]:
    """Check storage availability."""
    try:
        from agent_civilization.storage import is_configured, key_status, storage_stats
        configured = is_configured()
        key = key_status()
        stats = storage_stats()
        if not configured:
            status, message = "degraded", "Using in-memory storage"
        elif key["kind"] == "public":
            # R-078 (RISK-018): with RLS and no anon policies (D-039) every write is
            # rejected — the server silently falls back to memory. Make it visible.
            status = "degraded"
            message = (f"Supabase configured with a public key (from {key['source']}): writes are "
                       "rejected by RLS — set SUPABASE_SERVICE_KEY to the secret/service_role key")
        elif stats["last_ok"] is False:
            # R-082 (RISK-013): the most recent storage operation failed at runtime
            # (Supabase down, key revoked, 5xx ...) — data is going to memory only.
            # Back to healthy on the next successful operation.
            err = stats["last_error"] or {}
            status = "degraded"
            message = (f"last storage operation failed: {err.get('operation')} "
                       f"({err.get('reason')}) at {err.get('at')}")
        else:
            status, message = "healthy", "Supabase configured"
        return {
            "status": status,
            "message": message,
            "supabase_configured": configured,
            "supabase_key": key["kind"],
            "last_ok": stats["last_ok"],
            "last_error": stats["last_error"],
            "last_success_at": stats["last_success_at"],
            "total_ok": stats["total_ok"],
            "total_failed": stats["total_failed"],
        }
    except Exception as e:
        return {"status": "degraded", "message": f"Error: {type(e).__name__}"}  # R-160 (G-159-1)

async def check_redis() -> Dict[str, Any]:
    """Check Redis availability."""
    try:
        from agos_kernel.infrastructure.redis_queue import get_redis_queue
        queue = get_redis_queue()
        await queue.connect()
        stats = await queue.get_stats()
        return {
            "status": "healthy",
            "message": f"Redis connected: {stats.get('connected', False)}",
            **stats,
        }
    except Exception as e:
        return {"status": "degraded", "message": f"Redis unavailable: {type(e).__name__}"}  # R-160 (G-159-1)


def create_default_registry() -> HealthCheckRegistry:
    """Create registry with default health checks."""
    registry = HealthCheckRegistry()
    
    registry.register(HealthCheck(
        name="api",
        check_fn=check_api,
        critical=True,
        tags=["api", "core"],
    ))
    
    registry.register(HealthCheck(
        name="civilization",
        check_fn=check_civilization,
        critical=True,
        tags=["agents", "core"],
    ))
    
    registry.register(HealthCheck(
        name="storage",
        check_fn=check_storage,
        critical=False,
        tags=["persistence"],
    ))
    
    registry.register(HealthCheck(
        name="redis",
        check_fn=check_redis,
        critical=False,
        tags=["queue", "infrastructure"],
    ))
    
    return registry


# Global registry
_health_registry: Optional[HealthCheckRegistry] = None


def get_health_registry() -> HealthCheckRegistry:
    """Get or create global health registry."""
    global _health_registry
    if _health_registry is None:
        _health_registry = create_default_registry()
    return _health_registry
