# AGOS Agent Civilization - Architecture Analysis

**Generated:** 2026-07-23

---

## Architecture Overview

AGOS follows a **Modular Monolith with Plugin Architecture** pattern, combining:
- Central orchestrator (Kernel)
- Specialized agent subsystems
- Event-driven communication
- Registry-based component discovery

---

## Architectural Patterns Identified

### 1. **Kernel-Centric Architecture**
```
┌─────────────────────────────────────────────────────────┐
│                      AGOS Kernel                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │Registry │  │Runtime  │  │ Brain   │  │Governance│ │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  │
│       │             │             │             │       │
│       └─────────────┴─────────────┴─────────────┘       │
│                          │                              │
│                    ┌─────┴─────┐                       │
│                    │Orchestrator│                       │
│                    └─────┬─────┘                       │
└──────────────────────────┼──────────────────────────────┘
                           │
     ┌─────────────────────┼─────────────────────┐
     │                     │                     │
     ▼                     ▼                     ▼
┌─────────┐         ┌─────────────┐        ┌─────────┐
│ Civilization│       │ Agent System │        │ Frontend │
│   System   │       │              │        │   (Web) │
└───────────┘        └─────────────┘        └─────────┘
```

### 2. **Layered Architecture**

| Layer | Components | Responsibility |
|-------|------------|----------------|
| **Presentation** | `web/`, `src/` | User interface |
| **API** | `server.py` | REST endpoints |
| **Orchestration** | `kernel.py`, `orchestrator.py` | Flow control |
| **Business Logic** | Specialists, Brain | Task execution |
| **Infrastructure** | Storage, Registry | Data & state |

### 3. **Registry Pattern**
All components register themselves:
- Component Registry
- Capability Registry  
- Provider Registry
- Workflow Registry
- Knowledge Registry

### 4. **Agent Civilization Pattern**
```
User Input
    │
    ▼
┌────────────────┐
│  Router/Chat   │
└───────┬────────┘
        │
        ▼
┌────────────────┐     ┌─────────────────┐
│   Dispatcher    │────▶│ 20 Specializations│
└────────────────┘     └────────┬─────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
   ┌─────────┐            ┌─────────────┐          ┌──────────┐
   │ Builder │            │   Surgeon   │          │ Analyst  │
   │ (50)    │            │   (50)     │          │  (50)    │
   └─────────┘            └─────────────┘          └──────────┘
```

---

## Core Components

### 1. AGOS Kernel (`agos-kernel/`)

**Purpose:** Central orchestrator managing all system components.

**Key Components:**
- `kernel.py` - Singleton orchestrator
- `registry/` - Component registration
- `runtime/` - Execution environments
- `brain/` - Intelligence engine
- `governance/` - Policy enforcement

**Initialization Flow:**
```python
AGOSKernel.__init__()
  └─▶ Register built-in components
  └─▶ Register capabilities
  └─▶ Register providers
  └─▶ Register workflows
  └─▶ Initialize knowledge base
  └─▶ Start health check thread
```

### 2. Agent Civilization (`agent_civilization/`)

**Purpose:** Execute real tasks through specialized agents.

**20 Specializations:**
| Category | Count | Examples |
|----------|-------|----------|
| Development | 10 | builder, surgeon, test_runner, code_generator |
| Analysis | 4 | analyst, researcher, reviewer, monitor |
| Communication | 2 | communicator, educator |
| Infrastructure | 4 | architect, api_integrator, db_manager, designer |

**Execution Model:**
- 50 agents per specialization = 1,000 total
- Round-robin task distribution
- Async execution with event streaming

### 3. Storage Layer

**Primary:** Supabase (PostgreSQL + Realtime)

**Fallback:** In-memory storage

**Tables:**
- Tasks
- Agent Stats
- Events

---

## Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                     API Request Flow                          │
└──────────────────────────────────────────────────────────────┘

User Request
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI Server (server.py)                                   │
│  - CORS Middleware                                          │
│  - Authentication (future)                                   │
│  - Rate Limiting (future)                                    │
└────────────────────┬────────────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────────┐
│  /api/tasks     │     │  /api/chat          │
│  Direct dispatch│     │  LLM routing        │
└────────┬────────┘     └──────────┬──────────┘
         │                        │
         └──────────┬─────────────┘
                    │
                    ▼
         ┌─────────────────────┐
         │ Task Dispatcher     │
         │ (real_dispatcher)   │
         └──────────┬──────────┘
                    │
         ┌──────────┴──────────┐
         │                     │
         ▼                     ▼
   ┌─────────────┐     ┌─────────────────┐
   │  In-Memory  │     │   Supabase      │
   │   Queue     │     │   (Optional)     │
   └─────────────┘     └─────────────────┘
```

---

## Communication Patterns

### 1. Synchronous (REST)
```python
POST /api/tasks → await agent.execute()
```

### 2. Asynchronous (Background Tasks)
```python
POST /api/tasks → asyncio.create_task(_run_task())
```

### 3. Event Streaming (SSE)
```python
GET /api/events/stream → StreamingResponse
```

### 4. Internal Events
```python
await TASK_EVENTS.put({...})  # asyncio.Queue
```

---

## State Management

### Kernel State
- Status (initializing → ready → running → shutdown)
- Component count
- Workflow execution count
- Knowledge entries
- Execution logs (last 1000)

### Agent State
- Status (idle/working)
- Task counts (completed/failed)
- Total execution time

### Task State
- Status (queued/running/completed/failed)
- Assigned agent
- Result data
- Timestamps

---

## Component Relationships

```
                    ┌─────────────────┐
                    │  AGOS Kernel     │
                    │   (Singleton)    │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│   Registry    │  │    Runtime    │  │   Governance  │
│  - Component  │  │  - Session    │  │  - Policies   │
│  - Capability │  │  - Workspace  │  │  - Audit      │
│  - Provider   │  │  - Resource   │  │  - Security   │
└───────────────┘  └───────────────┘  └───────────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                             ▼
                    ┌───────────────┐
                    │  Intelligence │
                    │  - Brain      │
                    │  - Memory     │
                    │  - Learning   │
                    │  - Experience │
                    └───────────────┘
                             │
                             ▼
                    ┌───────────────┐
                    │    Agents     │
                    │  (Civilization)│
                    └───────────────┘
```

---

## Scalability Considerations

### Current Architecture
- **In-process:** All agents run in same process
- **Thread-based:** asyncio for concurrency
- **Memory-limited:** Task pruning at 500 tasks

### Extensibility Points
1. **External Agents:** Via OpenAI-compatible adapter
2. **Plugins:** Extensible plugin system
3. **Providers:** Multiple LLM provider support
4. **Storage:** Pluggable storage backends

---

## Security Architecture

### Current
- CORS: Allow all origins (configurable)
- No authentication (development mode)
- No rate limiting (future feature)

### Planned
- JWT authentication
- Role-based access control
- Rate limiting
- Input validation

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Deployment Options                      │
└─────────────────────────────────────────────────────────┘

Option 1: Single Container (Render/Docker)
┌─────────────────────────────────────────┐
│  ┌─────────┐  ┌─────────┐  ┌────────┐ │
│  │  API    │  │  Kernel │  │ Frontend│ │
│  │ Server  │  │         │  │  (dist) │ │
│  └────┬────┘  └────┬────┘  └────┬───┘ │
│       │             │             │      │
│       └─────────────┼─────────────┘      │
│                     │                     │
│                     ▼                     │
│            ┌─────────────────┐            │
│            │   Supabase      │            │
│            │   (External)    │            │
│            └─────────────────┘            │
└─────────────────────────────────────────┘

Option 2: Scalable (Kubernetes)
- API Server (replicas)
- Kernel Worker (queue consumer)
- Redis (task queue)
- PostgreSQL (state)
```

---

## Design Principles (from Constitution)

1. **Kernel has no business logic** - Only orchestration
2. **Evidence before claims** - All results are logged
3. **Everything is replaceable** - Modular design
4. **Every action passes through governance** - Policy enforcement
5. **Every error is recorded** - Comprehensive logging

---

## Strengths

| Aspect | Description |
|--------|-------------|
| **Modularity** | Clear separation of concerns |
| **Extensibility** | Registry pattern enables easy extension |
| **Observability** | Built-in logging and health checks |
| **Testability** | Component-based design |
| **Documentation** | Comprehensive knowledge base |

## Weaknesses

| Aspect | Description |
|--------|-------------|
| **In-process scaling** | Cannot scale agents independently |
| **No auth** | Security features not implemented |
| **Monolithic** | Single deployable unit |
| **In-memory fallback** | No persistent queue without Supabase |

---

*Report generated as part of comprehensive repository analysis*
