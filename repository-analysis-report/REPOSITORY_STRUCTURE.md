# AGOS Agent Civilization - Repository Structure Analysis

**Repository:** https://github.com/reeveero-tech/All-hand  
**Generated:** 2026-07-23  
**Total Files:** 1,928  
**Total Directories:** 364

---

## Executive Summary

This repository contains **AGOS (Agent Operating System)** - a real-first AI agent civilization system where users submit goals, the civilization plans stages, assigns specialist roles, executes real tools, and returns combined results.

### Key Metrics

| Metric | Value |
|--------|-------|
| Python Files | 1,661 |
| Markdown Files | 183 |
| YAML/JSON Files | 49 |
| TypeScript/TSX Files | 14 |
| JavaScript/JSX Files | 9 |
| Total Files | 1,928 |
| Repository Size | ~15 MB |

---

## Project Structure Overview

```
All-hand/
├── agos-kernel/              # Core kernel module (Python)
├── agos-canon/               # Constitutional/specified rules
├── agos-factory/              # AGL Language factory
├── agos-intelligence/         # Intelligence layer
├── agent_civilization/       # Agent civilization system
├── knowledge-base/            # Knowledge documentation (phase 1-9)
├── knowledge_base/            # Symlink to knowledge-base
├── web/                      # React frontend (Vite)
├── src/                      # Alternative frontend (TanStack)
├── sdk/                      # SDK components
├── server.py                  # FastAPI REST API server
├── run.py                     # Main entry point
└── ... (70+ other directories)
```

---

## Primary Components

### 1. **agos-kernel/** - Core Engine (114 subdirectories)
The heart of AGOS containing all core system components.

**Key Subdirectories:**
- `registry/` - Component, capability, provider registries
- `runtime/` - Execution runtimes (session, resource, recovery, workspace)
- `brain/` - Intelligence and reasoning components
- `execution/` - Task execution engine
- `governance/` - Policy enforcement and audit
- `knowledge/` - Knowledge management
- `memory/` - Memory management
- `intelligence/` - AI intelligence layer
- `api/` - API definitions
- `cli/` - Command line interface
- `plugins/` - Plugin system
- `container/` - Container management
- `security/` - Security modules

### 2. **agent_civilization/** - Civilization System
Implements the 20-specialization agent civilization.

**Key Subdirectories:**
- `agents/specialists/` - 20 specialization types (builder, surgeon, etc.)
- `agents/infrastructure/` - Orchestration and dispatching
- `agents/executors/` - Data execution workers
- `pipeline/` - Task pipeline orchestration
- `storage/` - Supabase storage integration

### 3. **web/** - React Frontend
Vite + React 19 frontend application.

**Key Files:**
- `index.html` - Entry HTML
- `src/` - React components and routes
- `vite.config.js` - Vite configuration
- `package.json` - Dependencies

### 4. **src/** - Alternative Frontend
TanStack Start (React Router v7) alternative frontend.

### 5. **knowledge-base/** - Documentation System
Phase-based knowledge documentation (9 phases).

---

## Top-Level Files

| File | Type | Purpose |
|------|------|---------|
| `server.py` | Python | FastAPI REST API server (Main entry) |
| `run.py` | Python | Main startup script |
| `agent_manager.py` | Python | Agent management utilities |
| `agents.yaml` | YAML | Agent registry (139KB, 299+ entries) |
| `requirements.txt` | Text | Python dependencies |
| `package.json` | JSON | Node.js dependencies |
| `Dockerfile` | Docker | Container configuration |
| `render.yaml` | YAML | Render deployment config |
| `DEPLOY.md` | Markdown | Deployment documentation |
| `README.md` | Markdown | Project overview |
| `agos_system_registry_report.json` | JSON | System registry data |

---

## Entry Points

### Primary Entry Points

1. **`run.py`** - Main startup script
   ```bash
   python run.py
   ```

2. **`server.py`** - FastAPI server
   ```bash
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```

3. **`agos-kernel/kernel.py`** - Kernel initialization
   ```python
   from agos_kernel.kernel import AGOSKernel
   kernel = AGOSKernel()
   kernel.start()
   ```

### Secondary Entry Points

- `agos-kernel/run_kernel.py` - Kernel runner
- `generate_real_agents.py` - Agent generation script
- `auto_fetch_agents.py` - Agent fetching script

---

## Technology Stack

### Backend (Python)

| Category | Technologies |
|----------|-------------|
| Framework | FastAPI, Uvicorn |
| Validation | Pydantic |
| HTTP | aiohttp, httpx |
| Database | SQLAlchemy, Alembic, Supabase |
| Cache | Redis |
| Logging | structlog |
| Metrics | prometheus-client |
| Security | cryptography, python-jose |
| Testing | pytest, pytest-asyncio, pytest-cov |
| Linting | ruff, black, isort, mypy |
| Git | GitPython |

### Frontend (JavaScript/TypeScript)

| Category | Technologies |
|----------|-------------|
| Framework | React 19, TanStack Start |
| Routing | TanStack Router v1 |
| UI | Radix UI primitives |
| Styling | Tailwind CSS v4 |
| Forms | React Hook Form, Zod |
| Data | TanStack Query |
| Charts | Recharts |
| Build | Vite |
| Language | TypeScript 5.8 |

---

## Build & Deployment

### Local Development
```bash
# Python backend
pip install -r requirements.txt
python run.py

# React frontend
cd web && npm install && npm run dev
```

### Docker
```bash
docker build -t agos .
docker run -p 8000:8000 agos
```

### Render (Cloud)
Uses `render.yaml` for free-tier deployment.

---

## Directory Classification

### Source Code (Python)
- `agos-kernel/`
- `agent_civilization/`
- `sdk/`
- `seos/`
- `cognitive/`
- `cognition/`
- `constitution/`
- `domain/`
- `ecosystem/`
- `enterprise/`
- `evolution/`
- `execution/`
- `fabric/`
- `foundation/`
- `governance/`
- `intelligence/`
- `meta/`
- `observatory/`
- `orchestration/`
- `products/`
- `research/`
- `resource/`
- `rie/`
- `rpp-pipeline/`
- `sdl-loop/`

### Documentation
- `knowledge-base/`
- `knowledge_base/`
- `agos-canon/`
- `agos-enterprise/`
- `agos-factory/`
- `agos-intelligence/`
- `agos-kernel/` (README.md)

### Frontend
- `web/`
- `src/`
- `public/`

### Configuration
- `.env.example`
- `.gitignore`
- `agos-kernel/pyproject.toml`
- `components.json`
- `render.yaml`
- `tsconfig.json`
- `vite.config.ts`

### CI/CD
- `.github/workflows/ci.yml`

---

## Project Relationships

```
                    ┌─────────────────┐
                    │    run.py       │
                    │   (Entry)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   server.py     │
                    │   (FastAPI)     │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ agent_civilization│ │   agos-kernel   │ │   web/src/      │
│ (20 specialists)│ │   (Core Engine) │ │   (React UI)    │
└─────────────────┘ └─────────────────┘ └─────────────────┘
         │                   │                   │
         └───────────────────┴───────────────────┘
                             │
                    ┌────────┴────────┐
                    │   Supabase      │
                    │   (Storage)     │
                    └─────────────────┘
```

---

## Additional Observations

### Multiple Frontend Approaches
The repository contains TWO frontend implementations:
1. **`web/`** - React with Vite (modern, production-ready)
2. **`src/`** - TanStack Start (alternative routing approach)

### Knowledge Base Duplication
- `knowledge-base/` and `knowledge_base/` appear to be symlinked or duplicated

### RIE Sprint Directories
- `rie-sprint1/` and `rie_sprint1/` (symlinked)

### Project Phases
The repository appears to be organized by development phases (sprint-000, phase-1 through phase-9 in knowledge-base)

---

*Report generated as part of comprehensive repository analysis*
