"""
Redis-based Distributed Task Queue

Provides reliable task queue with Redis as the backend.
Features:
- Atomic operations
- Persistent storage
- Distributed workers support
- Task priorities
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

logger = logging.getLogger("agos.infrastructure.redis")

# Try to import redis, provide fallback if not available
try:
    import redis.asyncio as redis_async
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available. Using in-memory fallback.")


class TaskStatus(str, Enum):
    """Task status enumeration."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Represents a task in the queue."""
    id: str
    specialization: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    data: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "id": self.id,
            "specialization": self.specialization,
            "prompt": self.prompt,
            "status": self.status.value,
            "data": self.data,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "priority": self.priority,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create task from dictionary."""
        return cls(
            id=data["id"],
            specialization=data["specialization"],
            prompt=data["prompt"],
            status=TaskStatus(data.get("status", "pending")),
            data=data.get("data"),
            result=data.get("result"),
            error=data.get("error"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            priority=data.get("priority", 0),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
        )


class RedisQueue:
    """
    Redis-based distributed task queue.
    
    Features:
    - Atomic task enqueueing
    - Reliable task processing
    - Automatic retry with backoff
    - Priority queue support
    - Distributed worker support
    """
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: str = "agos:tasks",
        results_name: str = "agos:results",
        default_timeout: int = 300,
        max_retries: int = 3,
    ):
        """
        Initialize Redis queue.
        
        Args:
            redis_url: Redis connection URL (defaults to env var)
            queue_name: Name of the task queue
            results_name: Name of the results hash
            default_timeout: Default task timeout in seconds
            max_retries: Maximum retry attempts per task
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.queue_name = queue_name
        self.results_name = results_name
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        
        self._redis: Optional[Any] = None
        self._connected = False
        self._use_fallback = not REDIS_AVAILABLE
        
        # In-memory fallback
        self._fallback_tasks: Dict[str, Task] = {}
        self._fallback_queue: asyncio.Queue = asyncio.Queue()
        
        logger.info(f"RedisQueue initialized with URL: {self.redis_url}")
        logger.info(f"Using Redis: {REDIS_AVAILABLE}, Fallback: {self._use_fallback}")
    
    async def connect(self) -> bool:
        """Establish Redis connection."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available, using in-memory fallback")
            return True
        
        try:
            self._redis = redis_async.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._redis.ping()
            self._connected = True
            logger.info("Connected to Redis successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._use_fallback = True
            return False
    
    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("Disconnected from Redis")
    
    async def enqueue(
        self,
        specialization: str,
        prompt: str,
        data: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        task_id: Optional[str] = None,
    ) -> str:
        """
        Add a task to the queue.
        
        Args:
            specialization: Agent specialization
            prompt: Task prompt
            data: Optional task data
            priority: Task priority (higher = more urgent)
            task_id: Optional custom task ID
            
        Returns:
            Task ID
        """
        task_id = task_id or str(uuid4())[:12]
        
        task = Task(
            id=task_id,
            specialization=specialization,
            prompt=prompt,
            data=data,
            priority=priority,
            status=TaskStatus.QUEUED,
        )
        
        if self._use_fallback:
            self._fallback_tasks[task_id] = task
            await self._fallback_queue.put(task)
            logger.info(f"Task {task_id} added to fallback queue")
        else:
            try:
                # Store task data
                await self._redis.hset(
                    self.results_name,
                    task_id,
                    json.dumps(task.to_dict()),
                )
                
                # Add to sorted set with priority as score
                # Use negative priority so higher priority comes first
                await self._redis.zadd(
                    self.queue_name,
                    {task_id: -priority},
                )
                
                logger.info(f"Task {task_id} enqueued with priority {priority}")
            except Exception as e:
                logger.error(f"Failed to enqueue task: {e}")
                # Fallback to memory
                self._fallback_tasks[task_id] = task
                await self._fallback_queue.put(task)
        
        return task_id
    
    async def dequeue(self, timeout: int = 5) -> Optional[Task]:
        """
        Get the next task from the queue.
        
        Args:
            timeout: Timeout in seconds
            
        Returns:
            Task object or None if timeout
        """
        if self._use_fallback:
            try:
                task = await asyncio.wait_for(
                    self._fallback_queue.get(),
                    timeout=timeout,
                )
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.utcnow()
                task.attempts += 1
                return task
            except asyncio.TimeoutError:
                return None
        
        try:
            # Get highest priority task
            result = await self._redis.zpopmin(self.queue_name, count=1)
            
            if not result:
                # Wait for new tasks
                await self._redis.blpop(self.queue_name, timeout=timeout)
                result = await self._redis.zpopmin(self.queue_name, count=1)
            
            if not result:
                return None
            
            task_id = result[0][0]
            task_data = await self._redis.hget(self.results_name, task_id)
            
            if not task_data:
                return None
            
            task = Task.from_dict(json.loads(task_data))
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow()
            task.attempts += 1
            
            await self._redis.hset(
                self.results_name,
                task_id,
                json.dumps(task.to_dict()),
            )
            
            logger.info(f"Task {task_id} dequeued")
            return task
            
        except Exception as e:
            logger.error(f"Failed to dequeue task: {e}")
            return None
    
    async def complete(
        self,
        task_id: str,
        result: Dict[str, Any],
        status: TaskStatus = TaskStatus.COMPLETED,
    ) -> None:
        """
        Mark a task as completed.
        
        Args:
            task_id: Task ID
            result: Task result
            status: Final status
        """
        if task_id in self._fallback_tasks:
            task = self._fallback_tasks[task_id]
            task.result = result
            task.status = status
            task.completed_at = datetime.utcnow()
            logger.info(f"Task {task_id} marked as {status.value}")
            return
        
        try:
            task_data = await self._redis.hget(self.results_name, task_id)
            if task_data:
                task = Task.from_dict(json.loads(task_data))
                task.result = result
                task.status = status
                task.completed_at = datetime.utcnow()
                
                await self._redis.hset(
                    self.results_name,
                    task_id,
                    json.dumps(task.to_dict()),
                )
                
                logger.info(f"Task {task_id} completed with status {status.value}")
        except Exception as e:
            logger.error(f"Failed to complete task: {e}")
    
    async def fail(self, task_id: str, error: str) -> bool:
        """
        Mark a task as failed.
        
        Args:
            task_id: Task ID
            error: Error message
            
        Returns:
            True if task should be retried
        """
        if task_id in self._fallback_tasks:
            task = self._fallback_tasks[task_id]
            task.error = error
            
            if task.attempts < task.max_attempts:
                task.status = TaskStatus.PENDING
                await self._fallback_queue.put(task)
                logger.info(f"Task {task_id} failed, retrying (attempt {task.attempts})")
                return True
            
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            logger.info(f"Task {task_id} permanently failed after {task.attempts} attempts")
            return False
        
        try:
            task_data = await self._redis.hget(self.results_name, task_id)
            if task_data:
                task = Task.from_dict(json.loads(task_data))
                task.error = error
                
                if task.attempts < task.max_attempts:
                    # Requeue with backoff
                    task.status = TaskStatus.PENDING
                    await asyncio.sleep(2 ** task.attempts)  # Exponential backoff
                    await self.zadd(self.queue_name, {task_id: task.priority})
                    logger.info(f"Task {task_id} failed, retrying (attempt {task.attempts})")
                else:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.utcnow()
                    logger.info(f"Task {task_id} permanently failed")
                
                await self._redis.hset(
                    self.results_name,
                    task_id,
                    json.dumps(task.to_dict()),
                )
                
                return task.attempts < task.max_attempts
                
        except Exception as e:
            logger.error(f"Failed to fail task: {e}")
        
        return False
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        if task_id in self._fallback_tasks:
            return self._fallback_tasks[task_id]
        
        try:
            task_data = await self._redis.hget(self.results_name, task_id)
            if task_data:
                return Task.from_dict(json.loads(task_data))
        except Exception as e:
            logger.error(f"Failed to get task: {e}")
        
        return None
    
    async def get_queue_size(self) -> int:
        """Get current queue size."""
        if self._use_fallback:
            return self._fallback_queue.qsize()
        
        try:
            return await self._redis.zcard(self.queue_name)
        except Exception as e:
            logger.error(f"Failed to get queue size: {e}")
            return 0
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        fallback_size = len(self._fallback_tasks)
        
        if self._use_fallback:
            completed = sum(
                1 for t in self._fallback_tasks.values()
                if t.status == TaskStatus.COMPLETED
            )
            failed = sum(
                1 for t in self._fallback_tasks.values()
                if t.status == TaskStatus.FAILED
            )
            return {
                "connected": False,
                "using_fallback": True,
                "queue_size": fallback_size,
                "total_tasks": len(self._fallback_tasks),
                "completed": completed,
                "failed": failed,
            }
        
        try:
            total = await self._redis.zcard(self.queue_name)
            
            completed = 0
            failed = 0
            running = 0
            
            async for task_id in self._redis.hscan_iter(self.results_name):
                task_data = await self._redis.hget(self.results_name, task_id)
                if task_data:
                    task = Task.from_dict(json.loads(task_data))
                    if task.status == TaskStatus.COMPLETED:
                        completed += 1
                    elif task.status == TaskStatus.FAILED:
                        failed += 1
                    elif task.status == TaskStatus.RUNNING:
                        running += 1
            
            return {
                "connected": self._connected,
                "using_fallback": False,
                "queue_size": total,
                "total_tasks": total + completed + failed,
                "completed": completed,
                "failed": failed,
                "running": running,
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"error": str(e)}


# Global instance
_redis_queue: Optional[RedisQueue] = None


def get_redis_queue() -> RedisQueue:
    """Get or create global Redis queue instance."""
    global _redis_queue
    if _redis_queue is None:
        _redis_queue = RedisQueue()
    return _redis_queue
