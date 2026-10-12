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
                message=f"Error: {type(e).__name__}: {str(e)}",
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

async def check_civilization() -> Dict[str, Any]:
    """Check civilization system.

    The civilization map is not exported as a module attribute; the canonical
    entry point is `create_civilization()` (see server.py). Build it lazily
    here so health can run independently of the server.
    """
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
        return {"status": "unhealthy", "message": str(e)}

async def check_storage() -> Dict[str, Any]:
    """Check storage availability."""
    try:
        from agent_civilization.storage import is_configured
        configured = is_configured()
        return {
            "status": "healthy" if configured else "degraded",
            "message": "Supabase configured" if configured else "Using in-memory storage",
            "supabase_configured": configured,
        }
    except Exception as e:
        return {"status": "degraded", "message": str(e)}

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
        return {"status": "degraded", "message": f"Redis unavailable: {str(e)}"}


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
