# Execution State Machine

> **Every execution passes through these states.**

---

## State Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                  Execution State Machine                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  CREATED                                                    │
│     │                                                         │
│     ▼                                                         │
│  QUEUED                                                     │
│     │                                                         │
│     ▼                                                         │
│  SCHEDULED                                                  │
│     │                                                         │
│     ▼                                                         │
│  PREPARING                                                  │
│     │                                                         │
│     ▼                                                         │
│  RUNNING                                                    │
│     │                                                         │
│     ├───────────────────────────────────────┐              │
│     │                                       │              │
│     ▼                                       ▼              │
│  WAITING ◄─────► RUNNING                  │              │
│     │                                       │              │
│     ▼                                       ▼              │
│  VERIFYING                                │              │
│     │                                       │              │
│     ├────────────────┐                    │              │
│     │                 │                    │              │
│     ▼                 ▼                    │              │
│  COMPLETED ◄──── VERIFYING                 │              │
│     │                 │                    │              │
│     │                 ▼                    │              │
│     │             FAILED ◄────────────────┤              │
│     │                 │                    │              │
│     │                 ▼                    │              │
│     │              RETRY ◄─────────────────┤              │
│     │                 │                    │              │
│     │     ┌────────────┼────────────┐       │              │
│     │     │            │            │       │              │
│     │     ▼            ▼            ▼       │              │
│     │  RECOVERED   SAME_PROVIDER  FALLBACK   │              │
│     │     │            │            │       │              │
│     │     └────────────┼────────────┘       │              │
│     │                  │                    │              │
│     │                  ▼                    │              │
│     │              RUNNING ─────────────────┘              │
│     │                                                       │
│     ▼                                                       │
│  CANCELLED                                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## State Definitions

```yaml
States:
  CREATED:
    description: "Execution created, not yet queued"
    entry_actions:
      - "Create Execution Object"
      - "Validate inputs"
      - "Assign workspace"
    exit_actions:
      - "Move to queue"
      
  QUEUED:
    description: "Waiting in queue for scheduling"
    entry_actions:
      - "Add to appropriate queue"
      - "Record queue time"
    exit_actions:
      - "Dequeue"
    allowed_transitions:
      - SCHEDULED
      - CANCELLED
      
  SCHEDULED:
    description: "Assigned to executor, waiting for resources"
    entry_actions:
      - "Reserve resources"
      - "Assign executor"
    exit_actions:
      - "Start preparation"
    allowed_transitions:
      - PREPARING
      - CANCELLED
      - RESOURCE_TIMEOUT
      
  PREPARING:
    description: "Setting up environment and context"
    entry_actions:
      - "Create isolated workspace"
      - "Inject secrets"
      - "Setup dependencies"
    exit_actions:
      - "Start execution"
    allowed_transitions:
      - RUNNING
      - PREPARATION_FAILED
      
  RUNNING:
    description: "Actively executing capability"
    entry_actions:
      - "Start provider"
      - "Monitor execution"
      - "Create checkpoints"
    exit_actions:
      - "Finalize execution"
    allowed_transitions:
      - WAITING
      - VERIFYING
      - COMPLETED
      - FAILED
      
  WAITING:
    description: "Waiting for external event or resource"
    entry_actions:
      - "Release compute resources"
      - "Save checkpoint"
    exit_actions:
      - "Resume execution"
    allowed_transitions:
      - RUNNING
      - TIMEOUT
      - CANCELLED
      
  VERIFYING:
    description: "Running verification checks"
    entry_actions:
      - "Run verification pipeline"
      - "Collect metrics"
    exit_actions:
      - "Accept or reject"
    allowed_transitions:
      - COMPLETED
      - FAILED
      
  COMPLETED:
    description: "Execution finished successfully"
    entry_actions:
      - "Collect artifacts"
      - "Finalize metrics"
      - "Cleanup resources"
    exit_actions:
      - "Archive execution"
    terminal_state: true
    
  FAILED:
    description: "Execution failed after all retries"
    entry_actions:
      - "Record failure"
      - "Collect error info"
    exit_actions:
      - "Cleanup"
    terminal_state: true
    
  RETRY:
    description: "Preparing for retry"
    entry_actions:
      - "Determine retry strategy"
      - "Select next provider"
    exit_actions:
      - "Retry execution"
    allowed_transitions:
      - RUNNING
      - FAILED
      
  CANCELLED:
    description: "Execution cancelled by user or system"
    entry_actions:
      - "Stop execution"
      - "Cleanup resources"
    exit_actions:
      - "Archive execution"
    terminal_state: true
    
  RECOVERED:
    description: "Execution recovered from checkpoint"
    entry_actions:
      - "Restore from checkpoint"
      - "Resume execution"
    exit_actions:
      - "Continue"
    allowed_transitions:
      - RUNNING
```

---

## State Transition Rules

```python
class ExecutionStateMachine:
    """
    Manages execution state transitions.
    """
    
    def validate_transition(
        self,
        current_state: State,
        target_state: State,
        execution: Execution
    ) -> bool:
        """
        Validate if transition is allowed.
        """
        
        # Get allowed transitions for current state
        allowed = self.get_allowed_transitions(current_state)
        
        if target_state not in allowed:
            return False
            
        # Check state-specific rules
        if current_state == State.RUNNING:
            if target_state == State.FAILED:
                # Can only fail if no retries left
                if execution.retry.count < execution.retry.max_retries:
                    return False
                    
        if current_state == State.QUEUED:
            if target_state == State.CANCELLED:
                # Can cancel if not yet started
                return True
                
        return True
```

---

## Event-Driven Transitions

```yaml
StateTransitions:
  # Events that trigger state transitions
  
  execution_created:
    from: null
    to: CREATED
    trigger: "Execution object created"
    
  queued:
    from: CREATED
    to: QUEUED
    trigger: "Added to execution queue"
    
  scheduled:
    from: QUEUED
    to: SCHEDULED
    trigger: "Resources allocated"
    
  preparing_started:
    from: SCHEDULED
    to: PREPARING
    trigger: "Environment setup begins"
    
  preparation_complete:
    from: PREPARING
    to: RUNNING
    trigger: "Environment ready"
    
  execution_complete:
    from: RUNNING
    to: VERIFYING
    trigger: "Provider finished"
    
  verification_passed:
    from: VERIFYING
    to: COMPLETED
    trigger: "All checks passed"
    
  verification_failed:
    from: VERIFYING
    to: FAILED
    trigger: "Verification failed"
    
  execution_failed:
    from: RUNNING
    to: RETRY
    trigger: "Execution error (retries available)"
    
  retry_exhausted:
    from: RETRY
    to: FAILED
    trigger: "Max retries reached"
    
  retry_success:
    from: RETRY
    to: RUNNING
    trigger: "Retry succeeded"
    
  user_cancelled:
    from: "*"
    to: CANCELLED
    trigger: "User or system cancellation"
```

---

## Recovery State

```yaml
RecoveryStates:
  # Special states for recovery
  
  CHECKPOINT_SAVED:
    description: "Checkpoint created successfully"
    checkpoint_id: string
    
  STATE_RESTORED:
    description: "State restored from checkpoint"
    checkpoint_id: string
    
  RESUMING:
    description: "Resuming from checkpoint"
    from_checkpoint: string
    
  PARTIAL_COMPLETION:
    description: "Some tasks completed before failure"
    completed_tasks: list[string]
    pending_tasks: list[string]
```

---

## Example: Execution Lifecycle

```yaml
Lifecycle:
  execution_id: "exec_2024_001"
  
  timeline:
    - time: "2024-01-15T10:00:00Z"
      state: "CREATED"
      action: "Execution created"
      
    - time: "2024-01-15T10:00:01Z"
      state: "QUEUED"
      action: "Added to high priority queue"
      
    - time: "2024-01-15T10:00:05Z"
      state: "SCHEDULED"
      action: "Resources allocated"
      
    - time: "2024-01-15T10:00:10Z"
      state: "PREPARING"
      action: "Creating isolated workspace"
      
    - time: "2024-01-15T10:00:15Z"
      state: "RUNNING"
      action: "OpenHands started"
      
    - time: "2024-01-15T10:00:45Z"
      state: "RUNNING"
      action: "Checkpoint created"
      
    - time: "2024-01-15T10:03:30Z"
      state: "VERIFICATION"
      action: "Execution completed"
      
    - time: "2024-01-15T10:05:00Z"
      state: "COMPLETED"
      action: "Verification passed"
```

---

## Error Handling

```yaml
ErrorHandling:
  # State transitions on errors
  
  preparation_error:
    current: PREPARING
    next: FAILED
    action: "Log error, cleanup"
    
  execution_error:
    current: RUNNING
    next: RETRY
    action: "Check retry count"
    
  verification_error:
    current: VERIFYING
    next: RETRY
    action: "Check retry count"
    
  timeout_error:
    current: RUNNING
    next: RETRY
    action: "Trigger timeout policy"
    
  resource_error:
    current: SCHEDULED
    next: QUEUED
    action: "Re-queue with lower priority"
```

---

## Related Documents

- [Execution-Object.md](./01-Execution-Object.md)
- [Checkpoint-System.md](../05-Execution-Recovery/03-Checkpoint-System.md)
