"""
Tests for AGOS Infrastructure Components

Tests circuit breaker, metrics, health checks, and Redis queue.
"""

import asyncio
import pytest
from datetime import datetime, timedelta

# Test Circuit Breaker
def test_circuit_breaker_closed_state():
    """Test circuit breaker starts in closed state."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker("test", failure_threshold=3)
    
    assert breaker.state == CircuitState.CLOSED
    assert breaker.is_closed
    assert not breaker.is_open


def test_circuit_breaker_opens_after_failures():
    """Test circuit breaker opens after threshold failures."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker("test", failure_threshold=3)
    
    # Record failures
    breaker._record_failure()
    breaker._record_failure()
    assert breaker.is_closed
    
    breaker._record_failure()
    assert breaker.is_open


def test_circuit_breaker_records_success():
    """Test circuit breaker records success."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker("test", failure_threshold=3)
    
    breaker._record_failure()
    breaker._record_failure()
    breaker._record_success()
    
    assert breaker._failure_count == 0
    assert breaker.is_closed


def test_circuit_breaker_half_open():
    """Test circuit breaker transitions to half-open."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker("test", failure_threshold=1, timeout=0)
    
    # Open the circuit
    breaker._record_failure()
    assert breaker.is_open
    
    # Wait for timeout
    breaker._last_failure_time = datetime.utcnow() - timedelta(seconds=1)
    
    # Should transition to half-open
    assert breaker.state == CircuitState.HALF_OPEN


def test_circuit_breaker_allow_request():
    """Test circuit breaker allows requests appropriately."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker("test", failure_threshold=1, timeout=0)
    
    # Should allow when closed
    assert breaker.allow_request()
    
    # Open the circuit
    breaker._record_failure()
    assert breaker.is_open
    
    # Should not allow when open
    assert not breaker.allow_request()


def test_circuit_breaker_get_stats():
    """Test circuit breaker stats."""
    from agos_kernel.infrastructure.circuit_breaker import CircuitBreaker
    
    breaker = CircuitBreaker("test", failure_threshold=3)
    breaker._record_failure()
    
    stats = breaker.get_stats()
    
    assert stats["name"] == "test"
    assert stats["failure_count"] == 1
    assert "config" in stats


# Test Metrics Collector
def test_metrics_collector_initialization():
    """Test metrics collector initializes correctly."""
    from agos_kernel.infrastructure.metrics import MetricsCollector
    
    metrics = MetricsCollector(namespace="test")
    
    assert metrics.namespace == "test"
    assert len(metrics._metrics) > 0  # Default metrics registered


def test_metrics_counter_increment():
    """Test counter increment."""
    from agos_kernel.infrastructure.metrics import MetricsCollector
    
    metrics = MetricsCollector(namespace="test")
    
    metrics.inc_counter("test_counter", value=5)
    
    assert metrics._counters.get("test_test_counter") == 5


def test_metrics_gauge_set():
    """Test gauge set."""
    from agos_kernel.infrastructure.metrics import MetricsCollector, MetricType
    
    metrics = MetricsCollector(namespace="test")
    
    metrics.set_gauge("test_gauge", 42.0)
    
    assert "test_test_gauge" in metrics._metrics


def test_metrics_histogram_observe():
    """Test histogram observation."""
    from agos_kernel.infrastructure.metrics import MetricsCollector
    
    metrics = MetricsCollector(namespace="test")
    
    metrics.observe_histogram("test_histogram", 0.5)
    metrics.observe_histogram("test_histogram", 1.0)
    
    assert len(metrics._histograms.get("test_test_histogram", [])) == 2


def test_metrics_export_prometheus():
    """Test Prometheus export format."""
    from agos_kernel.infrastructure.metrics import MetricsCollector
    
    metrics = MetricsCollector(namespace="test")
    
    metrics.inc_counter("test_counter", value=10)
    metrics.set_gauge("test_gauge", 42.0)
    
    output = metrics.export_prometheus()
    
    assert "test_counter" in output
    assert "test_gauge" in output


# Test Health Check
@pytest.mark.asyncio
async def test_health_check_success():
    """Test health check with successful check."""
    from agos_kernel.infrastructure.health import HealthCheck, HealthStatus
    
    async def check():
        return {"status": "healthy", "message": "OK"}
    
    health = HealthCheck("test", check)
    result = await health.run()
    
    assert result.status == HealthStatus.HEALTHY
    assert result.component == "test"


@pytest.mark.asyncio
async def test_health_check_timeout():
    """Test health check timeout."""
    from agos_kernel.infrastructure.health import HealthCheck, HealthStatus
    
    async def slow_check():
        await asyncio.sleep(10)
        return True
    
    health = HealthCheck("test", slow_check, timeout=0.1)
    result = await health.run()
    
    assert result.status == HealthStatus.UNHEALTHY
    assert "Timeout" in result.message


@pytest.mark.asyncio
async def test_health_check_exception():
    """Test health check exception handling."""
    from agos_kernel.infrastructure.health import HealthCheck, HealthStatus
    
    async def failing_check():
        raise ValueError("Test error")
    
    health = HealthCheck("test", failing_check)
    result = await health.run()
    
    assert result.status == HealthStatus.UNHEALTHY
    assert "ValueError" in result.message


@pytest.mark.asyncio
async def test_health_check_registry():
    """Test health check registry."""
    from agos_kernel.infrastructure.health import HealthCheck, HealthCheckRegistry, HealthStatus
    
    async def check1():
        return True
    
    async def check2():
        return {"status": "healthy", "message": "OK"}
    
    registry = HealthCheckRegistry()
    registry.register(HealthCheck("check1", check1))
    registry.register(HealthCheck("check2", check2))
    
    results = await registry.run_all()
    
    assert len(results) == 2
    assert all(r.status == HealthStatus.HEALTHY for r in results.values())


@pytest.mark.asyncio
async def test_health_system_status():
    """Test system health aggregation."""
    from agos_kernel.infrastructure.health import HealthCheck, HealthCheckRegistry
    
    async def healthy_check():
        return True
    
    async def failing_check():
        return False
    
    registry = HealthCheckRegistry()
    registry.register(HealthCheck("healthy", healthy_check))
    registry.register(HealthCheck("failing", failing_check))
    
    system_health = await registry.get_system_health()
    
    assert system_health["summary"]["total"] == 2


# Test Redis Queue (without actual Redis)
@pytest.mark.asyncio
async def test_redis_queue_fallback():
    """Test Redis queue fallback to in-memory."""
    from agos_kernel.infrastructure.redis_queue import RedisQueue, TaskStatus
    
    queue = RedisQueue(redis_url="invalid://localhost")  # Will fail to connect
    await queue.connect()
    
    # Should be using fallback
    assert queue._use_fallback
    
    # Enqueue a task
    task_id = await queue.enqueue("analyst", "test prompt")
    assert task_id is not None
    
    # Dequeue
    task = await queue.dequeue(timeout=1)
    assert task is not None
    assert task.specialization == "analyst"
    
    # Complete
    await queue.complete(task_id, {"result": "success"})
    
    retrieved = await queue.get_task(task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_task_priority():
    """Test task priority ordering."""
    from agos_kernel.infrastructure.redis_queue import RedisQueue
    
    queue = RedisQueue()
    await queue.connect()
    
    # Enqueue tasks with different priorities
    await queue.enqueue("analyst", "low priority", priority=1)
    await queue.enqueue("builder", "high priority", priority=10)
    await queue.enqueue("surgeon", "medium priority", priority=5)
    
    # Get queue size
    size = await queue.get_queue_size()
    assert size >= 3


@pytest.mark.asyncio
async def test_task_failure_retry():
    """Test task failure and retry."""
    from agos_kernel.infrastructure.redis_queue import RedisQueue
    
    queue = RedisQueue()
    await queue.connect()
    
    task_id = await queue.enqueue("analyst", "failing task")
    
    # Fail the task
    should_retry = await queue.fail(task_id, "Test error")
    
    # First failure should retry
    assert should_retry


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
