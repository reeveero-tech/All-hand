"""R-202 (D-151): AGOS v2 core contracts — version 1.

The five core objects of v2: Task, Step, AgentSpec, Artifact, Message.
They are the single source of the JSON Schemas under
``docs/governance/contracts/v2/`` (``scripts/export_v2_contract_schemas.py``,
``--check`` fails on drift) and they mirror the tables of
``docs/sql/v2/001_core.sql`` column for column:

- Task / Step / Artifact / Message: one field per column of agos.tasks /
  agos.steps / agos.artifacts / agos.messages, same names, and the same rules
  the database enforces (``tests/test_r202_v2_schema.py`` checks both layers
  reject the same invalid rows);
- AgentSpec: the body stored in ``agos.agent_specs.spec`` (slug and version
  are repeated in their own columns).

Rules encoded in BOTH the contract and the database:
- no success without verification: Task ``completed`` needs ``verified_at``,
  Step ``succeeded`` needs ``verification.passed is True``;
- a failure is declared: ``failed`` needs ``error`` {code, message};
- terminal states carry ``finished_at`` and non-terminal states do not;
- ``attempts <= max_attempts`` (bounded self-correction).

All models are strict (``extra="forbid"``): an unknown field is an error,
so the code, the schema files and the tables cannot drift silently.
"""
from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

CONTRACT_VERSION = 1

TASK_STATUSES = ("queued", "claimed", "planning", "running", "waiting_approval",
                 "verifying", "completed", "failed", "cancelled")
TASK_TERMINAL = ("completed", "failed", "cancelled")
STEP_STATUSES = ("pending", "ready", "running", "waiting_approval",
                 "succeeded", "failed", "skipped", "cancelled")
STEP_TERMINAL = ("succeeded", "failed", "skipped", "cancelled")
STEP_KINDS = ("plan", "act", "verify", "fix", "review", "approval", "final")
ARTIFACT_KINDS = ("file", "text", "code", "data", "link")
MESSAGE_ROLES = ("user", "assistant", "system", "tool")
VERIFY_METHODS = ("tests", "sources", "recompute", "schema", "review", "human")

TaskStatus = Literal["queued", "claimed", "planning", "running", "waiting_approval",
                     "verifying", "completed", "failed", "cancelled"]
StepStatus = Literal["pending", "ready", "running", "waiting_approval",
                     "succeeded", "failed", "skipped", "cancelled"]
StepKind = Literal["plan", "act", "verify", "fix", "review", "approval", "final"]
ArtifactKind = Literal["file", "text", "code", "data", "link"]
MessageRole = Literal["user", "assistant", "system", "tool"]
VerifyMethod = Literal["tests", "sources", "recompute", "schema", "review", "human"]

_SLUG = r"^[a-z][a-z0-9_]{1,62}$"
_ERROR_CODE = r"^[a-z][a-z0-9_.]{1,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_MIME = r"^[a-z]+/[a-z0-9.+-]+"
_STORAGE_PATH = r"^[A-Za-z0-9/_.-]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- shared parts

class ErrorInfo(_Strict):
    """A declared failure: a stable machine code and a human message."""
    code: str = Field(pattern=_ERROR_CODE)
    message: str = Field(min_length=1, max_length=4000)
    retryable: StrictBool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class Verification(_Strict):
    """Result of the independent check of a step's output."""
    passed: StrictBool
    method: VerifyMethod
    evidence: List[str] = Field(default_factory=list, max_length=100)
    checked_at: _dt.datetime
    notes: Optional[str] = Field(default=None, max_length=4000)


class AcceptanceCriterion(_Strict):
    """One acceptance criterion of a task or step, checkable by ``check``."""
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    text: str = Field(min_length=1, max_length=2000)
    check: VerifyMethod


class Budget(_Strict):
    """Limits of a task. Absent = no limit beyond the workspace quota (R-207)."""
    max_cost_usd: Optional[float] = Field(default=None, ge=0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    max_steps: Optional[int] = Field(default=None, ge=1, le=1000)
    deadline: Optional[_dt.datetime] = None


def _check_failed(status: str, error: Optional[ErrorInfo]) -> None:
    if status == "failed" and error is None:
        raise ValueError("status 'failed' requires error {code, message}")


def _check_finished(status: str, terminal: tuple, finished_at: Optional[_dt.datetime]) -> None:
    if (status in terminal) != (finished_at is not None):
        raise ValueError("finished_at must be set exactly for terminal states "
                         f"{terminal}, got status={status!r}")


# ----------------------------------------------------------------------- Task

class Task(_Strict):
    """A user goal with acceptance criteria — one row of agos.tasks."""
    id: _uuid.UUID
    workspace_id: _uuid.UUID
    created_by: Optional[_uuid.UUID] = None
    conversation_id: Optional[_uuid.UUID] = None
    parent_task_id: Optional[_uuid.UUID] = None
    title: str = Field(min_length=1, max_length=300)
    goal: str = Field(min_length=1, max_length=20000)
    acceptance_criteria: List[AcceptanceCriterion] = Field(default_factory=list)
    status: TaskStatus = "queued"
    priority: int = Field(default=0, ge=-10, le=10)
    budget: Budget = Field(default_factory=Budget)
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)
    claim_token: Optional[str] = None
    lease_expires_at: Optional[_dt.datetime] = None
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=20)
    result_summary: Optional[str] = None
    error: Optional[ErrorInfo] = None
    verified_at: Optional[_dt.datetime] = None
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    created_at: _dt.datetime
    updated_at: _dt.datetime
    started_at: Optional[_dt.datetime] = None
    finished_at: Optional[_dt.datetime] = None

    @model_validator(mode="after")
    def _rules(self) -> "Task":
        if self.attempts > self.max_attempts:
            raise ValueError("attempts must not exceed max_attempts")
        if self.status == "completed" and self.verified_at is None:
            raise ValueError("status 'completed' requires verified_at (no success without verification)")
        _check_failed(self.status, self.error)
        _check_finished(self.status, TASK_TERMINAL, self.finished_at)
        return self


# ----------------------------------------------------------------------- Step

class Step(_Strict):
    """One node of a task's plan graph — one row of agos.steps."""
    id: _uuid.UUID
    workspace_id: _uuid.UUID
    task_id: _uuid.UUID
    parent_step_id: Optional[_uuid.UUID] = None
    seq: int = Field(ge=0)
    kind: StepKind
    title: str = Field(min_length=1, max_length=300)
    agent_spec_id: Optional[_uuid.UUID] = None
    status: StepStatus = "pending"
    depends_on: List[_uuid.UUID] = Field(default_factory=list)
    acceptance: List[AcceptanceCriterion] = Field(default_factory=list)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    verification: Optional[Verification] = None
    error: Optional[ErrorInfo] = None
    model: Optional[str] = Field(default=None, max_length=200)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    created_at: _dt.datetime
    started_at: Optional[_dt.datetime] = None
    finished_at: Optional[_dt.datetime] = None

    @model_validator(mode="after")
    def _rules(self) -> "Step":
        if self.id in self.depends_on:
            raise ValueError("a step cannot depend on itself")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt must not exceed max_attempts")
        if self.status == "succeeded" and not (self.verification and self.verification.passed):
            raise ValueError("status 'succeeded' requires verification.passed = true")
        _check_failed(self.status, self.error)
        _check_finished(self.status, STEP_TERMINAL, self.finished_at)
        return self


# ------------------------------------------------------------------ AgentSpec

class ModelPolicy(_Strict):
    """Which models the agent may run on (resolved by the model gateway, R-206)."""
    preferred: List[str] = Field(min_length=1, max_length=10)
    min_context_tokens: int = Field(default=8000, ge=1000)
    needs_tool_calls: StrictBool = True
    temperature: float = Field(default=0.2, ge=0, le=2)


class MemoryPolicy(_Strict):
    task: StrictBool = True
    user: StrictBool = False
    knowledge: StrictBool = False


class LoopPolicy(_Strict):
    """plan -> act -> observe -> verify -> fix, bounded."""
    max_iterations: int = Field(default=8, ge=1, le=100)
    max_fix_attempts: int = Field(default=2, ge=0, le=10)


class Permissions(_Strict):
    network: StrictBool = False
    requires_approval_for: List[str] = Field(default_factory=list)


class AgentLimits(_Strict):
    max_cost_usd: Optional[float] = Field(default=None, ge=0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    timeout_s: int = Field(default=300, ge=1, le=86400)


class AgentSpec(_Strict):
    """What an agent IS (the 7 parts of ROADMAP_V2_AR §3) — the body of
    agos.agent_specs.spec. Weights are never stored: the 'brain' is a model
    policy resolved through the model gateway."""
    slug: str = Field(pattern=_SLUG)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    role: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=50000)
    model: ModelPolicy
    tools: List[str] = Field(default_factory=list, max_length=100)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    loop: LoopPolicy = Field(default_factory=LoopPolicy)
    permissions: Permissions = Field(default_factory=Permissions)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    output_schema: Optional[Dict[str, Any]] = None
    eval_set: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_./-]{1,200}$")


# ------------------------------------------------------------------- Artifact

class Artifact(_Strict):
    """A task output — one row of agos.artifacts. Exactly one of
    inline_content / storage_path."""
    id: _uuid.UUID
    workspace_id: _uuid.UUID
    task_id: _uuid.UUID
    step_id: Optional[_uuid.UUID] = None
    kind: ArtifactKind
    name: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(pattern=_MIME)
    inline_content: Optional[str] = Field(default=None, max_length=1_000_000)
    storage_path: Optional[str] = Field(default=None, pattern=_STORAGE_PATH)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256)
    created_at: _dt.datetime

    @model_validator(mode="after")
    def _one_location(self) -> "Artifact":
        if (self.inline_content is None) == (self.storage_path is None):
            raise ValueError("exactly one of inline_content / storage_path is required")
        return self


# -------------------------------------------------------------------- Message

class Message(_Strict):
    """One chat message — one row of agos.messages."""
    id: _uuid.UUID
    workspace_id: _uuid.UUID
    conversation_id: _uuid.UUID
    seq: int = Field(ge=0)
    role: MessageRole
    content: str = Field(max_length=200_000)
    task_id: Optional[_uuid.UUID] = None
    author_id: Optional[_uuid.UUID] = None
    created_at: _dt.datetime


# ------------------------------------------------------------------- registry

CONTRACTS: Dict[str, type] = {
    "task": Task,
    "step": Step,
    "agent_spec": AgentSpec,
    "artifact": Artifact,
    "message": Message,
}

# contract -> table whose columns equal the contract's fields (AgentSpec is
# stored as a JSON body, its table is checked separately).
TABLES: Dict[str, str] = {
    "task": "tasks",
    "step": "steps",
    "artifact": "artifacts",
    "message": "messages",
}


# Cross-field rules a JSON Schema cannot express directly; published in the
# schema files as ``x-rules`` and enforced by the models and by the database.
RULES: Dict[str, List[str]] = {
    "task": [
        "attempts <= max_attempts",
        "status 'completed' requires verified_at",
        "status 'failed' requires error {code, message}",
        "finished_at is set exactly when status is completed, failed or cancelled",
    ],
    "step": [
        "id not in depends_on",
        "attempt <= max_attempts",
        "status 'succeeded' requires verification.passed = true",
        "status 'failed' requires error {code, message}",
        "finished_at is set exactly when status is succeeded, failed, skipped or cancelled",
    ],
    "agent_spec": ["slug and version equal the agos.agent_specs columns"],
    "artifact": ["exactly one of inline_content / storage_path"],
    "message": ["(conversation_id, seq) is unique"],
}


def json_schemas() -> Dict[str, Dict[str, Any]]:
    """name -> JSON Schema (draft 2020-12, with x-contract-version)."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, model in CONTRACTS.items():
        s = model.model_json_schema()
        s.update({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"agos://contracts/v2/{name}/v{CONTRACT_VERSION}",
            "x-contract-version": CONTRACT_VERSION,
            "x-rules": RULES[name],
        })
        out[name] = s
    return out
