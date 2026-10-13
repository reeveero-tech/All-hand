#!/usr/bin/env python3
"""
Core agent framework for the AGOS civilization.

Compared with the previous revision, `BaseAgent` now installs a class-level
hook that wraps every subclass's `_process_task` so real, non-simulated code
always runs first. This means the ~1000 specialist files inherit real
behaviour without needing individual edits.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional
import asyncio
import functools
import inspect
import logging
import uuid

if TYPE_CHECKING:
    from agent_civilization.core.agents.communication_hub import CommunicationHub

logger = logging.getLogger('base_agent')


@dataclass
class Message:
    sender: str
    recipient: str
    content: Dict[str, Any]
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: 0.0)
    reply_to: Optional[str] = None

    def __post_init__(self):
        if self.timestamp == 0.0:
            try:
                self.timestamp = asyncio.get_running_loop().time()
            except RuntimeError:
                self.timestamp = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "content": self.content,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "reply_to": self.reply_to,
        }


def _wrap_process_task(fn: Callable[..., Awaitable[Any]]):
    """Wrap a subclass `_process_task` so real dispatcher runs first."""
    if getattr(fn, "__real_wrapped__", False):
        return fn

    @functools.wraps(fn)
    async def wrapper(self, work: Dict[str, Any]):
        # Import lazily to avoid circular imports at module load.
        try:
            from agent_civilization.core.agents.real_dispatcher import dispatch
        except Exception:  # pragma: no cover
            dispatch = None  # type: ignore
        spec = getattr(self, "specialization", None)
        if dispatch is not None and isinstance(spec, str):
            real = await dispatch(spec, work if isinstance(work, dict) else {"prompt": str(work)})
            if real is not None:
                # The real dispatcher is authoritative even when it returns a
                # validation/tool error. Falling back to generated template code
                # would reintroduce simulated answers.
                return real
        # Last-resort compatibility for legacy subclasses with unknown specs.
        return await fn(self, work)

    wrapper.__real_wrapped__ = True  # type: ignore[attr-defined]
    return wrapper


class BaseAgent(ABC):
    def __init__(self, name: str, communication_hub: 'CommunicationHub',
                 capacity: int = 100, **kwargs):
        self.name = name
        self.communication_hub = communication_hub
        self.capacity = capacity
        self.is_active = False
        self.status = "initialized"
        self.workqueue: asyncio.Queue = asyncio.Queue()
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.memory: Dict[str, Any] = {}
        self._run_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.start_time = datetime.now()
        self.performance_metrics = {
            "tasks_completed": 0,
            "tasks_failed": 0,
            "messages_processed": 0,
            "errors": [],
        }
        logger.debug(f"Agent {name} initialized")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        fn = cls.__dict__.get("_process_task")
        if fn is not None and inspect.iscoroutinefunction(fn):
            cls._process_task = _wrap_process_task(fn)  # type: ignore[assignment]

    def start(self):
        if self.is_active:
            return
        self.is_active = True
        self.status = "running"
        self._run_task = asyncio.create_task(self._run())

    async def _run(self):
        while self.is_active:
            try:
                try:
                    msg = await asyncio.wait_for(self._inbox.get(), timeout=0.1)
                    await self._handle_message(msg)
                    self._inbox.task_done()
                    self.performance_metrics["messages_processed"] += 1
                except asyncio.TimeoutError:
                    pass
                try:
                    work = await asyncio.wait_for(self.workqueue.get(), timeout=0.05)
                    await self._execute_work(work)
                    self.workqueue.task_done()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error in agent {self.name}: {e}")
                self.performance_metrics["errors"].append(str(e))
                await asyncio.sleep(0.1)

    async def execute(self, work: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous-style helper used by the orchestrator/pipeline."""
        # If the subclass exposes _process_task, use it (already real-wrapped).
        pt = getattr(self, "_process_task", None)
        if pt and inspect.iscoroutinefunction(pt):
            t0 = asyncio.get_event_loop().time()
            self.status = "working"
            try:
                result = await pt(work)
                # R-032: propagate the real outcome. Previously this returned
                # ok=True even when the dispatcher reported a failure.
                ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
                if ok:
                    self.performance_metrics["tasks_completed"] += 1
                else:
                    self.performance_metrics["tasks_failed"] += 1
                elapsed = asyncio.get_event_loop().time() - t0
                return {"ok": ok, "agent": self.name,
                        "elapsed_s": round(elapsed, 3), "result": result}
            except Exception as e:  # noqa: BLE001
                self.performance_metrics["tasks_failed"] += 1
                return {"ok": False, "agent": self.name,
                        "result": {"error": f"{type(e).__name__}: {e}"}}
            finally:
                self.status = "idle"
        return {"ok": False, "agent": self.name,
                "result": {"error": "agent has no _process_task"}}

    async def _execute_work(self, work_item: Dict[str, Any]):
        try:
            await self.execute(work_item)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Work execution error in {self.name}: {e}")
            self.performance_metrics["tasks_failed"] += 1

    async def _handle_message(self, message: Message):
        content = message.content or {}
        action = content.get("action") or content.get("type")
        if action in {"status_request", "status", "ping"}:
            await self.send_to(message.sender, {"action": "status", "status": self.get_status()}, reply_to=message.message_id)
        elif action == "task":
            self.add_work({**content, "original_sender": message.sender})
        else:
            self.memory.setdefault("messages", []).append(message.to_dict())

    def add_work(self, work_item: Dict[str, Any]):
        if not self.is_active:
            return
        self.workqueue.put_nowait(work_item)

    def deliver_message(self, message: Message):
        if self.is_active:
            self._inbox.put_nowait(message)

    async def send_to(self, recipient: str, content: Dict[str, Any],
                      reply_to: Optional[str] = None) -> Message:
        msg = Message(sender=self.name, recipient=recipient, content=content, reply_to=reply_to)
        await self.communication_hub.send_message(msg)
        return msg

    async def broadcast(self, content: Dict[str, Any]):
        msg = Message(sender=self.name, recipient="broadcast", content=content)
        await self.communication_hub.broadcast(msg)

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "is_active": self.is_active,
            "queue_size": self.workqueue.qsize(),
            "inbox_size": self._inbox.qsize(),
            "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
            "performance": self.performance_metrics.copy(),
        }

    async def shutdown_async(self):
        self.is_active = False
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass

    def shutdown(self):
        if self.is_active:
            asyncio.create_task(self.shutdown_async())

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name} status={self.status}>"
