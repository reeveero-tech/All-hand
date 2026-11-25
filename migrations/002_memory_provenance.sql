-- 002_memory_provenance.sql — memory provenance (who asserted each item, and how far it can be trusted).
-- Additive only: adds 4 nullable columns and one CHECK constraint. No row is deleted or rewritten.
-- Existing rows get nature = NULL, which the code reads as 'unverified' (never attributed to the user).
-- Idempotent: safe to run twice, and safe if an earlier draft of this file was already applied.
-- Requires 001_baseline.sql (public.memories must exist).

alter table public.memories add column if not exists nature text;
alter table public.memories add column if not exists subject text;
alter table public.memories add column if not exists evidence text;     -- user quote, or source URL for external_source
alter table public.memories add column if not exists source_goal text;  -- the request that produced the item

alter table public.memories drop constraint if exists memories_nature_check;
alter table public.memories add constraint memories_nature_check check (
  nature is null or nature in ('user_stated','user_confirmed','external_source','task_output','inferred',
                               'assumption','scenario','unverified'));

-- Rollback (only if the feature is reverted; loses provenance of rows saved after the migration):
-- alter table public.memories drop constraint if exists memories_nature_check;
-- alter table public.memories drop column if exists nature, drop column if exists subject,
--   drop column if exists evidence, drop column if exists source_goal;
