-- 001_baseline.sql
-- Documents the schema the current code already expects (app/store.py, app/memory.py).
-- Idempotent: safe to run on a database where these tables already exist.
-- DO NOT apply to production without the owner's explicit approval.

create table if not exists public.runs (
  id text primary key,
  goal text not null,
  status text not null,
  result text,
  data jsonb not null,
  agents_spawned int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists runs_created on public.runs (created_at desc);
alter table public.runs enable row level security;

create table if not exists public.memories (
  id text primary key,
  space text not null,
  kind text not null check (kind in ('profile','project','decision','finding','task')),
  content text not null,
  run_id text,
  importance int not null default 3,
  created_at timestamptz not null default now()
);
create index if not exists memories_space_created on public.memories (space, created_at desc);
alter table public.memories enable row level security;
-- RLS is enabled with no policies: only the service key (server side) can read/write.
