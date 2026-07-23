# AGOS Agent Civilization - Agent Analysis

**Generated:** 2026-07-23

---

## Overview

AGOS implements a **Civilization Pattern** where 20 specialized agent types work together to accomplish user goals. Each specialization has 50 agent instances = **1,000 total local agents**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Civilizations                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │             User Request (message)                   │    │
│  └─────────────────────────────────────────────────────┘    │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │            LLM Router (optional)                     │  │
│  │     Determines best specialization                  │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │            Task Dispatcher                          │  │
│  │     Routes to specific specialization pool          │  │
│  └─────────────────────────────────────────────────────┘  │
│                           │                                 │
│         ┌─────────────────┼─────────────────┐             │
│         │                 │                 │             │
│         ▼                 ▼                 ▼             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐      │
│  │  Analyst    │   │  Builder    │   │  Researcher │      │
│  │  (50 agents)│   │  (50 agents)│   │  (50 agents)│      │
│  └─────────────┘   └─────────────┘   └─────────────┘      │
│         │                 │                 │             │
│         └─────────────────┼─────────────────┘             │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐  │
│  │           Real Dispatcher                           │  │
│  │   Executes real operations (AST, HTTP, SQL, etc.)  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 20 Specializations

### Development Specializations (10)

| Name | Icon | Tool | Purpose |
|------|------|------|---------|
| **builder** | hammer | action-extraction | Convert goals into concrete build actions |
| **surgeon** | wrench | ast-diagnostics | Diagnose code defects with AST inspection |
| **test_runner** | flask | python-subprocess | Run Python test/code snippets |
| **code_generator** | code | ast-parse | Parse, validate, produce implementation code |
| **modifier** | edit | code-normalizer | Transform and normalize code/config |
| **reviewer** | shield-check | tokenize | Review code/text with metrics |
| **architect** | network | decomposition | Design system architecture |
| **designer** | palette | hash-palette | Generate design systems and UI specs |
| **self_developer** | sparkles | coverage-introspection | Inspect AGOS capability coverage |
| **db_manager** | database | sqlite | Validate/execute SQL against SQLite |

---

### Analysis Specializations (4)

| Name | Icon | Tool | Purpose |
|------|------|------|---------|
| **analyst** | chart-line | statistics | Analyze numeric/textual evidence |
| **researcher** | search | duckduckgo | Live web-backed research |
| **monitor** | activity | resource | Capture live runtime metrics |
| **validator** | check-circle | json-parse | Validate JSON and Python objects |

---

### Communication Specializations (2)

| Name | Icon | Tool | Purpose |
|------|------|------|---------|
| **communicator** | messages | sentiment | Rewrite, translate, measure tone |
| **educator** | graduation-cap | keypoint-extraction | Extract learning points |

---

### Infrastructure Specializations (4)

| Name | Icon | Tool | Purpose |
|------|------|------|---------|
| **api_integrator** | plug | http-get | Call HTTP APIs with live metadata |
| **network_client** | wifi | dns-http | DNS resolution and HTTP probing |
| **strategist** | map | priority-model | Prioritize goals into roadmaps |
| **data_processor** | database-zap | aggregation | Clean, aggregate, summarize data |

---

## Agent Structure

### RealSpecialistAgent

```python
@dataclass
class RealSpecialistAgent:
    name: str              # "analyst_0000"
    specialization: str    # "analyst"
    agent_id: int          # 0
    status: str            # "idle" | "working"
    stats: Dict            # Task counts, total time
```

### Agent Lifecycle

```
1. Created (idle)
2. Task assigned
3. Status → "working"
4. Execute task via real_dispatcher
5. Status → "idle"
6. Update stats
```

---

## Task Dispatcher

### Real Dispatcher Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     dispatch(specialization, work)          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │   Match specialization      │
              └─────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ analyst  │        │ builder  │        │ research │
   │ handler  │        │ handler  │        │ handler  │
   └──────────┘        └──────────┘        └──────────┘
```

### Coverage Report

Generated via `real_coverage()`:

```python
{
    "count": 20,                    # Implemented handlers
    "total_specializations": 20,    # Total specializations
    "coverage_percent": 100.0,      # Coverage percentage
    "default_local_agents": 1000,   # 20 × 50
}
```

---

## Agent Pools

### Pool Management

```python
def create_civilization(agents_per_spec: int = 50):
    civilization = {}
    for spec in SPECIALIZATIONS:
        pool = []
        for _ in range(agents_per_spec):
            pool.append(RealSpecialistAgent(...))
        civilization[spec] = pool
    return civilization
```

### Load Balancing

```python
def _pick_agent(specialization: str):
    pool = CIVILIZATION.get(specialization)
    idle = [a for a in pool if a.status == "idle"]
    return (idle or pool)[0]  # Prefer idle, fallback to any
```

**Strategy:** Round-robin with idle preference

---

## External Agents

### OpenAI-Compatible Adapter

```python
class ExternalAgentAdapter:
    name: str
    url: str                    # OpenAI-compatible endpoint
    model: str
    api_key: str
    stats: Dict                # Tasks completed, failed, time
```

### Registration Flow

```
1. POST /api/agents/external
2. Validate URL (http/https)
3. Check for duplicates
4. Create adapter
5. Add to EXTERNAL_AGENTS list
```

---

## Specialization Handlers

### Handler Examples

#### analyst
- Statistics computation
- Numeric analysis
- Pattern extraction

#### builder
- Goal decomposition
- Action extraction
- Build planning

#### surgeon
- AST parsing
- Code diagnostics
- Defect detection

#### test_runner
- Python subprocess execution
- Timeout enforcement
- Output capture

#### researcher
- Web search (DuckDuckGo)
- Source gathering
- Citation extraction

---

## Tool Capabilities

| Tool | Specializations | Operations |
|------|----------------|------------|
| `statistics` | analyst | mean, median, std, etc. |
| `http-get` | api_integrator | GET, POST, headers |
| `decomposition` | architect | System breakdown |
| `ast-parse` | code_generator, surgeon | AST analysis |
| `sqlite` | db_manager | SQL execution |
| `duckduckgo` | researcher | Web search |
| `tokenize` | reviewer | Text tokenization |
| `python-subprocess` | test_runner | Code execution |
| `json-parse` | validator | JSON validation |

---

## External Agent Integration

### Protocol
- OpenAI Chat Completions compatible
- Streaming support
- Custom headers for auth

### Configuration

```python
EXTERNAL_AGENTS = [
    {
        "name": "my-ollama",
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "llama3",
        "api_key": ""
    }
]
```

### Limitations
- No sandboxing for external agents
- No capability validation
- Full trust model

---

## Monitoring & Stats

### Per-Agent Stats

```python
stats = {
    "tasks_completed": 5,
    "tasks_failed": 1,
    "total_time_s": 123.456
}
```

### System Stats

```python
{
    "specializations": 20,
    "total_agents": 1000,
    "external_agents": 0,
    "tasks_completed": 150,
    "tasks_failed": 5
}
```

---

## Entry Points

### Direct Execution
```python
await run_real("analyst", "Analyze sales data", data)
```

### Via API
```bash
POST /api/tasks
{
  "specialization": "analyst",
  "prompt": "Analyze sales data"
}
```

### Via Chat
```bash
POST /api/chat
{
  "message": "Analyze our API response times"
}
```

---

## Limitations

| Aspect | Current | Ideal |
|--------|---------|-------|
| Scaling | In-process | Distributed workers |
| Fault Tolerance | Basic retry | Circuit breakers |
| Monitoring | Stats only | Full observability |
| External Agents | No isolation | Sandboxed execution |

---

## Summary

| Metric | Value |
|--------|-------|
| Total Specializations | 20 |
| Agents per Specialization | 50 |
| Total Local Agents | 1,000 |
| External Agents | Unlimited |
| Handler Coverage | 100% |
| Tool Categories | 20+ |

---

*Report generated as part of comprehensive repository analysis*
