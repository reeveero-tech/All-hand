# AGOS Agent Civilization - Responsibility Matrix

**Generated:** 2026-07-23

---

## Component Overview

| Component | Responsibility | Type |
|-----------|----------------|------|
| **AGOS Kernel** | System orchestration | Core |
| **Agent Civilization** | Task execution | Core |
| **FastAPI Server** | HTTP interface | API |
| **Registries** | Component discovery | Infrastructure |
| **Runtimes** | Execution environments | Infrastructure |

---

## RACI Matrix

| Component | Responsible | Accountable | Consulted | Informed |
|-----------|-------------|-------------|-----------|----------|
| **server.py** | Implements API | AGOS Team | - | Users |
| **run.py** | Starts server | AGOS Team | - | Users |
| **kernel.py** | Orchestrates | Kernel Team | - | All modules |
| **agent_civilization/** | Executes tasks | Agents Team | - | API, Users |
| **agos-kernel/registry/** | Manages components | Registry Team | - | Kernel |

---

## Component Responsibility Table

### Core Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `server.py` | HTTP API serving | fastapi, pydantic | Users |
| `run.py` | Application entry | server.py | Users |
| `kernel.py` | System orchestration | registries, runtimes | API, Agents |
| `civilization_routes.py` | Auto-routing | agents, llm | API |

---

### Agent Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `real_agents.py` | Agent creation | dispatcher | API, Kernel |
| `real_dispatcher.py` | Task routing | handlers | agents |
| `task_dispatcher.py` | Infrastructure dispatch | agents | API |
| `orchestrator_agent.py` | Workflow orchestration | agents | Kernel |

---

### Specialist Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `analyst/` | Data analysis | statistics tool | dispatcher |
| `builder/` | Build actions | action-extraction | dispatcher |
| `surgeon/` | Code diagnostics | ast-diagnostics | dispatcher |
| `researcher/` | Web research | duckduckgo | dispatcher |
| `test_runner/` | Code execution | subprocess | dispatcher |

---

### Registry Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `component.py` | Component registry | - | Kernel |
| `cap_registry.py` | Capability registry | component.py | Kernel |
| `provider_registry.py` | Provider registry | component.py | Kernel |
| `workflow_registry.py` | Workflow registry | component.py | Kernel |
| `knowledge_registry.py` | Knowledge registry | component.py | Kernel |

---

### Runtime Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `session/` | Session management | storage | Kernel |
| `workspace/` | Workspace handling | storage | Kernel |
| `resource/` | Resource allocation | - | Kernel |
| `recovery/` | Failure recovery | logs | Kernel |
| `state/` | State management | storage | Kernel |

---

### Storage Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `supabase_store.py` | Database operations | supabase | API, Agents |
| `storage/__init__.py` | Storage abstraction | - | API, Kernel |

---

### Intelligence Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `brain/engine.py` | Engineering brain | registries | Kernel |
| `knowledge/runtime.py` | Knowledge management | knowledge_registry | Kernel |
| `memory/runtime.py` | Memory management | storage | Kernel |
| `learning/runtime.py` | Learning engine | experience | Kernel |
| `experience/runtime.py` | Experience tracking | storage | Kernel |

---

### Governance Components

| Component | Responsibility | Dependencies | Dependents |
|-----------|----------------|--------------|------------|
| `governance/runtime.py` | Policy enforcement | policies | Kernel |
| `audit/` | Audit logging | storage | Kernel |
| `policies/` | Policy definitions | - | Governance |

---

## Module Responsibilities

### agent_civilization/

| File/Module | Responsibility |
|-------------|----------------|
| `agents/real/` | Create and manage 1000 real agents |
| `agents/specialists/` | Implement 20 specialization handlers |
| `agents/infrastructure/` | Orchestration, dispatch, resources |
| `core/` | Core functionality (LLM, dispatcher) |
| `pipeline/` | Task pipeline management |
| `storage/` | Data persistence |
| `civilization_routes.py` | Auto-routing API |

---

### agos-kernel/

| File/Module | Responsibility |
|-------------|----------------|
| `kernel.py` | Central orchestrator |
| `registry/` | Component registries |
| `runtime/` | Execution runtimes |
| `brain/` | Intelligence engine |
| `governance/` | Policy enforcement |
| `knowledge/` | Knowledge management |
| `memory/` | Memory management |
| `learning/` | Learning engine |
| `experience/` | Experience tracking |

---

### web/

| File/Module | Responsibility |
|-------------|----------------|
| `src/` | React components |
| `vite.config.js` | Build configuration |
| `package.json` | Dependencies |

---

## Cross-Cutting Concerns

| Concern | Owner | Location |
|---------|-------|----------|
| **Logging** | Kernel Team | `kernel.py`, `server.py` |
| **Error Handling** | All Teams | Throughout |
| **Security** | Security Team | `governance/`, `security/` |
| **Monitoring** | Platform Team | `observatory/`, `reports/` |
| **Testing** | QA Team | `agos-kernel/tests/` |

---

## Data Ownership

| Data | Owner | Access |
|------|-------|--------|
| Tasks | API | Read/Write |
| Agents | Civilization | Read/Write |
| Registries | Kernel | Read/Write |
| Knowledge | Knowledge Team | Read/Write |
| Audit Logs | Governance | Write |

---

## Service Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                        Users                                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      API Layer                               │
│                    (server.py)                              │
└─────────────────────────────────────────────────────────────┘
           │                                    │
           ▼                                    ▼
┌───────────────────────┐          ┌───────────────────────────┐
│   Civilizations       │          │      AGOS Kernel          │
│ (agent_civilization/) │          │     (agos-kernel/)       │
└───────────────────────┘          └───────────────────────────┘
           │                                    │
           ▼                                    ▼
┌───────────────────────┐          ┌───────────────────────────┐
│   Storage             │          │      Registries           │
│ (Supabase/Memory)     │          │   (Component/Capability)   │
└───────────────────────┘          └───────────────────────────┘
```

---

## Summary

| Category | Count |
|----------|-------|
| Core Components | 5 |
| Agent Components | 4 |
| Registry Components | 5 |
| Runtime Components | 5 |
| Storage Components | 2 |
| Specialist Types | 20 |
| **Total Components** | **41** |

---

*Report generated as part of comprehensive repository analysis*
