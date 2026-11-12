"""
AGOS Kernel Infrastructure Module

- Prometheus metrics for observability
- Health checks for monitoring

R-203 (D-152, DEL-001): the in-memory "Redis" queue and the unused circuit
breaker were removed — nothing on the request path used them. The live
breakers are in ``agent_civilization.core.backpressure``.
"""

from .metrics import MetricsCollector, get_metrics
from .health import HealthCheck, HealthStatus

__all__ = [
    "MetricsCollector",
    "get_metrics",
    "HealthCheck",
    "HealthStatus",
]
