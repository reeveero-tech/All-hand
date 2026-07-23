# AGOS Agent Civilization - File Catalog

**Generated:** 2026-07-23  
**Total Files:** 1,928

---

## Critical Entry Points

| File Path | Type | Purpose | Importance |
|-----------|------|---------|------------|
| `server.py` | Python | FastAPI REST API server | **CRITICAL** |
| `run.py` | Python | Main startup script | **CRITICAL** |
| `agent_manager.py` | Python | Agent management | **CRITICAL** |
| `agent_civilization/agents/real/real_agents.py` | Python | 20 specialization agents | **CRITICAL** |
| `agent_civilization/core/agents/real_dispatcher.py` | Python | Task dispatcher | **CRITICAL** |
| `agos-kernel/kernel.py` | Python | Core kernel orchestrator | **CRITICAL** |

---

## Python Source Files by Module

### Core Kernel (`agos-kernel/`)

| File | Purpose | Importance |
|------|---------|------------|
| `agos-kernel/kernel.py` | Central orchestrator | CRITICAL |
| `agos-kernel/main.py` | Kernel entry | HIGH |
| `agos-kernel/run_kernel.py` | Kernel runner | HIGH |
| `agos-kernel/run_tests.py` | Test runner | MEDIUM |
| `agos-kernel/agos_system_registry.py` | System registry | HIGH |
| `agos-kernel/pyproject.toml` | Package config | HIGH |

### Registry System (`agos-kernel/registry/`)

| File | Purpose | Importance |
|------|---------|------------|
| `component.py` | Component registry | HIGH |
| `cap_registry.py` | Capability registry | HIGH |
| `provider_registry.py` | Provider registry | HIGH |
| `workflow_registry.py` | Workflow registry | HIGH |
| `knowledge_registry.py` | Knowledge registry | HIGH |

### Runtime (`agos-kernel/runtime/`)

| File | Purpose | Importance |
|------|---------|------------|
| `session/` | Session management | HIGH |
| `resource/` | Resource allocation | MEDIUM |
| `recovery/` | Failure recovery | MEDIUM |
| `workspace/` | Workspace management | MEDIUM |
| `state/` | State management | MEDIUM |

### Brain System (`agos-kernel/brain/`)

| File | Purpose | Importance |
|------|---------|------------|
| `engine.py` | Engineering brain | HIGH |
| `cognitive/` | Cognitive processing | HIGH |

### Governance (`agos-kernel/governance/`)

| File | Purpose | Importance |
|------|---------|------------|
| `runtime.py` | Governance enforcement | HIGH |
| `policies/` | Policy definitions | MEDIUM |
| `audit/` | Audit logging | MEDIUM |

### Agent Civilizations (`agent_civilization/`)

| File | Purpose | Importance |
|------|---------|------------|
| `agents/real/real_agents.py` | 20 specializations | CRITICAL |
| `agents/infrastructure/task_dispatcher.py` | Task routing | CRITICAL |
| `agents/infrastructure/orchestrator_agent.py` | Orchestration | HIGH |
| `agents/external_adapter.py` | External agent support | HIGH |
| `civilization_routes.py` | API routes | HIGH |
| `storage/supabase_store.py` | Database storage | MEDIUM |
| `pipeline/orchestrator.py` | Pipeline orchestration | HIGH |

### Specialists (`agent_civilization/agents/specialists/`)

| Specialization | Path | Purpose |
|----------------|------|---------|
| analyst | `analyst/` | Numeric/text analysis |
| api_integrator | `api_integrator/` | HTTP API calls |
| architect | `architect/` | System architecture |
| builder | `builder/` | Build actions |
| code_generator | `code_generator/` | Code generation |
| communicator | `communicator/` | Communication/translate |
| data_processor | `data_processor/` | Data processing |
| db_manager | `db_manager/` | SQL/SQLite |
| designer | `designer/` | Design systems |
| educator | `educator/` | Learning extraction |
| modifier | `modifier/` | Code transformation |
| monitor | `monitor/` | Runtime metrics |
| network_client | `network_client/` | DNS/HTTP probes |
| researcher | `researcher/` | Web research |
| reviewer | `reviewer/` | Code review |
| self_developer | `self_developer/` | Self-inspection |
| strategist | `strategist/` | Roadmap planning |
| surgeon | `surgeon/` | AST diagnostics |
| test_runner | `test_runner/` | Python testing |
| validator | `validator/` | JSON/Python validation |

---

## Configuration Files

| File | Type | Purpose | Importance |
|------|------|---------|------------|
| `.env.example` | Text | Environment template | HIGH |
| `agos-kernel/pyproject.toml` | TOML | Python package config | HIGH |
| `package.json` | JSON | Node dependencies | HIGH |
| `render.yaml` | YAML | Render deployment | MEDIUM |
| `tsconfig.json` | JSON | TypeScript config | MEDIUM |
| `vite.config.ts` | TS | Vite config | MEDIUM |
| `Dockerfile` | Docker | Container image | MEDIUM |
| `.gitignore` | Text | Git exclusions | LOW |
| `components.json` | JSON | Component config | LOW |

---

## Documentation Files

| File | Purpose |
|------|---------|
| `README.md` | Project overview |
| `DEPLOY.md` | Deployment guide |
| `FINAL-ARCHITECTURE.md` | Architecture spec |
| `INTEGRITY-REPORT.md` | System integrity |
| `agos-kernel/README.md` | Kernel docs |
| `knowledge-base/README.md` | Knowledge base guide |

---

## Data Files

| File | Size | Purpose |
|------|------|---------|
| `agents.yaml` | 139 KB | Agent registry (299+ entries) |
| `agos_system_registry_report.json` | 63 KB | System registry data |

---

## Script Files

| File | Purpose |
|------|---------|
| `generate_real_agents.py` | Generate agent catalog |
| `auto_fetch_agents.py` | Auto-fetch agents |
| `generate_agents_catalogue.py` | Agent catalog generator |
| `push_to_github.sh` | GitHub push script |

---

## Frontend Files (`web/`)

| File | Purpose |
|------|---------|
| `web/index.html` | Entry HTML |
| `web/vite.config.js` | Vite config |
| `web/src/` | React components |

---

## Test Files

| Pattern | Location | Purpose |
|---------|----------|---------|
| `test_*.py` | `agos-kernel/` | Unit tests |
| `test_*.py` | `agos-kernel/tests/` | Integration tests |

---

## File Type Distribution

| Extension | Count | Percentage |
|-----------|-------|------------|
| `.py` | 1,661 | 86.2% |
| `.md` | 183 | 9.5% |
| `.json` | 46 | 2.4% |
| `.yaml/yml` | 3 | 0.2% |
| `.ts/tsx` | 14 | 0.7% |
| `.js/jsx` | 9 | 0.5% |
| Other | 12 | 0.6% |

---

## Files Requiring Analysis (Priority Order)

### Tier 1 - Critical (Must Analyze)
1. `server.py` - Main API server
2. `run.py` - Entry point
3. `agent_civilization/agents/real/real_agents.py` - Core agents
4. `agos-kernel/kernel.py` - Core kernel

### Tier 2 - High Priority
5. `agent_civilization/agents/infrastructure/task_dispatcher.py`
6. `agent_civilization/civilization_routes.py`
7. `agent_civilization/storage/supabase_store.py`
8. `agos-kernel/registry/*.py` (5 files)
9. `agos-kernel/brain/engine.py`

### Tier 3 - Medium Priority
10. All specialist implementation files
11. `agent_civilization/pipeline/orchestrator.py`
12. `agos-kernel/runtime/` (all files)
13. `agos-kernel/governance/runtime.py`

### Tier 4 - Documentation
14. All README.md files
15. Architecture documents

---

*Report generated as part of comprehensive repository analysis*
