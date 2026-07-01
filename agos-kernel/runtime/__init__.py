"""AGOS Runtime - Responsible for loading, managing and executing the Kernel."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class RuntimeState(Enum):
    """Runtime states."""
    INITIALIZED = "initialized"
    LOADED = "loaded"
    VALIDATED = "validated"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    SHUTDOWN = "shutdown"
    ERROR = "error"


@dataclass
class RuntimeConfiguration:
    """Runtime configuration."""
    kernel_path: str = ""
    auto_start: bool = True
    validation_enabled: bool = True
    max_missions: int = 100
    timeout_seconds: int = 300
    log_level: str = "INFO"


@dataclass
class RuntimeServices:
    """Runtime services."""
    kernel: Any = None
    scheduler: Any = None
    resource_manager: Any = None
    state_store: Any = None
    observability: Any = None


@dataclass
class RuntimeLifecycle:
    """Runtime lifecycle state."""
    state: RuntimeState = RuntimeState.INITIALIZED
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    error: Optional[str] = None


@dataclass
class RuntimeReady:
    """Runtime ready output."""
    success: bool
    version: str = "1.0.0"
    state: RuntimeState = RuntimeState.INITIALIZED
    loaded_capabilities: List[str] = field(default_factory=list)
    loaded_providers: List[str] = field(default_factory=list)
    startup_time_ms: int = 0
    error: Optional[str] = None


class RuntimeHost:
    """
    Runtime Host.
    Responsible for loading and managing the Kernel.
    """
    
    def __init__(self, configuration: RuntimeConfiguration = None):
        self.configuration = configuration or RuntimeConfiguration()
        self.services = RuntimeServices()
        self.lifecycle = RuntimeLifecycle()
        self._started_at: Optional[datetime] = None
    
    def initialize(self) -> None:
        """Initialize the runtime."""
        self.lifecycle.state = RuntimeState.INITIALIZED
    
    def load(self) -> None:
        """Load the kernel."""
        self.lifecycle.state = RuntimeState.LOADED
    
    def validate(self) -> None:
        """Validate the runtime."""
        self.lifecycle.state = RuntimeState.VALIDATED
    
    def start(self) -> None:
        """Start the runtime."""
        self.lifecycle.state = RuntimeState.RUNNING
        self._started_at = datetime.utcnow()
    
    def pause(self) -> None:
        """Pause the runtime."""
        if self.lifecycle.state == RuntimeState.RUNNING:
            self.lifecycle.state = RuntimeState.PAUSED
    
    def resume(self) -> None:
        """Resume the runtime."""
        if self.lifecycle.state == RuntimeState.PAUSED:
            self.lifecycle.state = RuntimeState.RUNNING
    
    def stop(self) -> None:
        """Stop the runtime."""
        self.lifecycle.state = RuntimeState.STOPPING
    
    def shutdown(self) -> None:
        """Shutdown the runtime."""
        self.lifecycle.state = RuntimeState.SHUTDOWN
        self.lifecycle.stopped_at = datetime.utcnow()
    
    def get_state(self) -> RuntimeState:
        """Get current runtime state."""
        return self.lifecycle.state
    
    def is_ready(self) -> bool:
        """Check if runtime is ready."""
        return self.lifecycle.state in [RuntimeState.READY, RuntimeState.RUNNING]
    
    def get_uptime_ms(self) -> int:
        """Get runtime uptime in milliseconds."""
        if not self._started_at:
            return 0
        return int((datetime.utcnow() - self._started_at).total_seconds() * 1000)


class RuntimeManager:
    """
    Runtime Manager.
    Manages the runtime lifecycle.
    """
    
    def __init__(self, host: RuntimeHost = None):
        self.host = host or RuntimeHost()
        self._ready: Optional[RuntimeReady] = None
    
    def boot(self) -> RuntimeReady:
        """Boot the runtime."""
        start_time = datetime.utcnow()
        
        try:
            # Lifecycle
            self.host.initialize()
            self.host.load()
            self.host.validate()
            self.host.start()
            
            startup_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            self._ready = RuntimeReady(
                success=True,
                version="1.0.0",
                state=RuntimeState.READY,
                startup_time_ms=startup_time_ms
            )
            
            return self._ready
            
        except Exception as e:
            startup_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            self.host.lifecycle.state = RuntimeState.ERROR
            self.host.lifecycle.error = str(e)
            
            return RuntimeReady(
                success=False,
                error=str(e),
                startup_time_ms=startup_time_ms
            )
    
    def shutdown(self) -> None:
        """Shutdown the runtime."""
        self.host.stop()
        self.host.shutdown()
    
    def get_ready(self) -> Optional[RuntimeReady]:
        """Get the ready status."""
        return self._ready
