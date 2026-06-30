# Mission Memory

> **Instead of saving a "conversation", the system saves a "Mission".**

---

## Concept

```
Conversation-based (OLD):
  - Saves chat messages
  - Loses context after session
  - Hard to resume after weeks

Mission-based (NEW):
  - Saves full mission context
  - Complete state after months
  - Easy to resume anytime
```

---

## Mission Memory Schema

```yaml
MissionMemory:
  # ========== Identity ==========
  mission_id: string
  version: string
  created_at: datetime
  updated_at: datetime
  last_accessed: datetime
  
  # ========== Objectives ==========
  objectives:
    - objective: string
      status: enum            # PENDING, IN_PROGRESS, COMPLETED, CANCELLED
      priority: enum
      completion_criteria: string
      
  # ========== Decisions ==========
  decisions:
    - decision_id: string
      timestamp: datetime
      type: enum              # STRATEGIC, TACTICAL, OPERATIONAL
      description: string
      alternatives_considered: list[string]
      selected: string
      reasoning: string
      confidence: 0.0-1.0
      outcome: enum           # SUCCESS, FAILURE, PARTIAL
      
  # ========== Execution Timeline ==========
  timeline:
    - event_id: string
      timestamp: datetime
      type: enum              # STARTED, CHECKPOINT, EXECUTION, DECISION, DELIVERABLE
      description: string
      state: object          # Full state snapshot
      artifacts: list[string]
      
  # ========== Artifacts ==========
  artifacts:
    - artifact_id: string
      name: string
      type: string
      path: string
      version: string
      created_at: datetime
      checksum: string
      lineage: string         # Parent artifact
      
  # ========== Provider History ==========
  provider_history:
    - provider_id: string
      capability: string
      executions: integer
      success_rate: decimal
      avg_duration: duration
      failures: list[string]
      
  # ========== Failures ==========
  failures:
    - failure_id: string
      timestamp: datetime
      type: string
      description: string
      impact: enum            # LOW, MEDIUM, HIGH, CRITICAL
      resolution: string
      recovery: string
        
  # ========== Recoveries ==========
  recoveries:
    - recovery_id: string
      failure_id: string
      timestamp: datetime
      strategy: string
      success: boolean
      duration: duration
      
  # ========== Lessons Learned ==========
  lessons_learned:
    - lesson: string
      category: enum          # SUCCESS, FAILURE, OPPORTUNITY, RISK
      evidence: string
      recommendation: string
      
  # ========== Context ==========
  context:
    original_request: string
    clarifications: list[string]
    constraints: list[string]
    assumptions: list[string]
    unknowns: list[string]
    
  # ========== State ==========
  state:
    current_phase: enum       # PLANNING, EXECUTION, VERIFICATION, COMPLETION
    progress_percentage: decimal
    blocked: boolean
    blockers: list[string]
    
  # ========== Final Outcome ==========
  final_outcome:
    status: enum            # SUCCESS, FAILURE, PARTIAL, ONGOING, CANCELLED
    summary: string
    deliverables: list[string]
    quality_score: 0.0-1.0
    completed_at: datetime
    duration: duration
    total_cost: decimal
```

---

## Why Mission Memory?

### Problems with Conversation Memory

```yaml
ConversationProblems:
  - "User asks to continue project after 2 weeks"
    → Conversation lost
    → Context gone
    → Start over
    
  - "Session expires"
    → Memory cleared
    → Everything forgotten
    
  - "Multiple sessions"
    → Different contexts
    → Inconsistent understanding
```

### Solutions with Mission Memory

```yaml
MissionMemorySolutions:
  - "User asks to continue after 2 weeks"
    → Load Mission Memory
    → Full context restored
    → Continue from checkpoint
    
  - "Session expires"
    → Mission persists in cloud
    → Resume anywhere
    
  - "Multiple sessions"
    → Same Mission Memory
    → Consistent understanding
```

---

## Mission Memory Operations

### Save Mission State

```python
async def save_mission_state(mission: Mission):
    """
    Save complete mission state.
    """
    
    memory = MissionMemory(
        mission_id=mission.id,
        updated_at=datetime.now(),
        
        objectives=mission.objectives,
        decisions=mission.decisions,
        timeline=mission.timeline,
        artifacts=mission.artifacts,
        provider_history=mission.provider_history,
        failures=mission.failures,
        recoveries=mission.recoveries,
        lessons_learned=mission.lessons_learned,
        context=mission.context,
        state=mission.state
    )
    
    await mission_memory_store.save(memory)
```

### Load Mission State

```python
async def load_mission_state(mission_id: string) -> MissionMemory:
    """
    Load complete mission state.
    """
    
    memory = await mission_memory_store.load(mission_id)
    
    # Restore mission context
    mission = Mission(
        id=mission_id,
        memory=memory
    )
    
    return mission
```

### Resume Mission

```python
async def resume_mission(mission_id: string) -> Mission:
    """
    Resume mission from where it left off.
    """
    
    # 1. Load mission memory
    memory = await load_mission_state(mission_id)
    
    # 2. Analyze current state
    if memory.state.blocked:
        # Handle blockers first
        blockers = await resolve_blockers(memory.state.blockers)
        
    # 3. Find last checkpoint
    last_checkpoint = find_last_checkpoint(memory)
    
    # 4. Restore state
    restored_state = await restore_from_checkpoint(last_checkpoint)
    
    # 5. Resume execution
    mission = await continue_execution(memory, restored_state)
    
    return mission
```

---

## Example: Resume After 1 Month

### Initial Mission (1 month ago)

```yaml
MissionMemory:
  mission_id: "mission_2023_ecommerce"
  
  objectives:
    - objective: "Build e-commerce platform"
      status: "IN_PROGRESS"
      completion: 60%
      
  decisions:
    - decision_id: "dec_001"
      type: "STRATEGIC"
      description: "Chose microservices architecture"
      selected: "Microservices"
      reasoning: "Best scalability"
      
  timeline:
    - event: "Started mission"
      timestamp: "2023-12-01"
    - event: "Completed backend"
      timestamp: "2023-12-10"
    - event: "Checkpoint saved"
      timestamp: "2023-12-15"
      
  artifacts:
    - name: "backend_api"
      version: "1.0"
    - name: "database_schema"
      version: "1.0"
```

### Resume Today (1 month later)

```yaml
# User: "Continue the e-commerce project"

1. Load Mission Memory
   - Found mission: "mission_2023_ecommerce"
   - Progress: 60%
   - Last checkpoint: "2023-12-15"
   
2. Analyze state
   - Completed: Backend API, Database
   - Pending: Frontend, Testing, Deployment
   
3. Restore context
   - Architecture: Microservices
   - Stack: Python, FastAPI, PostgreSQL
   - Current phase: Frontend development
   
4. Resume execution
   - Continue from frontend
   - No context lost
   - Mission continues seamlessly
```

---

## Mission Memory vs Conversation

```yaml
Comparison:

CONVERSATION:
  What it saves:
    - Chat messages
    - User requests
    - AI responses
    
  Lost when:
    - Session ends
    - Context cleared
    - After days
    
  Resumable: NO
  Complete picture: NO
  Searchable: PARTIAL
  
MISSION MEMORY:
  What it saves:
    - Full mission context
    - All decisions with reasoning
    - Complete timeline
    - All artifacts
    - Failures and recoveries
    - Lessons learned
    
  Lost when:
    - Mission deleted
    - Never (persists in cloud)
    
  Resumable: YES
  Complete picture: YES
  Searchable: YES
```

---

## Mission Memory Storage

```yaml
Storage:
  # Where mission memory is stored
  
  backend:
    type: "cloud_database"
    replication: "multi-region"
    backup: "continuous"
    
  retention:
    active: "indefinite"
    completed: "90 days"
    archived: "user_decides"
    
  access:
    - Read: Mission owner, team
    - Write: System only
    - Delete: Owner only
```

---

## Search & Query

```python
async def search_missions(query: SearchQuery) -> list[MissionMemory]:
    """
    Search mission memories.
    """
    
    results = await mission_memory_store.search(
        query={
            "text": query.text,
            "filters": query.filters,
            "date_range": query.date_range,
            "status": query.status,
            "tags": query.tags
        }
    )
    
    return results


# Example searches:
# - "Find all missions with 'microservices'"
# - "Find missions that failed on deployment"
# - "Find lessons learned from security audits"
# - "Find missions completed in last 30 days"
```

---

## Learning Integration

```yaml
Learning:
  # Mission memory feeds into learning
  
  from_missions:
    - Pattern: "Microservices successful for e-commerce"
    - Pattern: "Payment integration often fails first time"
    - Pattern: "Frontend takes 40% of time"
    
  to_future:
    - "Suggest microservices for e-commerce"
    - "Plan backup for payment"
    - "Allocate 40% time for frontend"
```

---

## Related Documents

- [Mission-Overview.md](./01-Mission-Overview.md)
- [Mission-Dashboard.md](./02-Mission-Dashboard.md)
