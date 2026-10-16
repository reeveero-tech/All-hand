-- SEC-02 (R-138, D-056): tenant ownership of tasks and events.
-- Idempotent; apply after 001_init.sql (scripts/r042/run_local_stack.sh does).
--
-- tenant_id is set by the server from the authenticated principal at insert
-- and is never updated. Rows that existed before this migration keep
-- tenant_id = NULL: their owner cannot be proven, so they are NOT assigned to
-- any tenant and are never returned to a tenant (the server filters
-- tenant_id=eq.<tenant>, which never matches NULL). Only an admin request with
-- all_tenants=true sees them, until an operator assigns them deliberately, e.g.
--   update tasks set tenant_id = '<tenant>' where task_id = '<id>' and tenant_id is null;

alter table tasks  add column if not exists tenant_id text;
alter table events add column if not exists tenant_id text;

-- Read access from /api/tasks and /api/events (tenant filter + newest first).
create index if not exists idx_tasks_tenant_created_at  on tasks  (tenant_id, created_at desc);
create index if not exists idx_events_tenant_created_at on events (tenant_id, created_at desc);
