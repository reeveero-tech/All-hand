"""
Circuit Breaker Pattern Implementation

Provides fault tolerance for external service calls.
Prevents cascading failures and enables graceful degradation.

Features:
- Three states: CLOSED, OPEN, HALF_OPEN
- Configurable failure thresholds
- Automatic recovery
- State persistence
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("agos.infrastructure.circuit_breaker")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 3           # Successes to close
    timeout: int = 60                   # Seconds before half-open
    half_open_requests: int = 3         # Requests in half-open
    exclude_exceptions: tuple = ()      # Exceptions to not count
    
    @classmethod
    def from_env(cls) -> "CircuitBreakerConfig":
        """Create config from environment variables."""
        return cls(
            failure_threshold=int(os.getenv("CIRCUIT_BREAKER_FAILURES", "5")),
            success_threshold=int(os.getenv("CIRCUIT_BREAKER_SUCCESSES", "3")),
            timeout=int(os.getenv("CIRCUIT_BREAKER_TIMEOUT", "60")),
            half_open_requests=int(os.getenv("CIRCUIT_BREAKER_HALF_OPEN", "3")),
        )


@dataclass
class CircuitBreaker:
    """
    Circuit Breaker for fault tolerance.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failing, requests are rejected immediately
    - HALF_OPEN: Testing recovery, limited requests pass
    
    Usage:
        breaker = CircuitBreaker("external_api")
        async with breaker:
            result = await call_external_api()
    """
    
    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: Optional[datetime] = field(default=None, init=False)
    _half_open_requests: int = field(default=0, init=False)
    _last_state_change: datetime = field(default_factory=datetime.utcnow, init=False)
    
    def __post_init__(self):
        """Initialize logging."""
        self.logger = logging.getLogger(f"agos.circuit_breaker.{self.name}")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state with timeout check."""
        if self._state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing)."""
        return self.state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing)."""
        return self.state == CircuitState.HALF_OPEN
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._last_failure_time is None:
            return True
        elapsed = datetime.utcnow() - self._last_failure_time
        return elapsed.total_seconds() >= self.config.timeout
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = datetime.utcnow()
        
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_requests = 0
            self._success_count = 0
        
        self.logger.info(
            f"Circuit {self.name}: {old_state.value} -> {new_state.value}"
        )
    
    def _record_success(self) -> None:
        """Record a successful call."""
        self._failure_count = 0
        
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
                self.logger.info(
                    f"Circuit {self.name}: Recovery successful "
                    f"({self._success_count} successes)"
                )
    
    def _record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = datetime.utcnow()
        
        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
            self.logger.warning(
                f"Circuit {self.name}: Recovery failed, reopening"
            )
        elif self._failure_count >= self.config.failure_threshold:
            self._transition_to(CircuitState.OPEN)
            self.logger.warning(
                f"Circuit {self.name}: Opened after "
                f"{self._failure_count} failures"
            )
    
    def allow_request(self) -> bool:
        """Check if a request should be allowed."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.HALF_OPEN:
            if self._half_open_requests < self.config.half_open_requests:
                self._half_open_requests += 1
                return True
            return False
        
        # OPEN state
        return False
    
    def record_success(self) -> None:
        """Record a successful operation."""
        self._record_success()
    
    def record_failure(self) -> None:
        """Record a failed operation."""
        self._record_failure()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure": self._last_failure_time.isoformat() if self._last_failure_time else None,
            "last_state_change": self._last_state_change.isoformat(),
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
            },
        }
    
    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_requests = 0
        self._last_failure_time = None
        self._last_state_change = datetime.utcnow()
        self.logger.info(f"Circuit {self.name}: Manual reset")
    
    async def __aenter__(self) -> "CircuitBreaker":
        """Async context manager entry."""
        if not self.allow_request():
            raise CircuitOpenError(
                f"Circuit {self.name} is {self.state.value}"
            )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if exc_type is not None:
            # Check if exception should be excluded
            if exc_type in self.config.exclude_exceptions:
                return
            self._record_failure()
        else:
            self._record_success()


class CircuitOpenError(Exception):
    """Exception raised when circuit is open."""
    pass


class CircuitBreakerManager:
    """
    Manages multiple circuit breakers.
    
    Provides centralized access to circuit breakers for different services.
    """
    
    _breakers: Dict[str, CircuitBreaker] = {}
    
    @classmethod
    def get(cls, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """
        Get or create a circuit breaker.
        
        Args:
            name: Circuit breaker name (usually service name)
            config: Optional configuration
            
        Returns:
            CircuitBreaker instance
        """
        if name not in cls._breakers:
            cls._breakers[name] = CircuitBreaker(
                name=name,
                config=config or CircuitBreakerConfig.from_env(),
            )
        return cls._breakers[name]
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers."""
        return {name: breaker.get_stats() for name, breaker in cls._breakers.items()}
    
    @classmethod
    def reset_all(cls) -> None:
        """Reset all circuit breakers."""
        for breaker in cls._breakers.values():
            breaker.reset()
        cls._breakers.clear()


def circuit_breaker(
    name: Optional[str] = None,
    config: Optional[CircuitBreakerConfig] = None,
):
    """
    Decorator for applying circuit breaker to async functions.
    
    Usage:
        @circuit_breaker("external_api")
        async def call_api():
            return await external_service.call()
    """
    def decorator(func: Callable) -> Callable:
        breaker_name = name or func.__name__
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            breaker = CircuitBreakerManager.get(breaker_name, config)
            
            if not breaker.allow_request():
                raise CircuitOpenError(f"Circuit {breaker_name} is open")
            
            try:
                result = await func(*args, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                breaker.record_failure()
                raise
        
        return wrapper
    return decorator
