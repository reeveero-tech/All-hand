-- R-163 (D-082): durable task queue columns on the tasks table.
-- Idempotent; apply after 001_init.sql and 002_tenant_isolation.sql
-- (scripts/r042/run_local_stack.sh does).
--
-- Queue semantics (server-side, agent_civilization/core/task_queue.py):
--   status: the internal lifecycle state (accepted/queued/claimed/running/
--           waiting/retrying/completed/failed/cancelled/dead_letter).
--   claim_token: fencing token written by the ATOMIC conditional claim
--           (update ... where status='queued'); a stale worker's write
--           (conditional on its token) matches 0 rows and is discarded.
--   lease_expires_at: the claim's deadline; the reaper recovers rows whose
--           lease expired while still claimed/running (worker death).
--   attempts: executions that reached running; recovery dead-letters a row
--           whose attempts already reached max_attempts.
--   payload: the request's data field, persisted so a recovered task can be
--           re-executed after a restart (tenant-scoped, same as the row).
--   idempotency_key: client key; one task per (tenant_id, key) — a duplicate
--           POST with the same key replays the existing task and creates
--           no second side effect.

alter table tasks add column if not exists claim_token     text;
alter table tasks add column if not exists lease_expires_at timestamptz;
alter table tasks add column if not exists attempts        integer not null default 0;
alter table tasks add column if not exists max_attempts    integer not null default 3;
alter table tasks add column if not exists payload        jsonb;
alter table tasks add column if not exists idempotency_key text;

-- One task per (tenant, idempotency key). Partial: NULL keys (no idempotency
-- requested) don't collide — Postgres unique indexes ignore NULL rows.
create unique index if not exists uq_tasks_tenant_idempotency_key
    on tasks (tenant_id, idempotency_key)
    where idempotency_key is not null;

-- The dispatcher's claim scan: queued rows, oldest first.
create index if not exists idx_tasks_queue_pick
    on tasks (status, created_at)
    where status = 'queued';

-- The reaper's expiry scan: leased rows past their deadline.
create index if not exists idx_tasks_lease_expiry
    on tasks (lease_expires_at)
    where status in ('claimed', 'running');
