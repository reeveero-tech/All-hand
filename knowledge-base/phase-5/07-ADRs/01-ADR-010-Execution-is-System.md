# ADR-010: Execution is a System

> **Architecture Decision Record**

---

## Status

**Proposed**: 2024-01-15  
**Status**: Accepted

---

## Context

Common misconception:
- Execution = "run a function"
- Execution = "call an API"
- Execution = "simple operation"

This leads to:
- No state tracking
- No recovery
- No checkpointing
- No parallelization
- No resource management

---

## Decision

> **Execution is not a process. Execution is a full System.**

```
Execution System includes:
- State Machine
- Scheduler
- Resource Manager
- Retry Engine
- Recovery Engine
- Checkpoint System
- Verification Pipeline
- Artifact Manager
- Workspace Manager
- Event Bus
- Cost Engine
- Monitoring
```

---

## Components

```yaml
ExecutionSystem:
  StateMachine:
    - CREATED → QUEUED → SCHEDULED → RUNNING → VERIFIED → COMPLETED
    - Or FAILED → RETRY → RECOVERED → RUNNING
    
  Scheduler:
    - Priority queues
    - Dependency resolution
    - Resource balancing
    - Deadline awareness
    
  ResourceManager:
    - CPU allocation
    - Memory allocation
    - GPU allocation
    - Token budgets
    
  RetryEngine:
    - Exponential backoff
    - Provider switching
    - Strategy patterns
    
  RecoveryEngine:
    - Checkpoint-based recovery
    - State restoration
    - Workspace recovery
```

---

## Consequences

### Positive

1. **Reliability** - System can recover from failures
2. **Observability** - Full tracking of execution
3. **Parallelization** - Smart scheduling
4. **Resource Control** - No resource exhaustion
5. **Cost Control** - Track every cost

### Negative

1. **Complexity** - More moving parts
2. **Overhead** - State management costs
3. **Debugging** - More to understand

### Neutral

1. **Performance** - Slightly slower but more reliable

---

## Execution Object

```yaml
Execution:
  id: "exec_001"
  state: "RUNNING"
  
  # State tracking
  state_history: [...]
  
  # Resource tracking
  resources_used: {...}
  
  # Cost tracking
  costs: {...}
  
  # Checkpointing
  checkpoints: [...]
  
  # Recovery
  retry_count: 0
  recovery_history: [...]
```

---

## Example

### Old Way

```python
# WRONG - Simple execution
result = provider.execute(task)
return result
```

### New Way

```python
# CORRECT - Execution as System
execution = await execution_system.create(task)

execution.on_state_change(lambda: save_state())
execution.on_failure(lambda: retry())
execution.on_checkpoint(lambda: save_checkpoint())

await execution.run()

return execution.result
```

---

## Related ADRs

| ADR | Title | Relationship |
|-----|-------|--------------|
| ADR-008 | Reasoning Chain | Foundation |
| **ADR-010** | **Execution is System** | **This decision** |
| ADR-011 | Mission-Based | Extension |
| ADR-012 | Cloud-Native | Extension |
| ADR-014 | Resumable Execution | Extension |

---

## References

- [Execution-State-Machine.md](../01-Execution-Engine/02-Execution-State-Machine.md)
- [Recovery-Engine.md](../05-Execution-Recovery/02-Recovery-Engine.md)
