"""Runtime Observability - Structured logging, metrics, tracing, and health checks."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


# =============================================================================
# LOGGING
# =============================================================================

class LogLevel(Enum):
    """Log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEntry:
    """Structured log entry."""
    id: str
    level: LogLevel
    message: str
    timestamp: datetime
    context: Dict[str, Any] = field(default_factory=dict)
    mission_id: Optional[str] = None
    execution_id: Optional[str] = None
    
    @classmethod
    def create(cls, level: LogLevel, message: str, **context) -> 'LogEntry':
        return cls(
            id=str(uuid4()),
            level=level,
            message=message,
            timestamp=datetime.utcnow(),
            context=context
        )


class StructuredLogger:
    """Structured logging."""
    
    def __init__(self):
        self._logs: List[LogEntry] = []
        self._max_logs = 10000
    
    def log(self, level: LogLevel, message: str, **context) -> LogEntry:
        """Log an entry."""
        entry = LogEntry.create(level, message, **context)
        self._logs.append(entry)
        
        # Trim
        if len(self._logs) > self._max_logs:
            self._logs = self._logs[-self._max_logs:]
        
        return entry
    
    def debug(self, message: str, **context) -> LogEntry:
        return self.log(LogLevel.DEBUG, message, **context)
    
    def info(self, message: str, **context) -> LogEntry:
        return self.log(LogLevel.INFO, message, **context)
    
    def warning(self, message: str, **context) -> LogEntry:
        return self.log(LogLevel.WARNING, message, **context)
    
    def error(self, message: str, **context) -> LogEntry:
        return self.log(LogLevel.ERROR, message, **context)
    
    def critical(self, message: str, **context) -> LogEntry:
        return self.log(LogLevel.CRITICAL, message, **context)
    
    def get_logs(self, level: LogLevel = None, limit: int = 100) -> List[LogEntry]:
        """Get logs."""
        logs = self._logs
        if level:
            logs = [l for l in logs if l.level == level]
        return logs[-limit:]


# =============================================================================
# METRICS
# =============================================================================

@dataclass
class Metric:
    """Metric data point."""
    name: str
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Collects metrics."""
    
    def __init__(self):
        self._metrics: Dict[str, List[Metric]] = {}
        self._counters: Dict[str, float] = {}
        self._gauges: Dict[str, float] = {}
    
    def record(self, name: str, value: float, **labels) -> Metric:
        """Record a metric."""
        metric = Metric(name=name, value=value, timestamp=datetime.utcnow(), labels=labels)
        
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append(metric)
        
        return metric
    
    def increment(self, name: str, value: float = 1.0) -> None:
        """Increment a counter."""
        self._counters[name] = self._counters.get(name, 0) + value
        self.record(name, self._counters[name])
    
    def gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        self._gauges[name] = value
        self.record(name, value)
    
    def get_counter(self, name: str) -> float:
        """Get counter value."""
        return self._counters.get(name, 0)
    
    def get_gauge(self, name: str) -> float:
        """Get gauge value."""
        return self._gauges.get(name, 0)
    
    def get_metrics(self, name: str = None) -> List[Metric]:
        """Get metrics."""
        if name:
            return self._metrics.get(name, [])
        return [m for metrics in self._metrics.values() for m in metrics]


# =============================================================================
# TRACING
# =============================================================================

class SpanStatus(Enum):
    """Span status."""
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Span:
    """Trace span."""
    span_id: str
    name: str
    trace_id: str
    parent_id: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: SpanStatus = SpanStatus.STARTED
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    def end(self, status: SpanStatus = SpanStatus.COMPLETED) -> None:
        """End the span."""
        self.completed_at = datetime.utcnow()
        self.status = status
    
    def duration_ms(self) -> float:
        """Get duration in milliseconds."""
        if not self.completed_at:
            return 0
        return (self.completed_at - self.started_at).total_seconds() * 1000


class Tracer:
    """Distributed tracing."""
    
    def __init__(self):
        self._spans: Dict[str, Span] = {}
        self._traces: Dict[str, List[Span]] = {}
    
    def start_span(self, name: str, trace_id: str = None, parent_id: str = None) -> Span:
        """Start a new span."""
        trace_id = trace_id or str(uuid4())
        span_id = str(uuid4())
        
        span = Span(
            span_id=span_id,
            name=name,
            trace_id=trace_id,
            parent_id=parent_id,
            started_at=datetime.utcnow()
        )
        
        self._spans[span_id] = span
        
        if trace_id not in self._traces:
            self._traces[trace_id] = []
        self._traces[trace_id].append(span)
        
        return span
    
    def end_span(self, span_id: str, status: SpanStatus = SpanStatus.COMPLETED) -> None:
        """End a span."""
        if span_id in self._spans:
            self._spans[span_id].end(status)
    
    def add_event(self, span_id: str, name: str, **attributes) -> None:
        """Add event to span."""
        if span_id in self._spans:
            self._spans[span_id].events.append({
                "name": name,
                "timestamp": datetime.utcnow().isoformat(),
                "attributes": attributes
            })
    
    def get_trace(self, trace_id: str) -> List[Span]:
        """Get trace by ID."""
        return self._traces.get(trace_id, [])
    
    def get_span(self, span_id: str) -> Optional[Span]:
        """Get span by ID."""
        return self._spans.get(span_id)


# =============================================================================
# EXECUTION TIMELINE
# =============================================================================

@dataclass
class TimelineEvent:
    """Timeline event."""
    event_id: str
    name: str
    timestamp: datetime
    duration_ms: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExecutionTimeline:
    """Execution timeline."""
    
    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.events: List[TimelineEvent] = []
    
    def add_event(self, name: str, duration_ms: float = 0, **metadata) -> TimelineEvent:
        """Add an event."""
        event = TimelineEvent(
            event_id=str(uuid4()),
            name=name,
            timestamp=datetime.utcnow(),
            duration_ms=duration_ms,
            metadata=metadata
        )
        self.events.append(event)
        return event
    
    def get_duration_ms(self) -> float:
        """Get total duration."""
        return sum(e.duration_ms for e in self.events)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "events": [
                {
                    "event_id": e.event_id,
                    "name": e.name,
                    "timestamp": e.timestamp.isoformat(),
                    "duration_ms": e.duration_ms,
                    "metadata": e.metadata
                }
                for e in self.events
            ],
            "total_duration_ms": self.get_duration_ms()
        }


# =============================================================================
# PERFORMANCE
# =============================================================================

@dataclass
class PerformanceSnapshot:
    """Performance snapshot."""
    timestamp: datetime
    mission_duration_ms_avg: float = 0
    decision_duration_ms_avg: float = 0
    execution_duration_ms_avg: float = 0
    provider_latency_ms_avg: float = 0
    capability_latency_ms_avg: float = 0
    failure_rate: float = 0
    success_rate: float = 0
    total_missions: int = 0
    total_failures: int = 0


# =============================================================================
# HEALTH
# =============================================================================

class HealthStatus(Enum):
    """Health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheck:
    """Health check result."""
    name: str
    status: HealthStatus
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# OBSERVABILITY
# =============================================================================

class RuntimeObservability:
    """
    Runtime Observability.
    
    Implements:
    ✅ Structured Logging
    ✅ Metrics
    ✅ Tracing
    ✅ Execution Timeline
    ✅ Health Checks
    ✅ Performance Counters
    
    Metrics:
    ✅ Mission Duration
    ✅ Decision Duration
    ✅ Execution Duration
    ✅ Provider Latency
    ✅ Capability Latency
    ✅ Failure Rate
    ✅ Success Rate
    
    Output:
    ✅ RuntimeHealthReport
    ✅ ExecutionTimeline
    ✅ PerformanceSnapshot
    """
    
    def __init__(self):
        self.logger = StructuredLogger()
        self.metrics = MetricsCollector()
        self.tracer = Tracer()
    
    def record_mission_duration(self, duration_ms: float) -> None:
        """Record mission duration."""
        self.metrics.record("mission_duration_ms", duration_ms, type="mission")
    
    def record_decision_duration(self, duration_ms: float) -> None:
        """Record decision duration."""
        self.metrics.record("decision_duration_ms", duration_ms, type="decision")
    
    def record_execution_duration(self, duration_ms: float) -> None:
        """Record execution duration."""
        self.metrics.record("execution_duration_ms", duration_ms, type="execution")
    
    def record_provider_latency(self, provider: str, latency_ms: float) -> None:
        """Record provider latency."""
        self.metrics.record("provider_latency_ms", latency_ms, provider=provider)
    
    def record_capability_latency(self, capability: str, latency_ms: float) -> None:
        """Record capability latency."""
        self.metrics.record("capability_latency_ms", latency_ms, capability=capability)
    
    def record_success(self) -> None:
        """Record success."""
        self.metrics.increment("mission_success_total")
        self.metrics.increment("mission_total")
    
    def record_failure(self) -> None:
        """Record failure."""
        self.metrics.increment("mission_failure_total")
        self.metrics.increment("mission_total")
    
    def get_health_report(self, checks: List[HealthCheck] = None) -> Dict[str, Any]:
        """Get health report."""
        overall = HealthStatus.HEALTHY
        
        if checks:
            for check in checks:
                if check.status == HealthStatus.UNHEALTHY:
                    overall = HealthStatus.UNHEALTHY
                    break
                elif check.status == HealthStatus.DEGRADED:
                    overall = HealthStatus.DEGRADED
        
        return {
            "status": overall.value,
            "timestamp": datetime.utcnow().isoformat(),
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "details": c.details
                }
                for c in (checks or [])
            ]
        }
    
    def get_performance_snapshot(self) -> PerformanceSnapshot:
        """Get performance snapshot."""
        mission_metrics = self.metrics.get_metrics("mission_duration_ms")
        decision_metrics = self.metrics.get_metrics("decision_duration_ms")
        execution_metrics = self.metrics.get_metrics("execution_duration_ms")
        
        mission_avg = sum(m.value for m in mission_metrics) / len(mission_metrics) if mission_metrics else 0
        decision_avg = sum(m.value for m in decision_metrics) / len(decision_metrics) if decision_metrics else 0
        execution_avg = sum(m.value for m in execution_metrics) / len(execution_metrics) if execution_metrics else 0
        
        total = self.metrics.get_counter("mission_total")
        failures = self.metrics.get_counter("mission_failure_total")
        
        return PerformanceSnapshot(
            timestamp=datetime.utcnow(),
            mission_duration_ms_avg=mission_avg,
            decision_duration_ms_avg=decision_avg,
            execution_duration_ms_avg=execution_avg,
            failure_rate=(failures / total if total > 0 else 0),
            success_rate=((total - failures) / total if total > 0 else 0),
            total_missions=int(total),
            total_failures=int(failures)
        )
    
    def create_timeline(self, mission_id: str) -> ExecutionTimeline:
        """Create execution timeline."""
        return ExecutionTimeline(mission_id)
