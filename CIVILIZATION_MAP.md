# AGOS Digital Civilization - Complete Map

**Version:** 1.0  
**Generated:** 2026-07-23  
**Status:** Phase 1 Complete

---

## 🎯 Executive Summary

This document maps the AGOS repository as a **Digital Civilization Operating System** with clearly defined roles, responsibilities, and relationships between all components.

### Civilization Classification

| Category | Arabic | Count | Description |
|----------|--------|-------|-------------|
| **Nucleus (Kernel)** | النواة | 1 | AGOS Kernel - Supreme authority |
| **Ministries** | المؤسسات | 5 | Core system ministries (Registry, Runtime, Brain, Governance, Knowledge) |
| **Citizens** | الموطنون | 1,000+ | Agent specialists - Execute work |
| **Infrastructure** | البنية التحتية | 3 | Storage, API Gateway, External Services |
| **Constitution** | القوانين | 2 | Canons, Policies, Governance |

---

## 🏛️ GOVERNMENT STRUCTURE

```
                    ╔═══════════════════════════════════════════╗
                    ║     DIGITAL CONSTITUTION (agos-canon/)   ║
                    ╚═══════════════════════════════════════════╝
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          AGOS KERNEL (agos-kernel/)                      │
│                           Supreme Authority                              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Ministry of Registration    │  Ministry of Intelligence       │   │
│  │  - Component Registry         │  - Brain Engine                 │   │
│  │  - Capability Registry        │  - Learning Runtime             │   │
│  │  - Provider Registry          │  - Memory Runtime               │   │
│  │  - Workflow Registry          │  - Experience Runtime          │   │
│  │  - Knowledge Registry         │                                │   │
│  └───────────────────────────────┴────────────────────────────────┘   │
│  ┌───────────────────────────────┬────────────────────────────────┐   │
│  │  Ministry of Execution        │  Ministry of Governance         │   │
│  │  - Session Runtime            │  - Policy Engine               │   │
│  │  - Workspace Runtime          │  - Audit Logging               │   │
│  │  - Resource Runtime          │  - Security Policies           │   │
│  │  - State Runtime            │  - Compliance                  │   │
│  └───────────────────────────────┴────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     AGENT CIVILIZATION (agent_civilization/)              │
│                        The Working Citizens                             │
│                                                                          │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐           │
│  │ Analyst  │ Builder  │ Surgeon  │Researcher│ Monitor  │           │
│  │ Ministry │ Ministry │ Ministry │ Ministry │ Ministry │           │
│  │   (50)   │   (50)   │   (50)   │   (50)   │   (50)   │           │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘           │
│                                                                          │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐           │
│  │Reviewer  │Designer  │ Validator│ Strategist│ Educator │           │
│  │ Ministry │ Ministry │ Ministry │ Ministry │ Ministry │           │
│  │   (50)   │   (50)   │   (50)   │   (50)   │   (50)   │           │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘           │
│                                                                          │
│  ... (20 Total Specializations × 50 Agents = 1,000 Citizens)          │
└──────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        INFRASTRUCTURE (Storage, API)                      │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │  Supabase       │  │  In-Memory      │  │  FastAPI Server │         │
│  │  (Persistence)  │  │  (Cache)       │  │  (API Gateway)  │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Component Classification

### 1. NUCLEUS (النواة)

| Component | Path | Role | Type |
|-----------|------|------|------|
| **AGOS Kernel** | `agos-kernel/kernel.py` | Supreme authority, orchestrator | CRITICAL |

**Responsibilities:**
- System bootstrap and initialization
- Dependency management
- Lifecycle control
- Execution orchestration
- Logging and monitoring
- Governance enforcement

---

### 2. MINISTRIES (المؤسسات)

#### 2.1 Ministry of Registration (وزارة التسجيل)

| Component | Path | Role |
|-----------|------|------|
| Component Registry | `agos-kernel/registry/component.py` | Register system components |
| Capability Registry | `agos-kernel/registry/cap_registry.py` | Register capabilities |
| Provider Registry | `agos-kernel/registry/provider_registry.py` | Register providers |
| Workflow Registry | `agos-kernel/registry/workflow_registry.py` | Register workflows |
| Knowledge Registry | `agos-kernel/registry/knowledge_registry.py` | Register knowledge |

#### 2.2 Ministry of Execution (وزارة التنفيذ)

| Component | Path | Role |
|-----------|------|------|
| Session Runtime | `agos-kernel/runtime/session/` | Session management |
| Workspace Runtime | `agos-kernel/runtime/workspace/` | Workspace handling |
| Resource Runtime | `agos-kernel/runtime/resource/` | Resource allocation |
| State Runtime | `agos-kernel/runtime/state/` | State management |
| Recovery Runtime | `agos-kernel/runtime/recovery/` | Failure recovery |

#### 2.3 Ministry of Intelligence (وزارة الذكاء)

| Component | Path | Role |
|-----------|------|------|
| Engineering Brain | `agos-kernel/brain/engine.py` | AI reasoning engine |
| Learning Runtime | `agos-kernel/learning/runtime.py` | Learning system |
| Memory Runtime | `agos-kernel/memory/runtime.py` | Memory management |
| Experience Runtime | `agos-kernel/experience/runtime.py` | Experience tracking |

#### 2.4 Ministry of Governance (وزارة الحكم)

| Component | Path | Role |
|-----------|------|------|
| Policy Engine | `agos-kernel/governance/runtime.py` | Policy enforcement |
| Audit Logging | `agos-kernel/audit/` | Audit trail |
| Security | `agos-kernel/security/` | Security policies |

#### 2.5 Ministry of Knowledge (وزارة المعرفة)

| Component | Path | Role |
|-----------|------|------|
| Knowledge Runtime | `agos-kernel/knowledge/runtime.py` | Knowledge management |
| Knowledge Base | `knowledge-base/` | Documentation system |

---

### 3. CITIZENS (المواطنون) - Agent Specializations

Each specialization is a **Ministry** with 50 **Citizens** (agents).

| Ministry | Arabic | Count | Tool | Purpose |
|----------|-------|-------|------|---------|
| **Analyst** | محلل | 50 | statistics | Numeric/text analysis |
| **API Integrator** | مدمج APIs | 50 | http-get | HTTP API calls |
| **Architect** | مهندس معماري | 50 | decomposition | System architecture |
| **Builder** | بنّاء | 50 | action-extraction | Build actions |
| **Code Generator** | مولد كود | 50 | ast-parse | Code generation |
| **Communicator** | مُ Communicator | 50 | sentiment | Communication/translation |
| **Data Processor** | معالج بيانات | 50 | aggregation | Data processing |
| **DB Manager** | مدير قواعد البيانات | 50 | sqlite | SQL execution |
| **Designer** | مصمم | 50 | hash-palette | Design systems |
| **Educator** | مُعلّم | 50 | keypoint-extraction | Learning extraction |
| **Modifier** | معدّل | 50 | code-normalizer | Code transformation |
| **Monitor** | مراقب | 50 | resource | Runtime metrics |
| **Network Client** | عميل الشبكة | 50 | dns-http | DNS/HTTP probing |
| **Researcher** | باحث | 50 | duckduckgo | Web research |
| **Reviewer** | مراجع | 50 | tokenize | Code review |
| **Self Developer** | مطوّر ذاتي | 50 | coverage-introspection | Self-inspection |
| **Strategist** | استراتيجي | 50 | priority-model | Roadmap planning |
| **Surgeon** | جرّاح | 50 | ast-diagnostics | AST diagnostics |
| **Test Runner** | مُشغّل اختبارات | 50 | python-subprocess | Code testing |
| **Validator** | مُدقّق | 50 | json-parse | Validation |

---

### 4. INFRASTRUCTURE (البنية التحتية)

| Component | Path | Role |
|-----------|------|------|
| **API Gateway** | `server.py` | REST API entry point |
| **Primary Storage** | `agent_civilization/storage/supabase_store.py` | Supabase persistence |
| **Cache Storage** | In-memory dict | Task queue memory |
| **Event Stream** | SSE | Real-time events |
| **LLM Provider** | `agent_civilization/core/llm.py` | AI intelligence |

---

### 5. CONSTITUTION (الدستور)

| Component | Path | Role |
|-----------|------|------|
| **Canons** | `agos-canon/Canons/` | Fundamental principles |
| **Constitution** | `agos-canon/Constitution/` | Governance rules |
| **Specification** | `agos-canon/Specification/` | Technical specs |

---

## 🔗 Dependency Map

```
                    ┌─────────────────┐
                    │    Constitution  │
                    │   (agos-canon)  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   AGOS Kernel    │
                    │    (Nucleus)     │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Ministry of     │ │ Ministry of     │ │ Ministry of     │
│ Registration    │ │ Execution       │ │ Intelligence   │
│ (Registries)    │ │ (Runtime)       │ │ (Brain)        │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         └───────────────────┼───────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                  AGENT CIVILIZATION                      │
│                   (1,000 Citizens)                      │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
│  │Specialist│ │Specialist│ │Specialist│ │Specialist│   │
│  │Ministry │  │Ministry │  │Ministry │  │Ministry │   │
│  │  (50)   │  │  (50)   │  │  (50)   │  │  (50)   │   │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘   │
└─────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE                        │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │  API   │  │Supabase │  │  LLM   │  │  Web   │  │
│  │ Gateway│  │ Storage │  │Provider│  │ Frontend│  │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🚦 Critical Paths

### Path 1: System Startup
```
run.py → server.py → AGOS Kernel → Registries → Civilizations → Ready
```

### Path 2: Task Execution
```
User → API → Dispatcher → Agent → Handler → Result → Storage
```

### Path 3: Natural Language Processing
```
User → /api/chat → LLM Router → Agent → Execute → Response
```

---

## ⚠️ Identified Issues

### Duplicated Code
| Issue | Location | Recommendation |
|-------|----------|----------------|
| Symlinked directories | `knowledge_base/` → `knowledge-base/` | Consolidate |
| Duplicate frontends | `web/` and `src/` | Choose one |
| Repeated agent files | `specialists/*/builder_*.py` | Generate dynamically |

### Missing Components
| Component | Status | Priority |
|-----------|--------|----------|
| Authentication | ❌ Not implemented | HIGH |
| Rate Limiting | ❌ Not implemented | HIGH |
| Structured Logging | ⚠️ Basic only | MEDIUM |
| Comprehensive Tests | ⚠️ ~30 files | MEDIUM |

### Security Concerns
| Issue | Risk | Mitigation |
|-------|------|------------|
| CORS open to all | HIGH | Restrict origins |
| No auth | HIGH | Add JWT/API key |
| Code execution sandbox | MEDIUM | Add Docker/seccomp |

---

## 📊 Resource Allocation

| Resource | Allocation | Status |
|----------|------------|--------|
| CPU | Per-agent async | ✓ Efficient |
| Memory | 500 task limit | ⚠️ Needs tuning |
| Storage | Supabase + memory | ⚠️ Hybrid needed |
| Network | LLM + APIs | ✓ Configurable |

---

## 🎯 Improvement Roadmap

### Phase 1: Stabilization (Immediate)
- [ ] Fix CORS configuration
- [ ] Add basic authentication
- [ ] Implement rate limiting

### Phase 2: Observability (Short-term)
- [ ] Add structured logging
- [ ] Add metrics collection
- [ ] Add health checks

### Phase 3: Scalability (Medium-term)
- [ ] Add Redis queue
- [ ] Implement distributed workers
- [ ] Add load balancing

### Phase 4: Intelligence (Long-term)
- [ ] Enhance learning runtime
- [ ] Improve memory system
- [ ] Add self-evolution

---

## 📁 File Role Classification

### CRITICAL (لا يمكن المساس)
| File | Role | Reason |
|------|------|--------|
| `server.py` | API Gateway | Main entry |
| `agos-kernel/kernel.py` | Nucleus | Supreme authority |
| `agent_civilization/agents/real/real_agents.py` | Citizen Factory | Creates agents |
| `agent_civilization/core/agents/real_dispatcher.py` | Task Dispatcher | Routes work |

### HIGH (تغيير بحذر)
| File | Role |
|------|------|
| `run.py` | Startup script |
| `agos-kernel/registry/*.py` | Registration system |
| `agos-kernel/runtime/*.py` | Execution system |

### MEDIUM (تحسين مسموح)
| File | Role |
|------|------|
| `agent_civilization/storage/*.py` | Storage layer |
| `agos-kernel/brain/*.py` | Intelligence |
| `agos-kernel/governance/*.py` | Governance |

### LOW (قابل للتحسين)
| File | Role |
|------|------|
| `web/src/*` | Frontend |
| `knowledge-base/*` | Documentation |

---

## 🔍 Gap Analysis

### Architecture Gaps
| Gap | Impact | Recommendation |
|-----|--------|----------------|
| No clear plugin architecture | Extensibility | Add plugin system |
| Mixed responsibilities | Maintainability | Separate concerns |
| No adapter pattern | Flexibility | Add abstraction layer |

### Technical Gaps
| Gap | Impact | Recommendation |
|-----|--------|----------------|
| No Redis queue | Scalability | Add Redis |
| No auth middleware | Security | Add JWT |
| No rate limiter | Security | Add slowapi |

### Documentation Gaps
| Gap | Impact | Recommendation |
|-----|--------|----------------|
| No API reference | Usability | Add OpenAPI docs |
| No SDK docs | Adoption | Add SDK guide |
| Scattered READMEs | Discoverability | Centralize |

---

## ✅ Phase 1 Completion Checklist

- [x] Repository analyzed
- [x] Components classified
- [x] Dependencies mapped
- [x] Critical paths identified
- [x] Issues documented
- [x] Gaps analyzed
- [x] CIVILIZATION_MAP.md created

---

**Status:** Phase 1 Complete ✓  
**Next:** Proceed to Phase 2 - Architecture Stabilization

---

*Generated by AI Engineering Analysis System*
*Part of AGOS Digital Civilization Platform*
