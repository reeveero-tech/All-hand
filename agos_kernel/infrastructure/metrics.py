"""
Prometheus Metrics Collector

Provides observability through metrics collection.
Features:
- Request counters and latencies
- Agent performance metrics
- Task queue metrics
- Custom business metrics
"""

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from collections import defaultdict, deque

logger = logging.getLogger("agos.infrastructure.metrics")


class MetricType(str, Enum):
    """Metric types supported."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class Metric:
    """Represents a single metric."""
    name: str
    description: str
    metric_type: MetricType
    labels: Dict[str, str] = field(default_factory=dict)
    value: float = 0
    buckets: Optional[List[float]] = None  # For histogram
    
    def to_prometheus(self) -> str:
        """Convert to Prometheus format."""
        label_str = ",".join(f'{k}="{v}"' for k, v in self.labels.items())
        if label_str:
            label_str = "{" + label_str + "}"
        
        if self.metric_type == MetricType.HISTOGRAM:
            lines = [f"# TYPE {self.name} histogram"]
            lines.append(f"# HELP {self.name} {self.description}")
            
            cumulative = 0
            for bucket in sorted(self.buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]):
                lines.append(f"{self.name}_bucket{{{label_str},le=\"{bucket}\"}} {cumulative}")
            
            lines.append(f"{self.name}_sum{{{label_str}}} {self.value}")
            lines.append(f"{self.name}_count{{{label_str}}} {self.value}")
            return "\n".join(lines)
        
        type_str = self.metric_type.value
        return f"# TYPE {self.name} {type_str}\n# HELP {self.name} {self.description}\n{self.name}{label_str} {self.value}"


class MetricsCollector:
    """
    Prometheus-compatible metrics collector.
    
    Features:
    - Counters for accumulating values
    - Gauges for point-in-time values
    - Histograms for distributions
    - Automatic label tracking
    """
    
    DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]

    def __init__(self, namespace: str = "agos"):
        """
        Initialize metrics collector.
        
        Args:
            namespace: Metric namespace prefix
        """
        self.namespace = namespace
        self._metrics: Dict[str, Metric] = {}
        self._counters: Dict[str, float] = defaultdict(float)
        # R-083: raw samples are kept only as a bounded window (diagnostics);
        # export uses exact per-series aggregates, so memory no longer grows
        # with every HTTP request once MetricsMiddleware is mounted.
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1024))
        self._hist_agg: Dict[str, Dict[str, Any]] = {}
        # R-082: one series per label set (Prometheus model). Before, gauges and
        # counters kept a single series per name, so `agents_active` exported 1
        # of 20 specializations and labelled counters collapsed into one line.
        self._gauges: Dict[str, float] = {}
        self._series_labels: Dict[str, Dict[str, str]] = {}
        
        # Predefined metrics
        self._init_default_metrics()
        
        logger.info(f"MetricsCollector initialized with namespace: {namespace}")
    
    def _init_default_metrics(self) -> None:
        """Initialize default system metrics."""
        # Request metrics
        self.register(
            name="http_requests_total",
            description="Total HTTP requests",
            metric_type=MetricType.COUNTER,
            labels=None,
        )
        
        self.register(
            name="http_request_duration_seconds",
            description="HTTP request duration in seconds",
            metric_type=MetricType.HISTOGRAM,
            labels=None,
            buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
        )
        
        # Task metrics
        self.register(
            name="tasks_total",
            description="Total tasks processed",
            metric_type=MetricType.COUNTER,
            labels=None,
        )
        
        self.register(
            name="task_duration_seconds",
            description="Task execution duration in seconds",
            metric_type=MetricType.HISTOGRAM,
            labels=None,
            buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
        )
        
        # Agent metrics
        self.register(
            name="agents_active",
            description="Number of active agents",
            metric_type=MetricType.GAUGE,
            labels=None,
        )
        
        self.register(
            name="agent_tasks_total",
            description="Total tasks per agent",
            metric_type=MetricType.COUNTER,
            labels=None,
        )
        
        # Queue metrics
        self.register(
            name="queue_size",
            description="Task queue size",
            metric_type=MetricType.GAUGE,
        )
        
        # System metrics
        self.register(
            name="uptime_seconds",
            description="System uptime in seconds",
            metric_type=MetricType.GAUGE,
        )
        
        self.register(
            name="memory_usage_bytes",
            description="Memory usage in bytes",
            metric_type=MetricType.GAUGE,
            labels=None,
        )
        
        # Error metrics
        self.register(
            name="errors_total",
            description="Total errors",
            metric_type=MetricType.COUNTER,
            labels=None,
        )
        
        # Circuit breaker metrics
        self.register(
            name="circuit_breaker_state",
            description="Circuit breaker state (0=closed, 1=open, 2=half_open)",
            metric_type=MetricType.GAUGE,
            labels=None,
        )
    
    def register(
        self,
        name: str,
        description: str,
        metric_type: MetricType,
        labels: Optional[Dict[str, str]] = None,
        buckets: Optional[List[float]] = None,
    ) -> None:
        """Register a new metric."""
        full_name = f"{self.namespace}_{name}"
        self._metrics[full_name] = Metric(
            name=full_name,
            description=description,
            metric_type=metric_type,
            labels=labels or {},
            buckets=buckets,
        )
    
    def inc_counter(
        self,
        name: str,
        value: float = 1,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Increment a counter metric."""
        key = self._make_key(name, labels)
        self._counters[key] += value
        self._series_labels[key] = dict(labels or {})
        
        full_name = f"{self.namespace}_{name}"
        if full_name not in self._metrics:
            # Auto-register counter (mirrors set_gauge behavior)
            self.register(name=name, description=name, metric_type=MetricType.COUNTER, labels=labels)
    
    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Set a gauge metric value."""
        full_name = f"{self.namespace}_{name}"
        key = self._make_key(name, labels)
        self._gauges[key] = value
        self._series_labels[key] = dict(labels or {})
        if full_name not in self._metrics:
            # Auto-register gauge (series live in self._gauges, keyed by labels)
            self.register(name=name, description=name, metric_type=MetricType.GAUGE)
        self._metrics[full_name].value = value  # last value — kept for get_metrics() callers
    
    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Observe a value for histogram."""
        key = self._make_key(name, labels)
        self._histograms[key].append(value)
        self._series_labels[key] = dict(labels or {})
        full_name = f"{self.namespace}_{name}"
        if full_name not in self._metrics:
            self.register(name=name, description=name, metric_type=MetricType.HISTOGRAM)
        metric = self._metrics[full_name]
        bounds = sorted(metric.buckets or self.DEFAULT_BUCKETS)
        agg = self._hist_agg.setdefault(key, {"bounds": bounds, "counts": [0] * len(bounds), "sum": 0.0, "count": 0})
        for i, b in enumerate(agg["bounds"]):
            if value <= b:
                agg["counts"][i] += 1  # per-bucket (non-cumulative) — cumulated at export
                break
        agg["sum"] += value
        agg["count"] += 1
        metric.value += value
    
    def _make_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        """Create unique key for metric with labels."""
        full_name = f"{self.namespace}_{name}"
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{full_name}{{{label_str}}}"
        return full_name
    
    def record_request(
        self,
        method: str,
        endpoint: str,
        status: int,
        duration: float,
    ) -> None:
        """Record an HTTP request."""
        labels = {"method": method, "endpoint": endpoint, "status": str(status)}
        self.inc_counter("http_requests_total", labels=labels)
        self.observe_histogram("http_request_duration_seconds", duration, labels=labels)
    
    def record_task(
        self,
        specialization: str,
        status: str,
        duration: Optional[float] = None,
    ) -> None:
        """Record a task execution."""
        self.inc_counter("tasks_total", labels={"specialization": specialization, "status": status})
        if duration is not None:
            self.observe_histogram("task_duration_seconds", duration, labels={"specialization": specialization})
    
    def record_agent_task(
        self,
        agent_name: str,
        specialization: str,
    ) -> None:
        """Record a task for a specific agent."""
        self.inc_counter("agent_tasks_total", labels={"agent_name": agent_name, "specialization": specialization})
    
    def record_error(
        self,
        error_type: str,
        component: str,
    ) -> None:
        """Record an error."""
        self.inc_counter("errors_total", labels={"type": error_type, "component": component})
    
    def record_circuit_breaker(self, name: str, state: int) -> None:
        """Record circuit breaker state."""
        self.set_gauge("circuit_breaker_state", state, labels={"name": name})
    
    def get_metrics(self) -> List[Metric]:
        """Get all registered metrics."""
        return list(self._metrics.values())
    
    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus format."""
        lines = []
        
        for name, metric in self._metrics.items():
            if metric.metric_type in (MetricType.COUNTER, MetricType.GAUGE):
                store = self._counters if metric.metric_type == MetricType.COUNTER else self._gauges
                kind = "counter" if metric.metric_type == MetricType.COUNTER else "gauge"
                lines.append(f"# TYPE {name} {kind}")
                lines.append(f"# HELP {name} {metric.description}")
                series = sorted(k for k in store if k == name or k.startswith(name + "{"))
                if not series:
                    # registered but never touched: one zero/initial sample, as before
                    label_str = ",".join(f'{k}="{v}"' for k, v in metric.labels.items())
                    lines.append(f"{name}{'{' + label_str + '}' if label_str else ''} "
                                 f"{0 if kind == 'counter' else metric.value}")
                for key in series:
                    labels = self._series_labels.get(key, {})
                    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
                    lines.append(f"{name}{'{' + label_str + '}' if label_str else ''} {store[key]}")
            
            elif metric.metric_type == MetricType.HISTOGRAM:
                # R-083: one histogram per label set, valid exposition format
                # (`le` inside the braces), correct cumulative buckets.
                series = sorted(k for k in self._hist_agg if k == name or k.startswith(name + "{"))
                if series:
                    lines.append(f"# TYPE {name} histogram")
                    lines.append(f"# HELP {name} {metric.description}")
                for key in series:
                    agg = self._hist_agg[key]
                    base = [f'{k}="{v}"' for k, v in self._series_labels.get(key, {}).items()]
                    cumulative = 0
                    # (no backslashes inside f-string expressions: CI runs Python 3.11)
                    for b, n in zip(agg["bounds"], agg["counts"]):
                        cumulative += n
                        le_labels = ",".join(base + ['le="%s"' % b])
                        lines.append(f"{name}_bucket{{{le_labels}}} {cumulative}")
                    inf_labels = ",".join(base + ['le="+Inf"'])
                    lines.append(f"{name}_bucket{{{inf_labels}}} {agg['count']}")
                    tail = "{" + ",".join(base) + "}" if base else ""
                    lines.append(f"{name}_sum{tail} {agg['sum']}")
                    lines.append(f"{name}_count{tail} {agg['count']}")
        
        return "\n".join(lines)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        return {
            "namespace": self.namespace,
            "metrics_count": len(self._metrics),
            "counters": dict(self._counters),
            "histograms": {k: len(v) for k, v in self._histograms.items()},
        }


# Global metrics collector
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get or create global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector(
            namespace=os.getenv("METRICS_NAMESPACE", "agos")
        )
    return _metrics_collector


class MetricsMiddleware:
    """FastAPI middleware for automatic metrics collection."""
    
    def __init__(self, app, metrics: Optional[MetricsCollector] = None):
        """Initialize middleware."""
        self.app = app
        self.metrics = metrics or get_metrics()
    
    async def __call__(self, scope, receive, send):
        """Process request and record metrics."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Extract request info
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        
        # Skip metrics endpoint
        if path.startswith("/metrics"):
            await self.app(scope, receive, send)
            return
        
        start_time = time.time()
        status = 200
        
        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status = 500
            raise
        finally:
            duration = time.time() - start_time
            # R-083: label by the matched route TEMPLATE (e.g. /api/tasks/{task_id},
            # /{full_path:path}) — bounded cardinality; the raw path never becomes a label.
            route = scope.get("route")
            endpoint = getattr(route, "path_format", None) or getattr(route, "path", None) or "<unmatched>"
            self.metrics.record_request(method, endpoint, status, duration)
    
    def _normalize_path(self, path: str) -> str:
        """Normalize path by replacing IDs with placeholders."""
        import re
        # Replace UUIDs
        path = re.sub(r'[0-9a-f]{12}', '{id}', path)
        # Replace numeric IDs
        path = re.sub(r'/\d+', '/{id}', path)
        return path
