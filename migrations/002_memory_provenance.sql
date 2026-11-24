-- Memory provenance (PR: fix/memory-provenance). Additive only: no row is deleted or rewritten.
-- Existing rows get nature = NULL, which the code reads as 'unverified' (never attributed to the user).
alter table public.memories add column if not exists nature text
  check (nature is null or nature in ('user_stated','user_confirmed','task_output','assumption','scenario','unverified'));
alter table public.memories add column if not exists subject text;
alter table public.memories add column if not exists evidence text;
alter table public.memories add column if not exists source_goal text;
-- Rollback (only if the feature is reverted; drops provenance of rows saved after the migration):
-- alter table public.memories drop column nature, drop column subject, drop column evidence, drop column source_goal;
