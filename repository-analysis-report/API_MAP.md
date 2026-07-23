# AGOS Agent Civilization - API Map

**Generated:** 2026-07-23  
**API Framework:** FastAPI  
**Server:** Uvicorn

---

## API Overview

### Base Information
- **Base URL:** `http://localhost:8000` (local)
- **API Version:** 3.0.0
- **Documentation:** `/docs` (Swagger UI), `/redoc` (ReDoc)

### CORS Configuration
- **Allowed Origins:** `*` (All origins - development mode)
- **Allowed Methods:** All
- **Allowed Headers:** All

---

## REST API Endpoints

### System Endpoints

#### `GET /api`
System information and health check.

**Response:**
```json
{
  "name": "AGOS Agent Civilization",
  "version": "3.0.0",
  "status": "alive",
  "specializations": ["analyst", "architect", "builder", ...],
  "docs": "/docs"
}
```

---

#### `GET /api/system/status`
Detailed system status.

**Response:**
```json
{
  "status": "healthy",
  "uptime_s": 3600,
  "llm_configured": true,
  "llm_model": "meta-llama/llama-3.3-70b-instruct:free",
  "supabase_configured": true,
  "specializations": 20,
  "total_agents": 1000,
  "external_agents": 0,
  "tasks_completed": 150,
  "tasks_failed": 5,
  "tasks_in_memory": 23,
  "tasks_in_db": 178
}
```

---

### Agent Endpoints

#### `GET /api/agents`
List all agents.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `specialization` | string | null | Filter by specialization |

**Response:**
```json
{
  "count": 1000,
  "agents": [
    {
      "id": 0,
      "name": "analyst_0000",
      "specialization": "analyst",
      "status": "idle",
      "stats": {
        "tasks_completed": 5,
        "tasks_failed": 0,
        "total_time_s": 12.5
      }
    },
    ...
  ]
}
```

---

#### `GET /api/specializations`
List all specializations.

**Response:**
```json
{
  "specializations": [
    {
      "name": "analyst",
      "description": "Analyze numeric/textual evidence and extract measurable patterns.",
      "agents": 50,
      "icon": "chart-line",
      "tool": "statistics"
    },
    ...
  ]
}
```

---

#### `POST /api/agents/external`
Register an external agent.

**Request:**
```json
{
  "name": "my-ollama",
  "url": "http://localhost:11434/v1/chat/completions",
  "model": "llama3",
  "api_key": ""
}
```

**Response:**
```json
{
  "registered": {
    "name": "my-ollama",
    "url": "http://localhost:11434/v1/chat/completions",
    "model": "llama3",
    "status": "idle"
  }
}
```

---

#### `DELETE /api/agents/external/{name}`
Remove an external agent.

**Response:**
```json
{
  "removed": "my-ollama"
}
```

---

### Task Endpoints

#### `POST /api/tasks`
Create a new task (asynchronous).

**Request:**
```json
{
  "specialization": "analyst",
  "prompt": "Analyze the sales data and find trends",
  "data": {
    "dataset": "sales_q4.csv"
  }
}
```

**Response (202 Accepted):**
```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "queued"
}
```

**Validation:**
- `specialization`: Must be one of the 20 specializations or "external"
- `prompt`: 1-20,000 characters
- `data`: Optional payload

---

#### `GET /api/tasks`
List recent tasks.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max tasks to return (1-200) |

**Response:**
```json
{
  "count": 50,
  "tasks": [...],
  "source": "memory"
}
```

---

#### `GET /api/tasks/{task_id}`
Get task details.

**Response:**
```json
{
  "id": "a1b2c3d4e5f6",
  "specialization": "analyst",
  "prompt": "Analyze the sales data...",
  "status": "completed",
  "created_at": 1700000000.123,
  "finished_at": 1700000012.456,
  "result": {
    "ok": true,
    "agent": "analyst_0000",
    "elapsed_s": 12.333,
    "result": {...}
  },
  "agent": "analyst_0000"
}
```

---

### Chat Endpoint

#### `POST /api/chat`
Natural language command with automatic routing.

**Request:**
```json
{
  "message": "Analyze our API response times and create a report"
}
```

**Response:**
```json
{
  "task_id": "b2c3d4e5f6g7",
  "routed_to": "analyst",
  "agent": "analyst_0015",
  "outcome": {
    "ok": true,
    "elapsed_s": 5.234,
    "result": {
      "analysis": {...}
    }
  }
}
```

**Routing Logic:**
1. If LLM configured: Use LLM to determine best specialization
2. Else: Default to "communicator"
3. Match specialization name in LLM response

---

### Event Endpoints

#### `GET /api/events`
List recent events.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 50 | Max events to return |

**Response:**
```json
{
  "count": 50,
  "events": [
    {
      "type": "task_completed",
      "data": {
        "task_id": "a1b2c3d4e5f6",
        "agent": "analyst_0000",
        "specialization": "analyst",
        "ok": true
      }
    },
    ...
  ]
}
```

---

#### `GET /api/events/stream`
Server-Sent Events (SSE) stream for real-time updates.

**Response Stream:**
```
data: {"type": "task_created", "task_id": "abc123", "specialization": "analyst"}

data: {"type": "task_started", "task_id": "abc123", "agent": "analyst_0000", "specialization": "analyst"}

data: {"type": "task_completed", "task_id": "abc123", "agent": "analyst_0000", "specialization": "analyst", "ok": true, "elapsed_s": 3.5}

data: {"type": "heartbeat"}

data: {"type": "heartbeat"}
...
```

**Headers:**
- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

---

## Civilization Routes

### `POST /civilization/plan`
Generate multi-stage plan for a goal.

**Request:**
```json
{
  "message": "Build a REST API for user management"
}
```

**Response:**
```json
{
  "plan_id": "plan_abc123",
  "stages": [
    {
      "stage": 1,
      "specialization": "architect",
      "description": "Design the API architecture",
      "prompt": "Design a REST API for user management..."
    },
    ...
  ]
}
```

---

### `POST /civilization/execute`
Execute all plan stages.

**Request:**
```json
{
  "message": "Build a REST API for user management"
}
```

**Response:**
```json
{
  "execution_id": "exec_abc123",
  "status": "running",
  "stages": [
    {
      "stage": 1,
      "specialization": "architect",
      "status": "completed",
      "result": {...}
    },
    ...
  ]
}
```

---

### `GET /civilization/registry`
Get agent registry and civilization scale.

**Response:**
```json
{
  "total_addressable": 1000,
  "specializations": 20,
  "agents_per_specialization": 50,
  "external_agents": 0
}
```

---

## Legacy Endpoints

### `GET /api/tasks` (Legacy)
Maintained for backward compatibility.

---

## Error Responses

### Standard Error Format
```json
{
  "detail": "Error message description"
}
```

### Common HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | Task completed |
| 201 | Created | Agent registered |
| 202 | Accepted | Task queued |
| 400 | Bad Request | Invalid specialization |
| 404 | Not Found | Task/agent not found |
| 409 | Conflict | Agent already exists |
| 500 | Server Error | Internal failure |

---

## Data Models

### TaskRequest
```python
class TaskRequest(BaseModel):
    specialization: str  # One of 20 specializations or 'external'
    prompt: str  # 1-20,000 chars
    data: Optional[Any]  # Optional payload
```

### ChatRequest
```python
class ChatRequest(BaseModel):
    message: str  # 1-20,000 chars
```

### ExternalAgentRequest
```python
class ExternalAgentRequest(BaseModel):
    name: str
    url: str  # Must start with http:// or https://
    model: str = ""
    api_key: str = ""
```

---

## Frontend API Proxy

The Vite config proxies API requests:
```typescript
// vite.config.ts
server: {
  proxy: {
    '/api': 'http://localhost:8000'
  }
}
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PORT` | No | 8000 | Server port |
| `OPENROUTER_API_KEY` | For AI | - | LLM API key |
| `LLM_MODEL` | No | meta-llama/... | LLM model name |
| `SUPABASE_URL` | For persistence | - | Supabase project URL |
| `SUPABASE_ANON_KEY` | For persistence | - | Supabase anonymous key |
| `EXTERNAL_AGENTS` | No | [] | JSON list of external agents |

---

## Rate Limiting

**Status:** Not implemented (future feature)

---

## Authentication

**Status:** Not implemented (development mode allows all)

**Planned:** JWT-based authentication

---

*Report generated as part of comprehensive repository analysis*
