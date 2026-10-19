"""R-160 (M8/P2): verifiable service contracts — version 1.

These models DESCRIBE the payloads AGOS exchanges today (read from the code
and measured on the running app — docs/governance/contracts/README.md); they do not
change what the server returns. They are used by:

- ``tests/test_r160_contracts.py``: real responses of the in-process app, the
  agent outcomes of both implementations (real/specialists), the LLM client,
  the queue and the storage payloads are validated against them;
- ``scripts/export_contract_schemas.py``: writes the JSON Schemas under
  ``docs/governance/contracts/`` and, with ``--check``, fails when the code-side contract
  drifts from the committed schema (a breaking change without a version bump
  fails with the list of breaking differences);
- ``web/tests/task_view.test.mjs``: the web client is tested on the same
  example payloads (docs/governance/contracts/examples/) the Python side validates.

Unknown-field policy (explicit, per direction):
- REQUESTS: the server ignores unknown fields (Pydantic default; kept — SEC-02
  relies on it: a ``tenant_id`` in a body is dropped, never honoured). The
  request schemas are therefore ``additionalProperties`` open.
- RESPONSES with a fixed shape: ``extra="forbid"`` here, so a field added or
  removed by the server is detected by the tests (drift), even though clients
  must themselves ignore unknown fields (the web client does — taskView.js).
- Free-form payloads (handler ``tool_output``, ``experience``, the system
  status sub-blocks): typed as open objects on purpose.

No contract is negotiated at runtime (no version header, no trace_id in
responses — both would change the public API and need a decision).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

CONTRACT_SET_VERSION = 1

TaskStatus = Literal["queued", "running", "completed", "failed"]
_TASK_ID = r"^[0-9a-f]{12}$"                       # uuid4().hex[:12] (tasks, chat)
_EXEC_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"  # civilization/execute
_RUN_ID = r"^[0-9a-f]{32}$"  # R-165 DAG run id (uuid4().hex)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------ tool / outcome

class HandlerResult(_Open):
    """What a specialist handler (tool) returns inside ``outcome.result``.
    Measured keys (20/20 specializations, both impls): ok, answer, error,
    tool_output. Handlers may add keys (open), but these four are typed."""
    ok: StrictBool
    answer: Optional[str] = None
    error: Optional[str] = None
    tool_output: Any = None


class AgentOutcome(_Strict):
    """runtime_contract.py (R-157): the result of ``agent.execute`` — the same
    shape under AGOS_AGENT_IMPL=real and =specialists (the latter adds
    ``impl``). ``specialization`` appears only on the outcome _run_task builds
    when no agent could run (BUG-01) or the run ended without one (R-160)."""
    ok: StrictBool
    agent: Optional[str]
    elapsed_s: float = Field(ge=0)
    result: Union[HandlerResult, Dict[str, Any]]
    experience: Optional[Dict[str, Any]] = None
    impl: Optional[str] = None
    specialization: Optional[str] = None


# --------------------------------------------------------------- task records

class TaskAccepted(_Strict):
    """202 of POST /api/tasks."""
    task_id: str = Field(pattern=_TASK_ID)
    status: Literal["queued"]


class TaskAcceptedV2(_Strict):
    """202 of POST /api/tasks — v2 (R-163, D-082).

    An idempotent replay (same ``idempotency_key``) returns the task's CURRENT
    public status — the task may already have progressed past ``queued`` — plus
    ``duplicate: true``. A first creation keeps the v1 shape exactly (status
    ``queued``, no ``duplicate``). """
    task_id: str = Field(pattern=_TASK_ID)
    status: TaskStatus
    duplicate: Optional[bool] = None


class TaskCancelled(_Strict):
    """200 of POST /api/tasks/{task_id}/cancel (R-163, D-082).

    The task is served through the public mapping: ``cancelled`` shows as
    ``failed`` with ``result.result.error = "cancelled"`` — the API status
    vocabulary is unchanged. Terminal tasks answer 409 instead."""
    task_id: str = Field(pattern=_TASK_ID)
    status: Literal["failed"]
    cancelled: Literal[True]
    result: Dict[str, Any]


class TaskRecord(_Strict):
    """A task/chat record from process memory (GET /api/tasks[/{id}], source
    memory). ``agent``/``finished_at`` appear once the run started/ended."""
    id: str = Field(pattern=_TASK_ID)
    specialization: str
    prompt: str = Field(max_length=500)
    status: TaskStatus
    created_at: float
    result: Optional[AgentOutcome]
    tenant_id: str
    agent: Optional[str] = None
    finished_at: Optional[float] = None

    @model_validator(mode="after")
    def _terminal_has_result(self):
        if self.status in ("completed", "failed") and self.result is None:
            raise ValueError("terminal task record without result")
        if self.status == "completed" and self.result is not None and not self.result.ok:
            raise ValueError("completed record with ok=false outcome")
        return self


class ExecuteRecord(_Strict):
    """A /civilization/execute record in the same memory store (another
    variant of /api/tasks items). Status is "completed" when the plan ran,
    whatever its stages' ok (no aggregate ok exists — gap G-160-3)."""
    id: str = Field(pattern=_EXEC_ID)
    message: str
    plan: Dict[str, Any]
    status: Literal["running", "completed", "failed"]
    created_at: float
    tenant_id: str
    result: Optional[Dict[str, Any]] = None
    finished_at: Optional[float] = None
    #: R-165 (D-084): the DAG run behind this record (GET /civilization/runs/{run_id})
    run_id: Optional[str] = Field(default=None, pattern=_RUN_ID)


class TaskRow(_Strict):
    """A ``tasks`` row as PostgREST returns it (docs/sql/001 + 002), i.e. a
    GET /api/tasks item with source=database. GET /api/tasks/{id} adds ``id``
    (= task_id) and ``source``."""
    task_id: str
    specialization: str
    prompt: Optional[str] = None
    status: str
    agent_name: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    elapsed_s: Optional[float] = None
    created_at: str
    finished_at: Optional[str] = None
    tenant_id: Optional[str] = None
    id: Optional[str] = None
    source: Optional[Literal["database"]] = None


class TaskRowV2(_Strict):
    """A ``tasks`` row as PostgREST returns it (docs/sql/001 + 002 + 003),
    i.e. a GET /api/tasks item with source=database. v2 (R-163, D-082): the
    durable-queue columns are part of the row — all optional, because 001/002
    rows and the volatile mode never set them. GET /api/tasks/{id} adds ``id``
    (= task_id) and ``source``."""
    task_id: str
    specialization: str
    prompt: Optional[str] = None
    status: str
    agent_name: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    elapsed_s: Optional[float] = None
    created_at: str
    finished_at: Optional[str] = None
    tenant_id: Optional[str] = None
    id: Optional[str] = None
    source: Optional[Literal["database"]] = None
    # R-163 (D-082) queue columns — optional: rows created before 003 (or in
    # the volatile mode) never set them
    claim_token: Optional[str] = None
    lease_expires_at: Optional[str] = None
    attempts: Optional[int] = None
    max_attempts: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None


class TaskList(_Strict):
    count: int = Field(ge=0)
    tasks: List[Union[TaskRecord, ExecuteRecord, TaskRowV2]]
    source: Literal["memory", "database"]

    @model_validator(mode="after")
    def _count(self):
        if self.count != len(self.tasks):
            raise ValueError("count != len(tasks)")
        return self


class Routing(_Strict):
    method: Literal["llm", "keywords"]
    reason: str


class ChatResponse(_Strict):
    task_id: str = Field(pattern=_TASK_ID)
    routed_to: str
    routing: Routing
    agent: str
    outcome: AgentOutcome


class Stage(_Open):
    name: str
    specialization: str
    ok: StrictBool
    agent: Optional[str] = None


#: R-165 (D-084): a DAG run's aggregate status
RunStatus = Literal["completed", "failed", "awaiting_approval", "timed_out",
                    "budget_exhausted", "cancelled", "running"]


class ExecuteResponse(_Strict):
    task_id: str = Field(pattern=_EXEC_ID)
    primary: str
    reason: str
    stages: List[Stage]
    final: Any = None
    elapsed_s: float = Field(ge=0)
    # R-165 (D-084) — additive, optional: the DAG run report
    run_id: Optional[str] = Field(default=None, pattern=_RUN_ID)
    status: Optional[RunStatus] = None
    pending_approvals: Optional[List[Dict[str, Any]]] = None
    budget: Optional[Dict[str, Any]] = None
    order: Optional[List[str]] = None
    ignored_approvals: Optional[List[str]] = None


class CoordinationReportContract(_Strict):
    """R-166 (D-085, amended D-086): the response of POST /civilization/coordinate.
    A fan-out/fan-in round over the real civilization agents — ok is True only
    when every subtask has a result AND no result reports failure (loss and
    failure detection, never a hidden success). Sensitive specializations
    without an explicit approval are reported as pending_approvals and never
    executed."""
    task_id: str
    ok: bool
    incomplete_reason: Optional[str] = None
    results: Dict[str, Any] = Field(default_factory=dict)
    missing: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)
    pending_approvals: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)


class StageArtifact(_Strict):
    """R-165 (D-084): a typed, schema-validated stage output. Downstream
    stages consume these (``work["inputs"]`` / ``work["artifact"]``) instead
    of a truncated text summary. Exactly the field named by ``kind`` is set."""
    kind: Literal["code", "sql", "analysis", "text"]
    stage: str = Field(min_length=1)
    code: Optional[str] = None
    source_file: Optional[str] = None
    sql: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None
    text: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self):
        others = {"code", "sql", "analysis", "text"} - {self.kind}
        for f in others:
            if getattr(self, f) is not None:
                raise ValueError(f"{self.kind} artifact must not carry {f!r}")
        if self.source_file is not None and self.kind != "code":
            raise ValueError("source_file is only valid on a code artifact")
        val = getattr(self, self.kind)
        if self.kind == "analysis":
            if not isinstance(val, dict):
                raise ValueError("analysis artifact needs an 'analysis' object")
            n, samples, mean = val.get("n"), val.get("samples"), val.get("mean")
            if not (isinstance(n, int) and not isinstance(n, bool) and n >= 0):
                raise ValueError("analysis.n must be a non-negative integer")
            if not isinstance(samples, list):
                raise ValueError("analysis.samples must be a list")
            if mean is not None and (isinstance(mean, bool) or not isinstance(mean, (int, float))):
                raise ValueError("analysis.mean must be a number or null")
        elif not (isinstance(val, str) and val.strip()):
            raise ValueError(f"{self.kind} artifact needs a non-empty {self.kind!r}")
        return self


class ErrorDetail(_Strict):
    """Every HTTPException body (400/401/403/404/409/429/503)."""
    detail: str


class ValidationErrorBody(_Strict):
    """FastAPI 422 body for an invalid request."""
    detail: List[Dict[str, Any]]


# ---------------------------------------------------------------- SSE events

class TaskCreatedEvent(_Strict):
    type: Literal["task_created"]
    task_id: str
    specialization: str


class TaskStartedEvent(_Strict):
    type: Literal["task_started"]
    task_id: str
    agent: str
    specialization: str


class TaskCompletedEvent(_Strict):
    type: Literal["task_completed"]
    task_id: str
    agent: Optional[str]
    specialization: str
    ok: StrictBool
    elapsed_s: float = Field(ge=0)


class HeartbeatEvent(_Strict):
    type: Literal["heartbeat"]


class StreamGapEvent(_Strict):
    type: Literal["stream_gap"]
    missed: int = Field(ge=1)
    recover: List[str]


class SSEFrame(BaseModel):
    """One ``data: <json>\\n\\n`` frame of /api/events/stream. No ``id:`` and
    no ``event:`` field are sent: a reconnect is a NEW subscription with no
    replay (Last-Event-ID is not supported); missed events are signalled by
    stream_gap and recovered from GET /api/tasks and /api/events."""
    event: Union[TaskCreatedEvent, TaskStartedEvent, TaskCompletedEvent,
                 HeartbeatEvent, StreamGapEvent] = Field(discriminator="type")


# -------------------------------------------------------- status / health

class SystemStatus(_Strict):
    status: Literal["healthy"]          # a constant today (the route is a status page, not a probe)
    uptime_s: int = Field(ge=0)
    llm_configured: StrictBool
    llm_model: str
    llm_provider: Literal["openrouter", "keyless", "none"]
    public_llm_enabled: StrictBool
    public_llm_requires_consent: StrictBool
    supabase_configured: StrictBool
    supabase_key: Dict[str, Any]
    specializations: int
    total_agents: int
    external_agents: int
    tasks_completed: int
    tasks_failed: int
    tasks_in_memory: int
    tasks_in_db: Optional[int]
    security: Dict[str, Any]
    experience: Dict[str, Any]


class Ready(_Strict):
    status: Literal["ready"]
    agents: int = Field(ge=1)
    specializations: int


class NotReady(_Strict):
    status: Literal["not_ready"]
    reason: str


# ------------------------------------------------------------------- memory

class MemoryEntry(_Strict):
    memory_id: str
    specialization: str
    agent: Optional[str]
    prompt: str
    answer: str
    ok: StrictBool
    input_kind: str
    created_at: str


class MemoryList(_Strict):
    count: int = Field(ge=0)
    memories: List[MemoryEntry]
    stats: Dict[str, Any]


# ---------------------------------------------------------------------- LLM

class LLMResult(_Strict):
    """``LLMClient.complete()``: never raises; ok=True only with a non-empty
    string content (R-160 — a null/empty/malformed completion is ok=False)."""
    ok: StrictBool
    content: str
    error: Optional[str]
    model: str
    provider: Optional[Literal["keyless"]] = None
    attempts: Optional[int] = None
    # R-164 (additive, optional): ``busy`` marks an admission refusal (R-131
    # keyless queue / R-164 keyed concurrency / open breaker) — it was
    # already returned before R-164 but missing from this strict model;
    # ``retry_after`` = seconds until the provider may be tried again.
    busy: Optional[StrictBool] = None
    retry_after: Optional[int] = None

    @model_validator(mode="after")
    def _ok_means_content(self):
        if self.ok and (not self.content.strip() or self.error is not None):
            raise ValueError("ok=True needs non-empty content and no error")
        if not self.ok and not self.error:
            raise ValueError("ok=False needs an error")
        return self


# -------------------------------------------------------------------- queue

class QueueTask(_Strict):
    """``agos_kernel.infrastructure.redis_queue.Task.to_dict()`` — the payload
    stored in Redis (results hash) and rebuilt by ``Task.from_dict``. Note its
    own status vocabulary (pending/queued/running/completed/failed/cancelled) differs
    from the API task status; the queue is not on the HTTP task path (R-156
    map — the server runs tasks with asyncio, R-163 owns a durable queue)."""
    id: str
    specialization: str
    prompt: str
    status: Literal["pending", "queued", "running", "completed", "failed", "cancelled"]
    data: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    priority: int = 0
    attempts: int = Field(0, ge=0)
    max_attempts: int = Field(3, ge=1)


# ----------------------------------------------------------- storage writes

class TaskInsert(_Strict):
    task_id: str
    specialization: str
    prompt: str = Field(max_length=500)
    status: TaskStatus
    tenant_id: Optional[str] = None


class TaskUpdate(_Strict):
    status: TaskStatus
    agent_name: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    elapsed_s: Optional[float] = None
    finished_at: Optional[Literal["now()"]] = None


class EventInsert(_Strict):
    event_type: Literal["task_created", "task_completed", "chat_completed"]
    event_data: Dict[str, Any]
    tenant_id: Optional[str] = None


class AgentStatsUpsert(_Strict):
    agent_name: str
    specialization: str
    tasks_completed: int = Field(ge=0)
    tasks_failed: int = Field(ge=0)
    total_time_s: float = Field(ge=0)
    last_active_at: Literal["now()"]
    updated_at: Literal["now()"]


# ----------------------------------------------------------------- registry

#: name -> (version, model, direction, producer -> consumer)
CONTRACTS: Dict[str, tuple] = {
    "agent_outcome":        (1, AgentOutcome, "internal+response", "agent.execute -> API/storage/web"),
    "handler_result":       (1, HandlerResult, "internal", "specialist handler (tool) -> agent outcome"),
    "task_accepted":        (2, TaskAcceptedV2, "response", "POST /api/tasks -> client"),
    "task_cancelled":       (1, TaskCancelled, "response", "POST /api/tasks/{task_id}/cancel -> client"),
    "task_record":          (1, TaskRecord, "response", "GET /api/tasks[/{id}] (memory) -> client"),
    "execute_record":       (1, ExecuteRecord, "response", "GET /api/tasks (memory, execute variant) -> client"),
    "task_row":             (2, TaskRowV2, "response+storage", "PostgREST tasks row -> API -> client"),
    "task_list":            (1, TaskList, "response", "GET /api/tasks -> client"),
    "chat_response":        (1, ChatResponse, "response", "POST /api/chat -> client"),
    "execute_response":     (1, ExecuteResponse, "response", "POST /civilization/execute -> client"),
    "error_detail":         (1, ErrorDetail, "response", "HTTPException -> client"),
    "validation_error":     (1, ValidationErrorBody, "response", "422 -> client"),
    "sse_frame":            (1, SSEFrame, "stream", "/api/events/stream -> client"),
    "system_status":        (1, SystemStatus, "response", "GET /api/system/status -> client"),
    "ready":                (1, Ready, "response", "GET /health/ready 200 -> probe"),
    "not_ready":            (1, NotReady, "response", "GET /health/ready 503 -> probe"),
    "memory_list":          (1, MemoryList, "response", "GET /api/memory -> client"),
    "llm_result":           (1, LLMResult, "internal", "LLMClient.complete -> handlers"),
    "queue_task":           (1, QueueTask, "storage", "RedisQueue -> Redis -> RedisQueue"),
    "storage_task_insert":  (1, TaskInsert, "storage", "SupabaseStorage.insert_task -> PostgREST"),
    "storage_task_update":  (1, TaskUpdate, "storage", "SupabaseStorage.update_task -> PostgREST"),
    "storage_event_insert": (1, EventInsert, "storage", "SupabaseStorage.insert_event -> PostgREST"),
    "storage_agent_stats":  (1, AgentStatsUpsert, "storage", "SupabaseStorage.upsert_agent_stats -> PostgREST"),
    "stage_artifact":       (1, StageArtifact, "internal", "DAG stage output -> dependent stages (R-165)"),
    "coordination_report":  (1, CoordinationReportContract, "api", "POST /civilization/coordinate response (R-166)"),
}

#: request models are the server's own (single source of truth) — resolved lazily
REQUEST_MODELS = {
    "task_request": ("server", "TaskRequest"),
    "chat_request": ("server", "ChatRequest"),
    "external_agent_request": ("server", "ExternalAgentRequest"),
    "execute_request": ("agent_civilization.civilization_routes", "ExecuteRequest"),
    "resume_request": ("agent_civilization.civilization_routes", "ResumeRequest"),
    "coordinate_request": ("agent_civilization.civilization_routes", "CoordinateRequest"),
}
REQUEST_VERSION = 1


def validate(name: str, payload: Any):
    """Validate ``payload`` against contract ``name`` (raises pydantic.ValidationError)."""
    return CONTRACTS[name][1].model_validate(payload)


def json_schemas(request_models: Optional[Dict[str, type]] = None) -> Dict[str, Dict[str, Any]]:
    """name -> JSON Schema (with ``x-contract-version``) of every contract."""
    out = {}
    for name, (version, model, direction, flow) in CONTRACTS.items():
        s = model.model_json_schema()
        s.update({"$id": f"agos://contracts/{name}/v{version}", "x-contract-version": version,
                  "x-direction": direction, "x-flow": flow})
        out[name] = s
    for name, model in (request_models or {}).items():
        s = model.model_json_schema()
        s.update({"$id": f"agos://contracts/{name}/v{REQUEST_VERSION}", "x-contract-version": REQUEST_VERSION,
                  "x-direction": "request", "x-flow": "client -> API",
                  "x-unknown-fields": "ignored by the server (not rejected)"})
        out[name] = s
    return out


# ------------------------------------------------------- compatibility check

def _resolve(schema: Dict[str, Any], node: Dict[str, Any]) -> Dict[str, Any]:
    ref = node.get("$ref")
    if ref and ref.startswith("#/$defs/"):
        return schema.get("$defs", {}).get(ref.split("/")[-1], {})
    return node


def _types(schema, node) -> set:
    """The accepted JSON types of ``node`` AND, when restricted, its values
    (const/enum) — so a widened or narrowed enum is a type change too."""
    node = _resolve(schema, node)
    out = set()
    if "type" in node:
        t = node["type"]
        out |= set(t) if isinstance(t, list) else {t}
    if "const" in node:
        out.add("const:" + repr(node["const"]))
    if "enum" in node:
        out |= {"enum:" + repr(v) for v in node["enum"]}
    for k in ("anyOf", "oneOf"):
        for sub in node.get(k, []):
            out |= _types(schema, sub)
    return out or {"any"}


def classify_changes(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, List[str]]:
    """Compare two JSON Schemas of one contract. CONSERVATIVE on purpose (the
    same rule for requests and responses): the only ``additive`` change is a
    new OPTIONAL property; everything else is ``breaking`` and needs a new
    contract version — a removed property, a property that became required
    or stopped being required, any change of the accepted types/values
    (narrowed or widened: a new enum value can break a strict reader), and a
    change of the unknown-field policy."""
    additive: List[str] = []
    breaking: List[str] = []

    def walk(o, n, p):
        o, n = _resolve(old, o), _resolve(new, n)
        to, tn = _types(old, o), _types(new, n)
        if to != tn:
            breaking.append(f"{p or '<root>'}: types {sorted(to)} -> {sorted(tn)}")
        op, np_ = o.get("properties", {}), n.get("properties", {})
        ro, rn = set(o.get("required", [])), set(n.get("required", []))
        for k in op:
            if k not in np_:
                breaking.append(f"{p}.{k}: removed")
            else:
                walk(op[k], np_[k], f"{p}.{k}")
        for k in np_:
            if k not in op:
                (breaking if k in rn else additive).append(
                    f"{p}.{k}: added ({'required' if k in rn else 'optional'})")
        for k in sorted((rn - ro) & set(op)):
            breaking.append(f"{p}.{k}: became required")
        for k in sorted((ro - rn) & set(np_)):
            breaking.append(f"{p}.{k}: no longer required")
        if o.get("additionalProperties", True) != n.get("additionalProperties", True):
            breaking.append(f"{p or '<root>'}: unknown-field policy changed")
        if "items" in o or "items" in n:
            walk(o.get("items", {}), n.get("items", {}), f"{p}[]")
        for k in ("anyOf", "oneOf"):
            ob, nb = o.get(k, []), n.get(k, [])
            if len(ob) == len(nb):
                for idx, (a, b) in enumerate(zip(ob, nb)):
                    walk(a, b, f"{p}<{k}{idx}>")

    walk(old, new, "")
    return {"additive": additive, "breaking": breaking}
