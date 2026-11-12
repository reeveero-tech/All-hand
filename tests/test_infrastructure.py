"""
Tests for AGOS Infrastructure Components

Tests circuit breaker, metrics, health checks, and Redis queue.
"""

import asyncio
import pytest

# R-203 (D-152, DEL-001): agos_kernel circuit_breaker.py and redis_queue.py
# were removed (unused); the live breaker is tested in test_r164_backpressure.py.

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
    from agos_kernel.infrastructure.metrics import MetricsCollector
    
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
