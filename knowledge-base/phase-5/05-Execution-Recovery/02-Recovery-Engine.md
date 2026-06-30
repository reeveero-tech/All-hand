# Recovery Engine

> **If Provider, Workspace, or Container fails, the system recovers.**

---

## Recovery Concept

```
Provider Fails
Workspace Crashes
Container Dies

        ↓
Recovery Engine
        ↓
Find Last Checkpoint
        ↓
Restore State
        ↓
Resume Execution
        ↓
Continue...
```

---

## Recovery Scenarios

```yaml
RecoveryScenarios:
  PROVIDER_FAILURE:
    description: "Provider stops responding"
    detection: "Timeout or error response"
    recovery:
      1. "Save checkpoint"
      2. "Select fallback provider"
      3. "Restore state to new provider"
      4. "Resume execution"
      
  WORKSPACE_CRASH:
    description: "Workspace becomes unavailable"
    detection: "Connection lost"
    recovery:
      1. "Detect crash"
      2. "Create new workspace"
      3. "Restore files from artifact storage"
      4. "Resume execution"
      
  CONTAINER_DEATH:
    description: "Container process dies"
    detection: "Process exit"
    recovery:
      1. "Save checkpoint"
      2. "Restart container"
      3. "Restore state"
      4. "Resume execution"
      
  NETWORK_PARTITION:
    description: "Network connectivity lost"
    detection: "Connection timeout"
    recovery:
      1. "Save state locally"
      2. "Wait for network"
      3. "Sync state"
      4. "Resume execution"
```

---

## Recovery Process

```python
async def recover_execution(execution: Execution) -> RecoveryResult:
    """
    Recover from failure.
    """
    
    # 1. Analyze failure
    failure_analysis = await analyze_failure(execution)
    
    # 2. Determine recovery strategy
    strategy = determine_strategy(failure_analysis)
    
    # 3. Find last good checkpoint
    checkpoint = await find_checkpoint(execution)
    
    if not checkpoint:
        # 4. No checkpoint - start fresh
        return await restart_execution(execution)
        
    # 5. Restore state from checkpoint
    restored_state = await restore_from_checkpoint(checkpoint)
    
    # 6. Apply recovery strategy
    if strategy == "SAME_PROVIDER":
        return await recover_same_provider(execution, restored_state)
        
    elif strategy == "DIFFERENT_PROVIDER":
        return await recover_different_provider(execution, restored_state)
        
    elif strategy == "RESTART_WORKSPACE":
        return await restart_workspace(execution, restored_state)
        
    elif strategy == "ESCALATE":
        return await escalate_to_human(execution)
```

---

## Recovery Strategies

```yaml
RecoveryStrategies:
  SAME_PROVIDER:
    trigger: "Transient provider error"
    action: "Retry with same provider"
    success_rate: 0.70
    
  DIFFERENT_PROVIDER:
    trigger: "Provider unavailable"
    action: "Switch to fallback provider"
    success_rate: 0.85
    
  RESTART_WORKSPACE:
    trigger: "Workspace crashed"
    action: "Create new workspace, restore state"
    success_rate: 0.90
    
  RESTORE_FROM_CLOUD:
    trigger: "Local state lost"
    action: "Restore from cloud storage"
    success_rate: 0.95
    
  FULL_RESTART:
    trigger: "No checkpoint available"
    action: "Start execution from beginning"
    success_rate: 1.0  # Last resort
    
  ESCALATE:
    trigger: "All recovery failed"
    action: "Request human intervention"
    success_rate: 0.0  # Manual
```

---

## Checkpoint-Based Recovery

```python
class CheckpointRecovery:
    """
    Recovery using checkpoints.
    """
    
    async def restore_from_checkpoint(
        self,
        execution: Execution,
        checkpoint: Checkpoint
    ) -> RestoredState:
        """
        Restore execution state from checkpoint.
        """
        
        # 1. Get checkpoint data
        state = checkpoint.state
        
        # 2. Restore execution context
        context = state.execution_context
        
        # 3. Restore artifacts
        artifacts = await self.restore_artifacts(checkpoint.artifacts)
        
        # 4. Restore workspace
        workspace = await self.restore_workspace(checkpoint.workspace)
        
        # 5. Restore provider state
        provider_state = state.provider_state
        
        return RestoredState(
            context=context,
            artifacts=artifacts,
            workspace=workspace,
            provider_state=provider_state,
            checkpoint_id=checkpoint.id
        )
        
    async def find_checkpoint(
        self,
        execution: Execution
    ) -> Checkpoint:
        """
        Find most recent valid checkpoint.
        """
        
        checkpoints = await self.checkpoint_store.get_all(execution.id)
        
        # Sort by timestamp descending
        sorted_checkpoints = sorted(
            checkpoints,
            key=lambda c: c.timestamp,
            reverse=True
        )
        
        # Find most recent valid checkpoint
        for checkpoint in sorted_checkpoints:
            if checkpoint.is_valid:
                return checkpoint
                
        return None
```

---

## State Restoration

```yaml
StateRestoration:
  # What gets restored
  
  EXECUTION_CONTEXT:
    - Current step
    - Input parameters
    - Environment variables
    - Configuration
    
  WORKSPACE_FILES:
    - Source code
    - Generated files
    - Temporary files
    - Build artifacts
    
  ARTIFACTS:
    - Intermediate artifacts
    - Test results
    - Logs
    
  PROVIDER_STATE:
    - Session state
    - Cached data
    - API state
    
  EXECUTION_HISTORY:
    - Completed steps
    - Verification results
    - Metrics
```

---

## Recovery Metrics

```yaml
RecoveryMetrics:
  # Track recovery effectiveness
  
  overall:
    recovery_attempts: 500
    successful_recoveries: 475
    recovery_rate: 95%
    avg_recovery_time: "2 minutes"
    
  by_strategy:
    same_provider:
      attempts: 200
      success: 140
      rate: 70%
      
    different_provider:
      attempts: 200
      success: 170
      rate: 85%
      
    restart_workspace:
      attempts: 80
      success: 72
      rate: 90%
```

---

## Example: Provider Failure Recovery

```yaml
Scenario:
  Mission: "Build e-commerce platform"
  Execution: "Generate payment API"
  
Failure:
  Provider: OpenHands
  Error: "Connection timeout"
  Time: "2024-01-15T10:30:00Z"
  Last Checkpoint: "2024-01-15T10:29:30Z"

Recovery Process:
  1. Detect failure
     - OpenHands timeout detected
     - Current state: "RUNNING"
     
  2. Check retry count
     - Retry count: 1 (max: 2)
     - Can retry
     
  3. Select recovery strategy
     - Strategy: DIFFERENT_PROVIDER
     - Reason: "Provider unavailable"
     
  4. Find checkpoint
     - Checkpoint ID: "ckpt_2024_001"
     - State: "Step 5 of 10"
     
  5. Restore to new provider (Cline)
     - Create new execution context
     - Restore files from checkpoint
     - Inject state into Cline
     
  6. Resume execution
     - Resume from step 5
     - Continue to step 6, 7, 8...
     
  7. Verification
     - All checks passed
     
  8. Execution completed

Result:
  - Mission continues
  - No work lost
  - Cost: ~$0.10 extra
```

---

## Failure Tolerance

```yaml
FailureTolerance:
  # How many failures before escalation
  
  per_execution:
    max_retries: 3
    strategies:
      - try_same_provider
      - try_different_provider
      - restart_workspace
      - restart_from_checkpoint
      
  per_mission:
    max_failures: 10
    escalation_after: 5
    
  per_hour:
    max_provider_failures: 20
    cooldown_period: "5 minutes"
```

---

## Related Documents

- [Checkpoint-System.md](./03-Checkpoint-System.md)
- [Retry-Engine.md](./01-Retry-Engine.md)
