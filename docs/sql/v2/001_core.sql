-- R-202 (D-151): AGOS v2 core data model — migration 001.
--
-- Scope: the core objects of v2 (workspace, member, agent spec, conversation,
-- message, task, step, artifact, task event, memory item). Every row belongs
-- to ONE workspace; child rows are tied to their parent AND to the same
-- workspace by composite foreign keys, so a row can never point into another
-- workspace.
--
-- Where it lives: a separate schema `agos`, NOT `public`. The v1 tables
-- (public.tasks / events / agent_stats from docs/sql/001..003) stay untouched
-- while v1 is still serving; v2 is built beside it and v1 is retired later
-- (R-203 / R-224). Nothing here is applied to the live Supabase project in
-- R-202 — it is verified on a test database only (tests/test_r202_v2_schema.py).
--
-- Requirements: PostgreSQL 15+ (NULLS NOT DISTINCT), pgvector (`vector`),
-- the Supabase roles `authenticated` / `service_role` and `auth.uid()`.
-- On a plain PostgreSQL use scripts/v2/supabase_auth_stub.sql first
-- (test only — never run that file on Supabase).
--
-- Encoded rules (checked by the database, not only by the code):
--   * "no success without verification": a task can be `completed` only with
--     `verified_at`; a step can be `succeeded` only with verification.passed = true;
--   * "a failure is declared": `failed` needs a non-empty error {code, message}
--     (written with an explicit `error is not null`: a CHECK whose result is
--     NULL passes in PostgreSQL — found by tests/test_r202_v2_schema.py);
--   * terminal states have `finished_at`, non-terminal states do not;
--   * attempts never exceed max_attempts (bounded self-correction);
--   * task_events are append-only (no UPDATE) — the durable context log.

create extension if not exists vector;

create schema if not exists agos;

-- ------------------------------------------------------------------ helpers

create or replace function agos.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create or replace function agos.forbid_update() returns trigger
language plpgsql as $$
begin
  raise exception 'agos.%: rows are append-only', tg_table_name
    using errcode = 'restrict_violation';
end $$;

-- ------------------------------------------------------------- workspaces

create table agos.workspaces (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (length(btrim(name)) between 1 and 200),
  created_at  timestamptz not null default now()
);

create table agos.workspace_members (
  workspace_id uuid not null references agos.workspaces(id) on delete cascade,
  user_id      uuid not null,             -- auth.users.id on Supabase (R-204)
  role         text not null check (role in ('owner', 'admin', 'member')),
  created_at   timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index idx_members_user on agos.workspace_members (user_id);

-- Membership test used by every RLS policy. SECURITY DEFINER so the policy
-- on workspace_members does not recurse into itself.
create or replace function agos.is_member(ws uuid) returns boolean
language sql stable security definer set search_path = agos, pg_temp as $$
  select exists (
    select 1 from agos.workspace_members m
    where m.workspace_id = ws and m.user_id = auth.uid()
  )
$$;

-- ------------------------------------------------------------ agent specs
-- `spec` holds the AgentSpec contract body (docs/governance/contracts/v2/
-- agent_spec.v1.schema.json). workspace_id NULL = a platform spec visible to
-- every workspace; otherwise private to its workspace.

create table agos.agent_specs (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid references agos.workspaces(id) on delete cascade,
  slug         text not null check (slug ~ '^[a-z][a-z0-9_]{1,62}$'),
  version      integer not null check (version >= 1),
  name         text not null check (length(btrim(name)) between 1 and 200),
  status       text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  spec         jsonb not null check (jsonb_typeof(spec) = 'object'),
  created_at   timestamptz not null default now(),
  unique nulls not distinct (workspace_id, slug, version),
  check (spec->>'slug' = slug and (spec->>'version')::int = version)
);

-- ---------------------------------------------------------- conversations

create table agos.conversations (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references agos.workspaces(id) on delete cascade,
  created_by   uuid,
  title        text not null default '' check (length(title) <= 300),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (id, workspace_id)
);
create trigger trg_conversations_touch before update on agos.conversations
  for each row execute function agos.touch_updated_at();

-- ------------------------------------------------------------------ tasks

create table agos.tasks (
  id                  uuid primary key default gen_random_uuid(),
  workspace_id        uuid not null references agos.workspaces(id) on delete cascade,
  created_by          uuid,                 -- NULL = created through a server/developer key
  conversation_id     uuid,
  parent_task_id      uuid,
  title               text not null check (length(btrim(title)) between 1 and 300),
  goal                text not null check (length(btrim(goal)) between 1 and 20000),
  acceptance_criteria jsonb not null default '[]'
                      check (jsonb_typeof(acceptance_criteria) = 'array'),
  status              text not null default 'queued' check (status in (
                        'queued', 'claimed', 'planning', 'running', 'waiting_approval',
                        'verifying', 'completed', 'failed', 'cancelled')),
  priority            smallint not null default 0 check (priority between -10 and 10),
  budget              jsonb not null default '{}' check (jsonb_typeof(budget) = 'object'),
  idempotency_key     text check (idempotency_key is null or length(idempotency_key) between 1 and 200),
  -- durable-queue mechanics reused from v1 (docs/sql/003_task_queue.sql, R-163)
  claim_token         text,
  lease_expires_at    timestamptz,
  attempts            integer not null default 0 check (attempts >= 0),
  max_attempts        integer not null default 3 check (max_attempts between 1 and 20),
  result_summary      text,
  error               jsonb,
  verified_at         timestamptz,
  tokens_in           bigint not null default 0 check (tokens_in >= 0),
  tokens_out          bigint not null default 0 check (tokens_out >= 0),
  cost_usd            numeric(14, 6) not null default 0 check (cost_usd >= 0),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  started_at          timestamptz,
  finished_at         timestamptz,
  unique (id, workspace_id),
  foreign key (conversation_id, workspace_id)
    references agos.conversations (id, workspace_id) on delete set null (conversation_id),
  foreign key (parent_task_id, workspace_id)
    references agos.tasks (id, workspace_id) on delete cascade,
  check (attempts <= max_attempts),
  check (status <> 'completed' or verified_at is not null),
  check (status <> 'failed' or (error is not null and error ? 'code' and error ? 'message'
                                and length(error->>'message') > 0)),
  check ((status in ('completed', 'failed', 'cancelled')) = (finished_at is not null)),
  check (error is null or jsonb_typeof(error) = 'object')
);
create unique index uq_tasks_ws_idempotency on agos.tasks (workspace_id, idempotency_key)
  where idempotency_key is not null;
create index idx_tasks_queue_pick on agos.tasks (priority desc, created_at)
  where status = 'queued';
create index idx_tasks_lease_expiry on agos.tasks (lease_expires_at)
  where status in ('claimed', 'planning', 'running', 'verifying');
create index idx_tasks_ws_created on agos.tasks (workspace_id, created_at desc);
create trigger trg_tasks_touch before update on agos.tasks
  for each row execute function agos.touch_updated_at();

-- ------------------------------------------------------------------ steps
-- One node of a task's plan graph. kind: plan / act / verify / fix / review /
-- approval / final. depends_on = ids of steps of the SAME task (checked by
-- the orchestrator; arrays cannot carry foreign keys).

create table agos.steps (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid not null,
  task_id        uuid not null,
  parent_step_id uuid,
  seq            integer not null check (seq >= 0),
  kind           text not null check (kind in ('plan', 'act', 'verify', 'fix', 'review',
                                               'approval', 'final')),
  title          text not null check (length(btrim(title)) between 1 and 300),
  agent_spec_id  uuid references agos.agent_specs(id) on delete restrict,
  status         text not null default 'pending' check (status in (
                   'pending', 'ready', 'running', 'waiting_approval',
                   'succeeded', 'failed', 'skipped', 'cancelled')),
  depends_on     uuid[] not null default '{}',
  acceptance     jsonb not null default '[]' check (jsonb_typeof(acceptance) = 'array'),
  attempt        integer not null default 0 check (attempt >= 0),
  max_attempts   integer not null default 3 check (max_attempts between 1 and 10),
  input          jsonb,
  output         jsonb,
  verification   jsonb,
  error          jsonb,
  model          text,
  tokens_in      bigint not null default 0 check (tokens_in >= 0),
  tokens_out     bigint not null default 0 check (tokens_out >= 0),
  cost_usd       numeric(14, 6) not null default 0 check (cost_usd >= 0),
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz,
  unique (id, workspace_id),
  unique (task_id, seq),
  foreign key (task_id, workspace_id) references agos.tasks (id, workspace_id) on delete cascade,
  foreign key (parent_step_id, workspace_id) references agos.steps (id, workspace_id) on delete cascade,
  check (not (id = any (depends_on))),
  check (attempt <= max_attempts),
  check (status <> 'succeeded' or (verification is not null
                                   and verification->>'passed' = 'true')),
  check (status <> 'failed' or (error is not null and error ? 'code' and error ? 'message'
                                and length(error->>'message') > 0)),
  check ((status in ('succeeded', 'failed', 'skipped', 'cancelled')) = (finished_at is not null)),
  check (verification is null or jsonb_typeof(verification) = 'object'),
  check (error is null or jsonb_typeof(error) = 'object')
);
create index idx_steps_task on agos.steps (task_id, seq);

-- --------------------------------------------------------------- messages

create table agos.messages (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null,
  conversation_id uuid not null,
  seq             bigint not null check (seq >= 0),
  role            text not null check (role in ('user', 'assistant', 'system', 'tool')),
  content         text not null check (length(content) <= 200000),
  task_id         uuid,
  author_id       uuid,
  created_at      timestamptz not null default now(),
  unique (conversation_id, seq),
  foreign key (conversation_id, workspace_id)
    references agos.conversations (id, workspace_id) on delete cascade,
  foreign key (task_id, workspace_id)
    references agos.tasks (id, workspace_id) on delete set null (task_id)
);

-- -------------------------------------------------------------- artifacts
-- A task output: either inline (small text/code/data) or a file in object
-- storage (storage_path) with its size and sha256 — exactly one of the two.

create table agos.artifacts (
  id             uuid primary key default gen_random_uuid(),
  workspace_id   uuid not null,
  task_id        uuid not null,
  step_id        uuid,
  kind           text not null check (kind in ('file', 'text', 'code', 'data', 'link')),
  name           text not null check (length(btrim(name)) between 1 and 300),
  mime_type      text not null check (mime_type ~ '^[a-z]+/[a-z0-9.+-]+'),
  inline_content text check (inline_content is null or length(inline_content) <= 1000000),
  storage_path   text check (storage_path is null or storage_path ~ '^[A-Za-z0-9/_.-]+$'),
  size_bytes     bigint not null check (size_bytes >= 0),
  sha256         text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at     timestamptz not null default now(),
  foreign key (task_id, workspace_id) references agos.tasks (id, workspace_id) on delete cascade,
  foreign key (step_id, workspace_id) references agos.steps (id, workspace_id) on delete set null (step_id),
  check ((inline_content is null) <> (storage_path is null))
);
create index idx_artifacts_task on agos.artifacts (task_id);

-- ------------------------------------------------------------ task events
-- Append-only log of everything that happens to a task (decisions, tool
-- calls, verifications, retries). This is what makes a task resumable
-- without relying on the model's context window.

create table agos.task_events (
  id           bigint generated always as identity primary key,
  workspace_id uuid not null,
  task_id      uuid not null,
  step_id      uuid,
  type         text not null check (type ~ '^[a-z][a-z0-9_.]{1,63}$'),
  data         jsonb not null default '{}' check (jsonb_typeof(data) = 'object'),
  created_at   timestamptz not null default now(),
  foreign key (task_id, workspace_id) references agos.tasks (id, workspace_id) on delete cascade,
  foreign key (step_id, workspace_id) references agos.steps (id, workspace_id) on delete set null (step_id)
);
create index idx_task_events_task on agos.task_events (task_id, id);
create trigger trg_task_events_append_only before update on agos.task_events
  for each row execute function agos.forbid_update();

-- ----------------------------------------------------------- memory items
-- Task / user / workspace memory. The embedding dimension depends on the
-- embedding model, so the column is an untyped `vector` and the ANN index is
-- created per model in R-216 (not here).

create table agos.memory_items (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references agos.workspaces(id) on delete cascade,
  scope           text not null check (scope in ('task', 'user', 'workspace')),
  user_id         uuid,
  task_id         uuid,
  content         text not null check (length(btrim(content)) between 1 and 50000),
  embedding       vector,
  embedding_model text,
  source          jsonb not null default '{}' check (jsonb_typeof(source) = 'object'),
  created_at      timestamptz not null default now(),
  foreign key (task_id, workspace_id) references agos.tasks (id, workspace_id) on delete cascade,
  check ((embedding is null) = (embedding_model is null)),
  check (scope <> 'task' or task_id is not null),
  check (scope <> 'user' or user_id is not null)
);
create index idx_memory_ws_scope on agos.memory_items (workspace_id, scope);

-- ------------------------------------------------------- row level security
-- Every table: RLS on; `authenticated` sees and writes only rows of the
-- workspaces it is a member of; `anon` gets nothing; `service_role` (server
-- and workers) bypasses RLS. Role-specific rules (who may delete, invite...)
-- are refined in R-204.

alter table agos.workspaces        enable row level security;
alter table agos.workspace_members enable row level security;
alter table agos.agent_specs       enable row level security;
alter table agos.conversations     enable row level security;
alter table agos.tasks             enable row level security;
alter table agos.steps             enable row level security;
alter table agos.messages          enable row level security;
alter table agos.artifacts         enable row level security;
alter table agos.task_events       enable row level security;
alter table agos.memory_items      enable row level security;

create policy ws_read on agos.workspaces for select to authenticated
  using (agos.is_member(id));
create policy members_read on agos.workspace_members for select to authenticated
  using (agos.is_member(workspace_id));
create policy specs_read on agos.agent_specs for select to authenticated
  using (workspace_id is null or agos.is_member(workspace_id));
create policy specs_write on agos.agent_specs for all to authenticated
  using (workspace_id is not null and agos.is_member(workspace_id))
  with check (workspace_id is not null and agos.is_member(workspace_id));

create policy conversations_rw on agos.conversations for all to authenticated
  using (agos.is_member(workspace_id)) with check (agos.is_member(workspace_id));
create policy tasks_rw on agos.tasks for all to authenticated
  using (agos.is_member(workspace_id)) with check (agos.is_member(workspace_id));
create policy steps_rw on agos.steps for all to authenticated
  using (agos.is_member(workspace_id)) with check (agos.is_member(workspace_id));
create policy messages_rw on agos.messages for all to authenticated
  using (agos.is_member(workspace_id)) with check (agos.is_member(workspace_id));
create policy artifacts_rw on agos.artifacts for all to authenticated
  using (agos.is_member(workspace_id)) with check (agos.is_member(workspace_id));
create policy events_read on agos.task_events for select to authenticated
  using (agos.is_member(workspace_id));
create policy events_insert on agos.task_events for insert to authenticated
  with check (agos.is_member(workspace_id));
create policy memory_rw on agos.memory_items for all to authenticated
  using (agos.is_member(workspace_id) and (user_id is null or user_id = auth.uid()))
  with check (agos.is_member(workspace_id) and (user_id is null or user_id = auth.uid()));

-- ------------------------------------------------------------------ grants

revoke all on schema agos from public;
grant usage on schema agos to authenticated, service_role;
grant select, insert, update, delete on all tables in schema agos to authenticated, service_role;
grant usage, select on all sequences in schema agos to authenticated, service_role;
revoke execute on all functions in schema agos from public;
grant execute on function agos.is_member(uuid) to authenticated, service_role;
