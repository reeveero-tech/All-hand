# AGOS Agent Civilization - Control Flow Analysis

**Generated:** 2026-07-23

---

## Control Flow Overview

This document traces the control flow through the AGOS system, showing how execution is directed from entry points through the system.

---

## Main Control Flow

```mermaid
flowchart TD
    subgraph "Entry"
        A[python run.py]
    end

    subgraph "Initialization"
        B[Create FastAPI app]
        C[Add CORS middleware]
        D[Create CIVILIZATION]
        E[Load EXTERNAL_AGENTS]
    end

    subgraph "Server Start"
        F[Uvicorn starts]
        G[Server ready]
    end

    subgraph "Request Handling"
        H{Request received}
        I[/api/tasks]
        J[/api/chat]
        K[/api/agents]
        L[/* fallback]
    end

    subgraph "Task Execution"
        M[Create task]
        N[Background task]
        O[Pick agent]
        P[Execute]
        Q[Update status]
    end

    subgraph "Response"
        R[Return response]
        S[Event emitted]
    end

    A --> B --> C --> D --> E --> F --> G
    G --> H
    H --> I
    H --> J
    H --> K
    H --> L
    I --> M --> N --> O --> P --> Q --> R
    Q --> S
    J --> O
```

---

## Task Creation Control Flow

```
User Request: POST /api/tasks
    │
    ▼
server.py: create_task(req)
    │
    ├──► Validate specialization
    │         │
    │         ├──► Valid? → Continue
    │         └──► Invalid? → 400 Error
    │
    ├──► Generate task_id
    │
    ├──► Create task dict
    │
    ├──► Store in TASKS dict
    │
    ├──► Prune old tasks (if > 500)
    │
    ├──► Insert to storage
    │
    ├──► Insert event
    │
    ├──► Emit event
    │
    └──► Create background task: _run_task()
            │
            ▼
        Return: {task_id, status: "queued"}
```

---

## Background Task Control Flow

```
asyncio.create_task(_run_task(task_id, spec, prompt, data))
    │
    ▼
Run in background:
    │
    ├──► _pick_agent(specialization)
    │         │
    │         ├──► Get agent pool
    │         ├──► Find idle agents
    │         └──► Return first idle or first available
    │
    ├──► Update task status: "running"
    │
    ├──► Update storage: status = "running"
    │
    ├──► Emit event: task_started
    │
    ├──► agent.execute({prompt, data})
    │         │
    │         ├──► Set status: "working"
    │         ├──► _dispatch(spec, work)
    │         │         │
    │         │         └──► Match handler:
    │         │             ├── analyst → handle_analyst
    │         │             ├── builder → handle_builder
    │         │             └── ... (20 handlers)
    │         │
    │         └──► Return outcome
    │
    ├──► Update task: status = completed/failed
    │
    ├──► Store result
    │
    ├──► Update agent stats
    │
    ├──► Emit event: task_completed
    │
    └──► Set agent status: "idle"
```

---

## Chat Routing Control Flow

```
User Request: POST /api/chat
    │
    ▼
server.py: chat(req)
    │
    ├──► llm = get_llm()
    │
    ├──► spec = "communicator" (default)
    │
    ├──► if llm.available:
    │         │
    │         ├──► llm.complete(ROUTER_SYSTEM, req.message)
    │         │
    │         ├──► Parse response for specialization
    │         │
    │         └──► Set spec
    │
    ├──► agent = _pick_agent(spec)
    │
    ├──► outcome = agent.execute({prompt: req.message})
    │
    ├──► Create task record
    │
    ├──► Store in TASKS
    │
    ├──► Update storage
    │
    ├──► Update agent stats
    │
    ├──► Emit event
    │
    └──► Return {task_id, routed_to, outcome}
```

---

## Event Stream Control Flow

```
User Request: GET /api/events/stream
    │
    ▼
server.py: event_stream()
    │
    ├──► Create StreamingResponse
    │
    └──► Generator:
            │
            └─► while True:
                    │
                    ├──► event = await TASK_EVENTS.get()
                    │         │
                    │         └──► Blocks until event available
                    │             (30 second timeout)
                    │
                    ├──► yield f"data: {json.dumps(event)}\n\n"
                    │
                    └──► (on timeout)
                        │
                        └──► yield heartbeat
```

---

## Agent Selection Control Flow

```
def _pick_agent(specialization: str):
    │
    ├──► pool = CIVILIZATION.get(specialization)
    │
    ├──► if specialization == "external" and EXTERNAL_AGENTS:
    │         │
    │         └──► Return agent with fewest tasks_completed
    │
    ├──► if not pool:
    │         │
    │         └──► raise HTTPException(404)
    │
    ├──► idle = [a for a in pool if a.status == "idle"]
    │
    └──► return (idle or pool)[0]
            │
            ├──► If idle agents exist: Return first idle
            └──► Otherwise: Return first available (round-robin effect)
```

---

## Task Pruning Control Flow

```
def _prune_tasks():
    │
    ├──► if len(TASKS) > MAX_TASKS_KEPT (500):
    │         │
    │         └──► Yes: Continue
    │         │
    │         └──► No: Return
    │
    └──► for tid in sorted(TASKS, key=created_at)[:excess]:
            │
            └──► TASKS.pop(tid)
```

---

## Real Dispatcher Control Flow

```
_dispatch(specialization: str, work: Dict) → Result
    │
    ▼
Match specialization:
    │
    ├──► "analyst" → handle_analyst(work)
    │             │
    │             └──► Statistics computation
    │
    ├──► "builder" → handle_builder(work)
    │             │
    │             └──► Goal decomposition
    │
    ├──► "surgeon" → handle_surgeon(work)
    │             │
    │             └──► AST diagnostics
    │
    ├──► "researcher" → handle_researcher(work)
    │               │
    │               └──► Web search
    │
    ├──► "test_runner" → handle_test_runner(work)
    │               │
    │               └──► Python execution
    │
    └──► ... (15 more handlers)
            │
            └──► Default: return None (no handler)
```

---

## Error Handling Control Flow

```
try:
    outcome = await agent.execute(...)
except Exception as exc:
    │
    ├──► Log error
    │
    ├──► outcome = {
    │         "ok": False,
    │         "error": f"{type(exc).__name__}: {exc}"
    │     }
    │
    └──► Continue (don't crash)
```

---

## Kernel Initialization Control Flow

```
kernel = AGOSKernel()
    │
    ├──► __init__()
    │         │
    │         ├──► Set status: INITIALIZING
    │         ├──► Create KernelConfig
    │         ├──► Initialize KernelState
    │         ├──► Get registries (singletons)
    │         ├──► Setup event handlers
    │         └──► Create health check thread
    │
    ▼
kernel.start()
    │
    ├──► Set status: BOOTSTRAPPING
    │
    ├──► _register_builtin_components()
    │         │
    │         └──► Import and register core components
    │
    ├──► _register_builtin_capabilities()
    │         │
    │         └──► Register capabilities
    │
    ├──► _register_builtin_providers()
    │         │
    │         └──► Register providers
    │
    ├──► _register_builtin_workflows()
    │         │
    │         └──► Register workflows
    │
    ├──► _activate_components()
    │         │
    │         └──► Activate registered components
    │
    ├──► _initialize_knowledge()
    │         │
    │         └──► Add base knowledge entries
    │
    ├──► Start health check thread
    │
    └──► Set status: READY
```

---

## Control Flow Summary

| Flow | Trigger | Response |
|------|---------|----------|
| Task Creation | POST /api/tasks | 202 + background execution |
| Chat Routing | POST /api/chat | 200 with routed result |
| Agent Selection | Task execution | First idle agent |
| Event Stream | GET /api/events/stream | SSE stream |
| Task Pruning | Task creation | Automatic cleanup |

---

*Report generated as part of comprehensive repository analysis*
