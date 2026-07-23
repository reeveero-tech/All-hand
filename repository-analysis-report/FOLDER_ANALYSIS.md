# AGOS Agent Civilization - Folder Analysis

**Generated:** 2026-07-23

---

## Top-Level Directory Summary

| Directory | Type | Purpose | Files | Priority |
|-----------|------|---------|-------|----------|
| `agos-kernel/` | Source | Core engine | ~500 | CRITICAL |
| `agent_civilization/` | Source | Agent system | ~200 | CRITICAL |
| `web/` | Frontend | React UI | ~50 | HIGH |
| `src/` | Frontend | Alt React UI | ~30 | MEDIUM |
| `knowledge-base/` | Documentation | Knowledge docs | ~150 | HIGH |
| `agos-canon/` | Documentation | Rules/specs | ~20 | MEDIUM |

---

## Critical Directories

### agos-kernel/ (114 subdirectories)

**Purpose:** Core engine of the AGOS system

**Key Subdirectories:**
| Path | Purpose | Priority |
|------|---------|----------|
| `agos-kernel/kernel.py` | Central orchestrator | CRITICAL |
| `agos-kernel/registry/` | Component registries | HIGH |
| `agos-kernel/runtime/` | Execution environments | HIGH |
| `agos-kernel/brain/` | Intelligence engine | HIGH |
| `agos-kernel/governance/` | Policy enforcement | MEDIUM |
| `agos-kernel/api/` | API definitions | MEDIUM |
| `agos-kernel/cli/` | Command line | LOW |
| `agos-kernel/plugins/` | Plugin system | MEDIUM |
| `agos-kernel/security/` | Security modules | MEDIUM |

**Dependencies:** None (top-level)

**Dependents:** `server.py`, `run.py`, all other modules

---

### agent_civilization/

**Purpose:** Agent civilization system with 20 specializations

**Key Subdirectories:**
| Path | Purpose | Priority |
|------|---------|----------|
| `agents/real/` | Real agent implementations | CRITICAL |
| `agents/specialists/` | 20 specialization types | CRITICAL |
| `agents/infrastructure/` | Orchestration/dispatch | HIGH |
| `agents/executors/` | Data workers | HIGH |
| `pipeline/` | Task pipeline | MEDIUM |
| `storage/` | Supabase integration | MEDIUM |
| `core/` | Core functionality | HIGH |

**Dependencies:** `agos-kernel/` (optional)

**Dependents:** `server.py`

---

## Frontend Directories

### web/

**Purpose:** React + Vite frontend application

**Structure:**
```
web/
├── index.html
├── package.json
├── vite.config.js
└── src/
    ├── components/
    ├── hooks/
    ├── lib/
    ├── router.tsx
    ├── routes/
    ├── server.ts
    ├── start.ts
    └── styles.css
```

**Tech Stack:** React 19, Vite, TanStack Router, Tailwind CSS

**Dependencies:** `server.py` (API)

---

### src/

**Purpose:** Alternative frontend (TanStack Start)

**Structure:**
```
src/
├── components/
├── hooks/
├── lib/
├── router.tsx
├── routes/
├── server.ts
├── start.ts
└── styles.css
```

**Note:** Appears to be a duplicate/alternative to `web/`

---

## Documentation Directories

### knowledge-base/

**Purpose:** Comprehensive knowledge documentation

**Structure:**
```
knowledge-base/
├── README.md
├── 01-Ecosystem/
├── 02-Architecture/
├── 03-Agents/
├── 04-Tools/
├── 05-Technologies/
├── 06-Research/
├── 07-Decisions/
├── 08-Standards/
├── 09-Roadmap/
├── 10-Experiments/
├── 11-Competitors/
├── 12-Risks/
├── 13-Glossary/
├── 14-References/
└── phase-1/ through phase-9/
```

**Dependencies:** None

**Dependents:** Developers, AI agents

---

### agos-canon/

**Purpose:** Constitutional rules and specifications

**Structure:**
```
agos-canon/
├── README.md
├── Canons/           # Fundamental principles
├── Constitution/     # Governance rules
└── Specification/    # Technical specs
```

---

### agos-factory/

**Purpose:** AGL Language factory

**Structure:**
```
agos-factory/
├── README.md
└── 01-AGL-Language/  # Domain-specific language
```

---

### agos-intelligence/

**Purpose:** Intelligence layer documentation

**Structure:**
```
agos-intelligence/
├── README.md
├── 01-Core-Engines/
├── 02-Decision-Graph/
├── 03-Decision-Packet/
└── 06-ADRs/          # Architecture Decision Records
```

---

### agos-kernel/ (Documentation)

**Purpose:** Kernel-specific documentation

**Files:**
- `agos-kernel/README.md`
- `agos-kernel/agos_system_registry.py`
- `agos-system-registry-report.json`

---

## Source Code Directories

### cognitive/

**Purpose:** Cognitive processing modules

**Files:**
- `constraint.py`
- `deliberation.py`
- `evaluation.py`
- `planning.py`
- `reasoning.py`
- `goal.py`
- `strategy.py`
- `intent.py`
- `decision.py`
- `context.py`

---

### cognition/

**Purpose:** Cognition system (alternative/related)

**Files:** Similar structure to `cognitive/`

---

### constitution/

**Purpose:** Constitutional framework

**Files:**
- `constitution.py`

---

### governance/

**Purpose:** Governance and audit

**Files:**
- `audit.py`
- `enforcement.py`
- `normalization.py`

---

### intelligence/

**Purpose:** Intelligence layer

**Files:**
- `ai_execution.py`
- `ontology.py`
- `platform.py`
- `protocol.py`
- `tool_cognition.py`

---

### meta/

**Purpose:** Metadata management

**Files:**
- `graph.py`
- `index.py`
- `lifecycle.py`
- `registry.py`
- `repository.py`
- `schema.py`

---

### observatory/

**Purpose:** Monitoring and observability

**Files:**
- `dashboard.py`

---

### orchestration/

**Purpose:** Orchestration layer

**Files:** Module initialization

---

### products/

**Purpose:** Product management

**Files:**
- `api.py`
- `artifacts.py`
- `missions.py`
- `projects.py`

---

### research/

**Purpose:** Research and simulation

**Files:**
- `genome.py`
- `platform.py`
- `future.py`
- `simulation.py`
- `meta.py`

---

### sdk/

**Purpose:** SDK components

**Files:** Module initialization

---

## Project/Sprint Directories

### rie-sprint1/ (and rie_sprint1/)

**Purpose:** RIE Sprint 1 development

**Structure:**
```
rie-sprint1/
├── README.md
├── 02-Fetcher/
├── 03-Technology/
├── 04-AI-Detector/
├── 06-Capability/
└── 09-DNA/
```

**Note:** `rie_sprint1/` is a symlink to `rie-sprint1/`

---

### sprint-000/

**Purpose:** Initial sprint documentation

---

### spec-formal/

**Purpose:** Formal specifications

**Structure:**
```
spec-formal/
├── README.md
└── AGOS-001-Universal-Object-Model/
```

---

## Cloud/Deployment Directories

### cloud/

**Purpose:** Cloud integration

**Subdirectories:**
- `distributed/`
- `models/`
- `agents/`

---

### cloud_*/

**Purpose:** Cloud-related modules
- `cloud_experience/`
- `cloud_session/`
- `cloud_workspace/`

---

## Duplicate/Symlinked Directories

| Directory | Symlink To | Status |
|-----------|------------|--------|
| `knowledge_base/` | `knowledge-base/` | Symlink |
| `rie_sprint1/` | `rie-sprint1/` | Symlink |
| `execution_program` | `execution-program` | Symlink |

**Recommendation:** Consolidate to single directory

---

## Directory Purpose Summary

| Category | Count | Examples |
|----------|-------|----------|
| Source Code | 40+ | `agos-kernel/`, `agent_civilization/` |
| Documentation | 10+ | `knowledge-base/`, `agos-canon/` |
| Frontend | 2 | `web/`, `src/` |
| Configuration | 5+ | `.github/`, `.lovable/` |
| Research | 5+ | `research/`, `rie/` |

---

## Analysis Notes

### Orphan Directories
None identified - all directories appear to be referenced

### Outdated Directories
- `sprint-000/` - Initial sprint (may be historical)

### Missing Directories
- `tests/` - Unit tests scattered in modules
- `docs/` - Documentation in knowledge-base

---

*Report generated as part of comprehensive repository analysis*
