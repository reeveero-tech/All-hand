"""R-159 (D-071): safe, correlated telemetry — structured span logs + bounded metrics.

One trace per HTTP request (``TelemetryMiddleware``) or per detached root
(a background task started outside a request, a test). Inside it, spans of
eight kinds are opened by the code that really runs them:

    http · task · workflow · agent · tool · memory · llm · storage

Every finished span emits ONE structured record (``SCHEMA``) to the logger
``agos.telemetry`` (a JSON object, keys sorted) and two metric series:
``agos_ops_total{kind,op,outcome}`` and ``agos_op_duration_seconds{kind,op}``.

Data minimisation is enforced here, not left to callers:

* Records carry only fixed keys (``BASE_FIELDS``) plus the per-kind allowlist
  (``EXTRA_FIELDS``); any other key passed by a caller is dropped.
* String values must match ``_SAFE_VALUE`` (identifier-like, no spaces, no
  quotes, <= 80 chars) or they are replaced by ``"<redacted>"``. Prompts,
  model output, user content, URLs with credentials, raw exception messages
  and auth headers cannot pass that pattern, so they never reach a record.
* Errors are reported by exception TYPE name only (``error_type``).
* The tenant is a keyed pseudonym (``tenant_ref``): HMAC-SHA256 with
  ``AGOS_TELEMETRY_TENANT_KEY`` if set (stable across processes), otherwise a
  random per-process key (stable only within the process). The raw tenant id
  is never written. No new secret is required.
* Metric labels are only ``kind`` (8 values), ``op`` (fixed allowlists or a
  per-kind cap of ``OP_CAP`` distinct values, then ``"other"``) and
  ``outcome`` (5 values). trace_id / task_id / span_id / tenant are NEVER
  labels, so the number of series does not grow with requests or tasks.

``AGOS_TELEMETRY=off`` (also 0/false/no) disables records and metrics; spans
then cost one environment lookup. Default: on.
"""
from __future__ import annotations

import asyncio
import collections
import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

SCHEMA = "agos.telemetry/1"
KINDS = ("http", "task", "workflow", "agent", "tool", "memory", "llm", "storage")
OUTCOMES = ("ok", "fail", "error", "cancelled", "skipped")
BASE_FIELDS = ("schema", "ts", "kind", "op", "outcome", "duration_ms", "trace_id", "span_id",
               "parent_span_id", "task_id", "tenant", "error_type")
EXTRA_FIELDS: Dict[str, tuple] = {
    "http": ("method", "route", "status"),
    "task": ("specialization", "phase"),
    "workflow": ("stage", "stage_index", "stage_count", "specialization"),
    "agent": ("impl", "agent", "specialization"),
    "tool": ("tool",),
    "memory": ("recalled", "stored", "reason"),
    "llm": ("provider", "model", "http_status", "reason"),
    "storage": ("http_status", "reason"),
}
# fixed op vocabularies; kinds absent here use the OP_CAP guard
ALLOWED_OPS: Dict[str, frozenset] = {
    "task": frozenset({"queued", "run", "recorded"}),
    "workflow": frozenset({"stage"}),
    "memory": frozenset({"recall", "record"}),
    "llm": frozenset({"openrouter", "keyless", "none"}),
    "storage": frozenset({"insert_task", "update_task", "get_tasks", "get_task", "upsert_agent_stats",
                          "get_agent_stats", "insert_event", "get_events"}),
}
OP_CAP = 64
REDACTED = "<redacted>"
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/{}<>+\-]{0,80}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX = re.compile(r"^[0-9a-f]{16,32}$")
_TYPE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

logger = logging.getLogger("agos.telemetry")
RING_SIZE = 512
_RING: Deque[Dict[str, Any]] = collections.deque(maxlen=RING_SIZE)
_SEEN_OPS: Dict[str, set] = {}
_PROCESS_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    task_id: Optional[str] = None


_CTX: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar("agos_trace", default=None)
_SPAN: contextvars.ContextVar[Optional["Span"]] = contextvars.ContextVar("agos_span", default=None)


def enabled() -> bool:
    return os.environ.get("AGOS_TELEMETRY", "on").strip().lower() not in ("0", "off", "false", "no")


def new_trace_id() -> str:
    return secrets.token_hex(16)


def current() -> Optional[TraceContext]:
    return _CTX.get()


def current_span() -> Optional["Span"]:
    return _SPAN.get()


def start_trace(task_id: Optional[str] = None) -> contextvars.Token:
    """Begin a fresh trace in the current context (reset with ``end_trace``)."""
    _SPAN.set(None)
    return _CTX.set(TraceContext(new_trace_id(), _safe_id(task_id)))


def end_trace(token: contextvars.Token) -> None:
    _CTX.reset(token)


def bind_task(task_id: str) -> None:
    """Attach ``task_id`` to the current trace from here on (this context only:
    a copy, so a parent request or sibling task is never modified)."""
    ctx = _CTX.get()
    tid = _safe_id(task_id)
    if ctx is None:
        _CTX.set(TraceContext(new_trace_id(), tid))
    else:
        _CTX.set(replace(ctx, task_id=tid))


def adopt_trace(trace_id: str, task_id: Optional[str] = None) -> contextvars.Token:
    """R-159/R-188: CONTINUE an existing trace in this context (reset with
    ``end_trace``). The durable queue is a boundary the request's trace must
    survive: the accept stores its trace_id inside the task row's payload
    (reserved ``_agos_trace_id`` key), and the dispatcher adopts it before
    creating the run's spans — so the request and the run stay ONE trace,
    preserving R-159's background-task guarantee through the queue (works
    across processes too: the trace_id lives in the row)."""
    _SPAN.set(None)
    return _CTX.set(TraceContext(str(trace_id), _safe_id(task_id)))


def tenant_ref(tenant: Any = None, *, _lookup: bool = True) -> str:
    if tenant is None and _lookup:
        try:
            from agent_civilization.core import tenancy
            tenant = tenancy.current_tenant()
        except Exception:  # noqa: BLE001
            tenant = None
    if tenant is None:
        return "none"
    if not isinstance(tenant, str):
        return "admin"            # tenancy.ALL_TENANTS (admin scope)
    key = os.environ.get("AGOS_TELEMETRY_TENANT_KEY", "").encode() or _PROCESS_KEY
    return "t_" + hmac.new(key, tenant.encode(), hashlib.sha256).hexdigest()[:12]


def _safe_id(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _ID.match(value) else None


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str) and _SAFE_VALUE.match(value):
        return value
    return REDACTED


def error_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if _TYPE_NAME.match(name) else "Exception"


def bounded_op(kind: str, op: Any) -> str:
    """The metric label value for ``op``: never unbounded, never secret."""
    if not isinstance(op, str) or not _SAFE_VALUE.match(op) or not op:
        return "other"
    allowed = ALLOWED_OPS.get(kind)
    if allowed is not None:
        return op if op in allowed else "other"
    seen = _SEEN_OPS.setdefault(kind, set())
    if op in seen:
        return op
    if len(seen) >= OP_CAP:
        return "other"
    seen.add(op)
    return op


def _record(kind: str, op: str, outcome: str, duration_s: float, fields: Dict[str, Any],
            span_id: str, parent: Optional[str], err: Optional[str]) -> Dict[str, Any]:
    ctx = _CTX.get()
    rec: Dict[str, Any] = {
        "schema": SCHEMA,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "kind": kind,
        "op": _safe_value(op),
        "outcome": outcome if outcome in OUTCOMES else "error",
        "duration_ms": round(duration_s * 1000.0, 3),
        "trace_id": ctx.trace_id if ctx else None,
        "span_id": span_id,
        "parent_span_id": parent,
        "task_id": ctx.task_id if ctx else None,
        "tenant": tenant_ref(),
        "error_type": err,
    }
    allowed = EXTRA_FIELDS.get(kind, ())
    for k, v in fields.items():
        if k in allowed:
            rec[k] = _safe_value(v)
    return rec


def _emit(rec: Dict[str, Any], duration_s: float) -> None:
    _RING.append(rec)
    try:
        from agos_kernel.infrastructure.metrics import get_metrics
        m = get_metrics()
        op = bounded_op(rec["kind"], rec["op"])
        m.inc_counter("ops_total", labels={"kind": rec["kind"], "op": op, "outcome": rec["outcome"]})
        m.observe_histogram("op_duration_seconds", duration_s, labels={"kind": rec["kind"], "op": op})
    except Exception:  # noqa: BLE001 — telemetry never breaks the operation
        pass
    if logger.isEnabledFor(logging.INFO):
        logger.info(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


class Span:
    """Context manager (sync form, usable inside async code)."""

    __slots__ = ("kind", "op", "fields", "outcome", "err", "span_id", "parent", "_t0", "_tok",
                 "_ctx_tok", "_on")

    def __init__(self, kind: str, op: str, **fields: Any):
        if kind not in KINDS:
            raise ValueError(f"unknown telemetry kind {kind!r}")
        self.kind, self.op, self.fields = kind, op, fields
        self.outcome: Optional[str] = None
        self.err: Optional[str] = None
        self.span_id = ""
        self.parent: Optional[str] = None
        self._tok = self._ctx_tok = None
        self._on = False

    def set(self, outcome: Optional[str] = None, error: Optional[BaseException] = None, **fields: Any) -> "Span":
        if outcome is not None:
            self.outcome = outcome
        if error is not None:
            self.err = error_type(error)
            if self.outcome is None or self.outcome == "ok":
                self.outcome = "error"
        self.fields.update(fields)
        return self

    def __enter__(self) -> "Span":
        self._on = enabled()
        if not self._on:
            return self
        if _CTX.get() is None:
            self._ctx_tok = _CTX.set(TraceContext(new_trace_id()))
        parent = _SPAN.get()
        self.parent = parent.span_id if parent is not None else None
        self.span_id = secrets.token_hex(8)
        self._tok = _SPAN.set(self)
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._on:
            return False
        duration = time.perf_counter() - self._t0
        try:
            if exc is not None:
                if isinstance(exc, asyncio.CancelledError):
                    self.outcome = "cancelled"
                    self.err = self.err or "CancelledError"
                else:
                    self.outcome = "error"
                    self.err = error_type(exc)
            rec = _record(self.kind, self.op, self.outcome or "ok", duration, self.fields,
                          self.span_id, self.parent, self.err)
            _emit(rec, duration)
        finally:
            _SPAN.reset(self._tok)
            if self._ctx_tok is not None:
                _CTX.reset(self._ctx_tok)
        return False


def span(kind: str, op: str, **fields: Any) -> Span:
    return Span(kind, op, **fields)


def event(kind: str, op: str, outcome: str = "ok", **fields: Any) -> None:
    """A zero-duration span (lifecycle transition, skipped operation)."""
    if not enabled():
        return
    parent = _SPAN.get()
    rec = _record(kind, op, outcome, 0.0, fields, secrets.token_hex(8),
                  parent.span_id if parent is not None else None, None)
    _emit(rec, 0.0)


def annotate(outcome: Optional[str] = None, kind: Optional[str] = None, **fields: Any) -> None:
    """Set fields on the current span (only if it is of ``kind`` when given)."""
    sp = _SPAN.get()
    if sp is not None and sp._on and (kind is None or sp.kind == kind):
        sp.set(outcome=outcome, **fields)


def recent(n: int = RING_SIZE) -> list:
    """The last ``n`` records (bounded in-process ring; diagnostics/tests)."""
    return list(_RING)[-n:]


def reset_for_tests() -> None:
    _RING.clear()
    _SEEN_OPS.clear()


class TelemetryMiddleware:
    """Pure ASGI: one fresh trace + one ``http`` span per request. The context
    is reset in ``finally`` (also on error/cancel), so nothing is inherited by
    a later request. Health and /metrics probes are not traced (noise)."""

    SKIP_PREFIXES = ("/metrics", "/health")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled() or scope.get("path", "").startswith(self.SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        token = start_trace()
        sp = Span("http", "request", method=scope.get("method", "GET"))
        try:
            with sp:
                try:
                    await self.app(scope, receive, send_wrapper)
                finally:
                    route = scope.get("route")
                    tmpl = getattr(route, "path_format", None) or getattr(route, "path", None) or "<unmatched>"
                    code = status["code"]
                    sp.op = tmpl
                    sp.set(outcome="ok" if code < 400 else ("fail" if code < 500 else "error"),
                           route=tmpl, status=code)
        finally:
            end_trace(token)
