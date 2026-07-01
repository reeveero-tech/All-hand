"""Runtime Resource Manager - Tracks and manages system resources."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ResourceLimits:
    """Resource limits configuration."""
    max_cpu_percent: float = 100.0
    max_memory_mb: float = 4096.0
    max_execution_time_seconds: int = 300
    max_concurrent_missions: int = 10
    max_queue_size: int = 1000
    max_running_tasks: int = 20


@dataclass
class ExecutionQuota:
    """Execution quota for a mission."""
    mission_id: str
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    execution_time_ms: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ResourceAllocation:
    """Resource allocation record."""
    resource_id: str
    mission_id: str
    cpu_percent: float
    memory_mb: float
    allocated_at: datetime = field(default_factory=datetime.utcnow)
    released_at: Optional[datetime] = None


@dataclass
class ResourceUsage:
    """Current resource usage."""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    execution_time_ms: int = 0
    concurrent_missions: int = 0
    queue_size: int = 0
    running_tasks: int = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ResourceMonitor:
    """Monitors resource usage."""
    
    def __init__(self):
        self._allocations: Dict[str, ResourceAllocation] = {}
        self._quotas: Dict[str, ExecutionQuota] = {}
        self._usage_history: List[ResourceUsage] = []
    
    def allocate(self, resource_id: str, mission_id: str, cpu_percent: float, memory_mb: float) -> bool:
        """Allocate resources for a mission."""
        allocation = ResourceAllocation(
            resource_id=resource_id,
            mission_id=mission_id,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb
        )
        self._allocations[resource_id] = allocation
        return True
    
    def release(self, resource_id: str) -> None:
        """Release resources."""
        if resource_id in self._allocations:
            self._allocations[resource_id].released_at = datetime.utcnow()
            del self._allocations[resource_id]
    
    def get_usage(self) -> ResourceUsage:
        """Get current resource usage."""
        cpu = sum(a.cpu_percent for a in self._allocations.values() if not a.released_at)
        memory = sum(a.memory_mb for a in self._allocations.values() if not a.released_at)
        
        return ResourceUsage(
            cpu_percent=cpu,
            memory_mb=memory,
            concurrent_missions=len([a for a in self._allocations.values() if not a.released_at])
        )
    
    def set_quota(self, mission_id: str, quota: ExecutionQuota) -> None:
        """Set execution quota for a mission."""
        self._quotas[mission_id] = quota
    
    def get_quota(self, mission_id: str) -> Optional[ExecutionQuota]:
        """Get execution quota for a mission."""
        return self._quotas.get(mission_id)
    
    def record_usage(self, usage: ResourceUsage) -> None:
        """Record usage snapshot."""
        self._usage_history.append(usage)
        # Keep only last 1000 records
        if len(self._usage_history) > 1000:
            self._usage_history = self._usage_history[-1000:]


class ResourceAllocator:
    """Allocates resources to missions."""
    
    def __init__(self, limits: ResourceLimits = None):
        self._limits = limits or ResourceLimits()
        self._monitor = ResourceMonitor()
    
    def can_allocate(self, cpu_percent: float, memory_mb: float) -> bool:
        """Check if resources can be allocated."""
        current = self._monitor.get_usage()
        
        return (
            current.cpu_percent + cpu_percent <= self._limits.max_cpu_percent and
            current.memory_mb + memory_mb <= self._limits.max_memory_mb and
            current.concurrent_missions < self._limits.max_concurrent_missions
        )
    
    def allocate(self, mission_id: str, cpu_percent: float = 10.0, memory_mb: float = 256.0) -> Optional[str]:
        """Allocate resources for a mission."""
        if not self.can_allocate(cpu_percent, memory_mb):
            return None
        
        resource_id = f"res_{mission_id}"
        self._monitor.allocate(resource_id, mission_id, cpu_percent, memory_mb)
        
        # Set quota
        quota = ExecutionQuota(
            mission_id=mission_id,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb
        )
        self._monitor.set_quota(mission_id, quota)
        
        return resource_id
    
    def release(self, resource_id: str) -> None:
        """Release resources."""
        self._monitor.release(resource_id)
    
    def get_usage(self) -> ResourceUsage:
        """Get current resource usage."""
        return self._monitor.get_usage()
    
    def get_limits(self) -> ResourceLimits:
        """Get resource limits."""
        return self._limits


class ResourceManager:
    """
    Runtime Resource Manager.
    
    Tracks:
    - CPU
    - Memory
    - Execution Time
    - Concurrent Missions
    - Queue Size
    - Running Tasks
    
    Rules:
    ❌ Reject Missions Exceeding Limits
    """
    
    def __init__(self, limits: ResourceLimits = None):
        self._allocator = ResourceAllocator(limits)
        self._monitor = self._allocator._monitor
        self._queue_size = 0
        self._running_tasks = 0
    
    def can_submit_mission(self) -> bool:
        """Check if a mission can be submitted."""
        return self._queue_size < self._allocator._limits.max_queue_size
    
    def can_execute_mission(self) -> bool:
        """Check if a mission can be executed."""
        usage = self._allocator.get_usage()
        return usage.concurrent_missions < self._allocator._limits.max_concurrent_missions
    
    def submit(self) -> None:
        """Record mission submission."""
        self._queue_size += 1
    
    def start_execution(self, mission_id: str, cpu_percent: float = 10.0, memory_mb: float = 256.0) -> Optional[str]:
        """Start mission execution with resource allocation."""
        if self._queue_size > 0:
            self._queue_size -= 1
        
        resource_id = self._allocator.allocate(mission_id, cpu_percent, memory_mb)
        
        if resource_id:
            self._running_tasks += 1
        
        return resource_id
    
    def end_execution(self, resource_id: str) -> None:
        """End mission execution."""
        self._allocator.release(resource_id)
        if self._running_tasks > 0:
            self._running_tasks -= 1
    
    def get_usage(self) -> ResourceUsage:
        """Get current resource usage."""
        usage = self._allocator.get_usage()
        usage.queue_size = self._queue_size
        usage.running_tasks = self._running_tasks
        return usage
    
    def get_limits(self) -> ResourceLimits:
        """Get resource limits."""
        return self._allocator.get_limits()
    
    def reject_mission(self, reason: str) -> Dict[str, Any]:
        """Reject a mission with reason."""
        return {
            "rejected": True,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        }
