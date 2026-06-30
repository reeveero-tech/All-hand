# Universal Execution Object

> **Every execution transforms into this standardized Object.**

---

## Execution Object Schema

```yaml
Execution:
  # ========== Identification ==========
  id: string                    # Unique execution ID
  version: string              # Schema version
  mission_id: string            # Parent mission ID
  goal_id: string             # Associated goal
  task_id: string             # Associated task
  
  # ========== Capability ==========
  capability:
    id: CapabilityID           # What capability
    version: string           # Capability version
    input_schema: object      # Input schema
    output_schema: object     # Output schema
    
  # ========== Provider ==========
  provider:
    id: ProviderID            # Selected provider
    adapter: string          # Adapter used
    endpoint: string         # Provider endpoint
    version: string          # Provider version
    
  # ========== Policy ==========
  policy:
    execution_policy_id: string
    retry_policy: object
    timeout_policy: object
    verification_policy: object
    escalation_policy: object
    
  # ========== Scheduling ==========
  scheduling:
    priority: enum           # CRITICAL, HIGH, MEDIUM, LOW
    queue: enum             # Which queue
    scheduled_at: datetime   # When scheduled
    started_at: datetime    # When started
    deadline: datetime      # Hard deadline
    estimated_duration: duration
    
  # ========== State ==========
  state:
    current: enum           # Current state
    previous: enum          # Previous state
    history: list[StateTransition]
    updated_at: datetime
    
  # ========== Execution ==========
  execution:
    mode: enum             # SYNC, ASYNC, PARALLEL
    mode_config: object    # Mode-specific config
    workspace_id: string   # Workspace for this execution
    
  # ========== Retry ==========
  retry:
    count: integer         # Current retry count
    max_retries: integer   # Maximum retries
    strategy: enum         # SAME_PROVIDER, DIFFERENT_PROVIDER, etc.
    history: list[RetryAttempt]
    
  # ========== Verification ==========
  verification:
    status: enum           # PENDING, IN_PROGRESS, PASSED, FAILED, SKIPPED
    required: boolean
    verification_types: list[string]
    checks: list[CheckResult]
    completed_at: datetime
    
  # ========== Output ==========
  output:
    status: enum           # SUCCESS, FAILURE, PARTIAL, TIMEOUT
    message: string         # Human-readable message
    artifacts: list[Artifact]
    data: object           # Structured output
    logs: list[LogEntry]
    
  # ========== Metrics ==========
  metrics:
    # Timing
    queued_duration: duration
    preparing_duration: duration
    running_duration: duration
    verifying_duration: duration
    total_duration: duration
    
    # Resources
    cpu_seconds: decimal
    memory_mb_seconds: decimal
    gpu_seconds: decimal
    
    # Cost
    model_cost: decimal
    provider_cost: decimal
    compute_cost: decimal
    storage_cost: decimal
    network_cost: decimal
    total_cost: decimal
    
    # Quality
    quality_score: 0.0-1.0
    verification_score: 0.0-1.0
    
  # ========== Checkpoint ==========
  checkpoint:
    enabled: boolean
    intervals: list[duration]
    last_checkpoint: datetime
    checkpoints: list[Checkpoint]
    
  # ========== Isolation ==========
  isolation:
    workspace_id: string
    network_namespace: string
    process_group: string
    sandbox_type: enum   # CONTAINER, VM, PROCESS
    
  # ========== Security ==========
  security:
    secrets_granted: list[string]     # Secrets this execution can access
    secrets_expires_at: datetime
    permissions: list[string]
    audit_log: list[AuditEntry]
    
  # ========== Context ==========
  context:
    environment: string   # dev, staging, prod
    region: string        # Cloud region
    tags: list[string]
    metadata: dict
        
  # ========== Relationships ==========
  relationships:
    parent_execution: string   # If this is a retry/sub-execution
    child_executions: list[string]  # If this spawned sub-executions
    blocked_by: list[string]  # Execution IDs this is blocked by
    blocking: list[string]   # Execution IDs this blocks
    
  # ========== Timestamps ==========
  timestamps:
    created_at: datetime
    queued_at: datetime
    scheduled_at: datetime
    started_at: datetime
    completed_at: datetime
    failed_at: datetime
    cancelled_at: datetime
    
  # ========== Source ==========
  source:
    created_by: enum         # USER, SYSTEM, SCHEDULER
    triggered_by: string     # What triggered this
```

---

## State Transition History

```yaml
StateTransition:
  from_state: string
  to_state: string
  timestamp: datetime
  reason: string
  triggered_by: string      # USER, SYSTEM, PROVIDER
  metadata: dict
```

---

## Retry Attempt

```yaml
RetryAttempt:
  attempt_number: integer
  timestamp: datetime
  provider: ProviderID
  reason: string            # Why this retry
  strategy: enum
  result: enum             # SUCCESS, FAILURE, TIMEOUT
  duration: duration
  error: string             # If failed
```

---

## Example: Code Generation Execution

```yaml
Execution:
  id: "exec_2024_001"
  mission_id: "mission_2024_ecommerce"
  goal_id: "goal_2024_001"
  task_id: "task_15"
  
  capability:
    id: "cap_generate_backend"
    version: "1.0.0"
    
  provider:
    id: "provider_openhands"
    adapter: "openhands-adapter"
    endpoint: "https://api.openhands.ai"
    
  policy:
    execution_policy_id: "policy_generate_backend"
    retry_policy:
      max_retries: 2
      strategy: "DIFFERENT_PROVIDER"
    timeout_policy:
      default: "5 minutes"
      max: "10 minutes"
    verification_policy:
      required: true
      types: ["syntax", "lint", "tests"]
      
  scheduling:
    priority: "HIGH"
    queue: "high"
    deadline: "2024-01-20T12:00:00Z"
    estimated_duration: "5 minutes"
    
  state:
    current: "COMPLETED"
    previous: "VERIFYING"
    history:
      - from: "CREATED"
        to: "QUEUED"
        timestamp: "2024-01-15T10:00:00Z"
      - from: "QUEUED"
        to: "SCHEDULED"
        timestamp: "2024-01-15T10:00:05Z"
      - from: "SCHEDULED"
        to: "PREPARING"
        timestamp: "2024-01-15T10:00:10Z"
      - from: "PREPARING"
        to: "RUNNING"
        timestamp: "2024-01-15T10:00:15Z"
      - from: "RUNNING"
        to: "VERIFYING"
        timestamp: "2024-01-15T10:03:30Z"
      - from: "VERIFYING"
        to: "COMPLETED"
        timestamp: "2024-01-15T10:05:00Z"
        
  retry:
    count: 0
    max_retries: 2
    
  verification:
    status: "PASSED"
    required: true
    checks:
      - name: "syntax"
        status: "PASSED"
        details: "Compiles successfully"
      - name: "lint"
        status: "PASSED"
        details: "No errors, 2 warnings"
      - name: "tests"
        status: "PASSED"
        details: "12/12 tests passed"
        
  output:
    status: "SUCCESS"
    message: "Backend API generated successfully"
    artifacts:
      - type: "source_code"
        path: "src/api/users.py"
        language: "python"
        lines: 250
      - type: "tests"
        path: "tests/api/users_test.py"
        lines: 80
    data:
      endpoints_created: 5
      models_created: 3
      
  metrics:
    queued_duration: "5 seconds"
    preparing_duration: "5 seconds"
    running_duration: "3m 15s"
    verifying_duration: "1m 30s"
    total_duration: "4m 55s"
    
    model_cost: 0.03
    provider_cost: 0.05
    compute_cost: 0.02
    storage_cost: 0.01
    total_cost: 0.11
    
    quality_score: 0.95
    verification_score: 1.0
    
  checkpoint:
    enabled: true
    intervals: ["30 seconds"]
    last_checkpoint: "2024-01-15T10:03:00Z"
```

---

## Related Documents

- [Execution-State-Machine.md](./02-Execution-State-Machine.md)
- [Execution-Scheduler.md](./03-Execution-Scheduler.md)
