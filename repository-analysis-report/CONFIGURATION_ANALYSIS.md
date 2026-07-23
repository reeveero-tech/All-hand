# AGOS Agent Civilization - Configuration Analysis

**Generated:** 2026-07-23

---

## Environment Variables

### Required for AI Features

| Variable | Required | Default | Description | Used By |
|----------|----------|---------|-------------|---------|
| `OPENROUTER_API_KEY` | Conditional | - | LLM API key for OpenRouter | `core/llm.py` |
| `LLM_MODEL` | No | `meta-llama/llama-3.3-70b-instruct:free` | LLM model name | `core/llm.py` |

---

### Optional for Persistence

| Variable | Required | Default | Description | Used By |
|----------|----------|---------|-------------|---------|
| `SUPABASE_URL` | Conditional | - | Supabase project URL | `storage/supabase_store.py` |
| `SUPABASE_ANON_KEY` | Conditional | - | Supabase anonymous key | `storage/supabase_store.py` |
| `VITE_SUPABASE_URL` | No | - | Frontend Supabase URL | `web/src/` |
| `VITE_SUPABASE_ANON_KEY` | No | - | Frontend Supabase key | `web/src/` |

---

### Application Configuration

| Variable | Required | Default | Description | Used By |
|----------|----------|---------|-------------|---------|
| `PORT` | No | 8000 | Server port | `server.py` |
| `AGENTS_PER_SPEC` | No | 3 | Agents per specialization | `run.py` |
| `EXTERNAL_AGENTS` | No | [] | JSON list of external agents | `server.py` |

---

## Configuration Files

### Python Configuration

#### `agos-kernel/pyproject.toml`
```toml
[project]
name = "agos-kernel"
version = "1.0.0"
requires-python = ">=3.10"

[tool.black]
line-length = 100

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.10"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

#### `requirements.txt`
```
PyYAML
aiohttp
fastapi
uvicorn[standard]
pydantic
python-multipart
```

---

### Frontend Configuration

#### `package.json`
```json
{
  "name": "tanstack_start_ts",
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "lint": "eslint ."
  }
}
```

#### `vite.config.ts`
```typescript
// Vite configuration with proxy
```

#### `tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler"
  }
}
```

---

### Deployment Configuration

#### `render.yaml`
Blueprint for Render deployment.

#### `Dockerfile`
Container configuration.

#### `.env.example`
Template for environment variables.

---

## Configuration Flow

```
┌─────────────────────────────────────────────────────────────┐
│                   Configuration Flow                         │
└─────────────────────────────────────────────────────────────┘

Environment Variables (.env)
         │
         ▼
┌─────────────────────┐
│  server.py          │
│  - PORT             │
│  - OPENROUTER_API_KEY│
│  - SUPABASE_*       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐     ┌─────────────────────┐
│  agent_manager.py   │     │  supabase_store.py  │
│  - AGENTS_PER_SPEC  │     │  - SUPABASE_URL     │
└─────────────────────┘     │  - SUPABASE_ANON_KEY│
                            └──────────┬──────────┘
                                       │
                                       ▼
                            ┌─────────────────────┐
                            │   Supabase Client   │
                            └─────────────────────┘
```

---

## Default Values

| Component | Setting | Default | Min | Max |
|-----------|---------|---------|-----|-----|
| Server Port | `PORT` | 8000 | 1 | 65535 |
| Agents/Spec | `AGENTS_PER_SPEC` | 3 | 1 | 1000 |
| Task Memory | `MAX_TASKS_KEPT` | 500 | 100 | 10000 |
| Task Prompt | length | 20,000 chars | 1 | 20,000 |
| LLM Tokens | `max_tokens` | 20 (routing) | - | - |
| SSE Timeout | `timeout` | 30 sec | - | - |

---

## Runtime Configuration

### Server Configuration (`server.py`)

```python
# CORS
allow_origins=["*"]
allow_methods=["*"]
allow_headers=["*"]

# Task Management
MAX_TASKS_KEPT = 500

# LLM Routing
ROUTER_SYSTEM = "dispatcher prompt..."

# Civilizations
CIVILIZATION = create_civilization()
```

### Kernel Configuration (`agos-kernel/kernel.py`)

```python
@dataclass
class KernelConfig:
    name: str = "AGOS-Kernel"
    version: str = "1.0.0"
    enable_logging: bool = True
    enable_governance: bool = True
    enable_knowledge: bool = True
    bootstrap_timeout: int = 60
    health_check_interval: int = 60
```

---

## Feature Flags

| Feature | Environment | Default | Description |
|---------|-------------|---------|-------------|
| LLM Routing | `OPENROUTER_API_KEY` | Disabled | Use LLM for specialization routing |
| Supabase | `SUPABASE_URL` | Disabled | Use database persistence |
| External Agents | `EXTERNAL_AGENTS` | Disabled | Support external AI agents |
| Frontend Supabase | `VITE_SUPABASE_URL` | Disabled | Frontend data access |

---

## Configuration Validation

### Startup Validation

```python
# Check LLM availability
llm = get_llm()
if llm.available:
    # Use LLM routing
else:
    # Use default communicator

# Check Supabase availability
if supabase_configured():
    # Use database storage
else:
    # Use in-memory storage
```

---

## Unused/Missing Configuration

### Unused Variables
- `VITE_SUPABASE_URL` - Not referenced in code
- `VITE_SUPABASE_ANON_KEY` - Not referenced in code

### Recommended Variables
- `LOG_LEVEL` - Missing (would control logging level)
- `MAX_WORKERS` - Missing (for async task limits)
- `RATE_LIMIT` - Missing (for rate limiting)
- `CORS_ORIGINS` - Missing (should replace wildcard)

---

## Environment-Specific Configs

### Development
```bash
OPENROUTER_API_KEY=dev_key
LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
PORT=8000
```

### Production
```bash
OPENROUTER_API_KEY=prod_key
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx
PORT=8000
```

### Minimal
```bash
# No environment variables needed
# Uses all defaults
```

---

## Configuration Security

| Variable | Sensitivity | Risk | Recommendation |
|----------|-------------|------|----------------|
| `OPENROUTER_API_KEY` | HIGH | Key exposure | Use secret manager |
| `SUPABASE_ANON_KEY` | MEDIUM | Public but protect | Use .env, don't commit |
| `EXTERNAL_AGENTS` | MEDIUM | May contain URLs | Validate JSON |

---

## Configuration Documentation

### `.env.example`
```bash
# AGOS Agent Civilization - Environment Variables

# LLM (required for real AI intelligence)
OPENROUTER_API_KEY=your_openrouter_api_key_here
LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free

# Agent count per specialization (default: 3)
AGENTS_PER_SPEC=3

# Supabase (auto-configured in Bolt, set manually for Render)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key_here
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your_anon_key_here

# External agents (optional, JSON list)
# EXTERNAL_AGENTS=[{"name":"my-ollama","url":"http://localhost:11434/v1/chat/completions","model":"llama3","api_key":""}]

# Server port (auto-set by Render)
PORT=8000
```

---

## Summary

| Category | Count |
|----------|-------|
| Required Variables | 0-1 |
| Optional Variables | 7 |
| Configuration Files | 6 |
| Feature Flags | 4 |

---

*Report generated as part of comprehensive repository analysis*
