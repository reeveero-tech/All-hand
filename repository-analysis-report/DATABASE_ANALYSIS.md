# AGOS Agent Civilization - Database Analysis

**Generated:** 2026-07-23

---

## Database Overview

| Aspect | Details |
|--------|---------|
| **Primary Database** | Supabase (PostgreSQL) |
| **Fallback** | In-memory (Python dict) |
| **ORM** | SQLAlchemy (optional) |
| **Migrations** | Alembic (configured) |

---

## Storage Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Storage Layer                             │
└─────────────────────────────────────────────────────────────┘

                    ┌─────────────────┐
                    │   API Layer     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ get_storage()   │
                    │ (Factory)       │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  Supabase  │  │  In-Memory  │  │  Future:   │
     │  (Primary) │  │  (Fallback) │  │  Redis     │
     └────────────┘  └────────────┘  └────────────┘
```

---

## Supabase Schema

### Configuration

```python
# agent_civilization/storage/supabase_store.py
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
```

### Connection

```python
from supabase import create_client, Client

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
```

---

## Tables

### tasks

**Purpose:** Store task information

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT (PK) | Task UUID |
| `specialization` | TEXT | Agent specialization |
| `prompt` | TEXT | User prompt |
| `status` | TEXT | queued/running/completed/failed |
| `agent_name` | TEXT | Assigned agent |
| `result` | JSONB | Task result data |
| `elapsed_s` | FLOAT | Execution time |
| `created_at` | TIMESTAMP | Creation time |
| `updated_at` | TIMESTAMP | Last update |

**Indexes:**
- `id` (Primary Key)
- `status`
- `created_at`

---

### events

**Purpose:** Audit log of system events

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Event ID |
| `type` | TEXT | Event type |
| `data` | JSONB | Event payload |
| `created_at` | TIMESTAMP | Event time |

**Event Types:**
- `task_created`
- `task_started`
- `task_completed`
- `chat_completed`

---

### agent_stats

**Purpose:** Track agent performance

| Column | Type | Description |
|--------|------|-------------|
| `agent_name` | TEXT (PK) | Agent identifier |
| `specialization` | TEXT | Agent type |
| `tasks_completed` | INTEGER | Successful tasks |
| `tasks_failed` | INTEGER | Failed tasks |
| `total_time_s` | FLOAT | Total execution time |
| `updated_at` | TIMESTAMP | Last update |

---

## In-Memory Storage

### Task Storage

```python
# server.py
TASKS: Dict[str, Dict[str, Any]] = {}

# Structure
{
    "task_id": {
        "id": "abc123",
        "specialization": "analyst",
        "prompt": "Analyze data",
        "status": "completed",
        "created_at": 1700000000.0,
        "finished_at": 1700000005.0,
        "agent": "analyst_0000",
        "result": {...}
    }
}
```

**Limitations:**
- Max 500 tasks (`MAX_TASKS_KEPT`)
- Lost on restart
- No persistence

---

### Event Queue

```python
# server.py
TASK_EVENTS: asyncio.Queue = asyncio.Queue()
```

**Purpose:** Real-time event streaming via SSE

---

## Storage Operations

### Check Configuration

```python
def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)
```

### Insert Task

```python
async def insert_task(task_id: str, specialization: str, prompt: str, status: str):
    if supabase_configured():
        supabase.table("tasks").insert({
            "id": task_id,
            "specialization": specialization,
            "prompt": prompt,
            "status": status,
            "created_at": datetime.utcnow().isoformat()
        }).execute()
```

### Update Task

```python
async def update_task(task_id: str, status: str, **kwargs):
    if supabase_configured():
        data = {"status": status, "updated_at": datetime.utcnow().isoformat()}
        data.update(kwargs)
        supabase.table("tasks").update(data).eq("id", task_id).execute()
```

### Get Tasks

```python
async def get_tasks(limit: int = 50) -> List[Dict]:
    if supabase_configured():
        result = supabase.table("tasks").select("*").order("created_at", desc=True).limit(limit).execute()
        return result.data
    return []
```

---

## Query Patterns

### Get Recent Tasks
```sql
SELECT * FROM tasks 
ORDER BY created_at DESC 
LIMIT 50;
```

### Get Task by ID
```sql
SELECT * FROM tasks 
WHERE id = 'task_id';
```

### Get Agent Stats
```sql
SELECT * FROM agent_stats 
WHERE agent_name = 'analyst_0000';
```

### Get Events by Type
```sql
SELECT * FROM events 
WHERE type = 'task_completed' 
ORDER BY created_at DESC 
LIMIT 100;
```

---

## Row-Level Security (RLS)

**Status:** Not configured

**Recommendation:** Add RLS policies for production:
```sql
-- Example RLS policy
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own tasks"
ON tasks FOR SELECT
USING (auth.uid() = user_id);
```

---

## Migrations

### Alembic Configuration

```python
# alembic.ini
[alembic]
script_location = agos_kernel/alembic
sqlalchemy.url = postgresql://user:pass@localhost/agos
```

### Migration Commands

```bash
# Create migration
alembic revision --autogenerate -m "Add tasks table"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

---

## Performance Considerations

### Indexes

**Current:** Basic indexes on `id` and `created_at`

**Recommended:** Add indexes for common queries:
```sql
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_specialization ON tasks(specialization);
CREATE INDEX idx_events_type ON events(type);
```

### Query Optimization

**Current:** Simple SELECT queries

**Issues:**
- No pagination cursor-based
- No query caching
- Full table scans for large datasets

---

## Backup & Recovery

**Status:** Not implemented

**Recommendation:** Configure Supabase backup:
1. Point-in-time recovery
2. Daily automated backups
3. Cross-region replication for HA

---

## Future Database Improvements

| Improvement | Priority | Notes |
|-------------|----------|-------|
| Add RLS policies | HIGH | Security requirement |
| Cursor-based pagination | MEDIUM | Performance |
| Query caching | MEDIUM | Redis integration |
| Connection pooling | MEDIUM | Supabase Pro |
| Full-text search | LOW | If needed |

---

## Summary

| Aspect | Status |
|--------|--------|
| Schema Design | ✓ Basic tables defined |
| Indexes | ⚠️ Minimal |
| Migrations | ⚠️ Configured but not used |
| RLS | ❌ Not implemented |
| Backups | ❌ Not configured |
| Documentation | ⚠️ Minimal |

---

*Report generated as part of comprehensive repository analysis*
