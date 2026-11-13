-- R-202: emulate the parts of Supabase that docs/sql/v2/001_core.sql relies on,
-- on a plain PostgreSQL TEST database: the roles `anon`, `authenticated`,
-- `service_role` and the function `auth.uid()` (same definition idea as
-- Supabase: the `sub` claim of the request JWT, set by PostgREST as the
-- setting request.jwt.claims). TEST ONLY — never run this file on Supabase,
-- where these objects already exist.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;

create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(
    coalesce(
      current_setting('request.jwt.claim.sub', true),
      (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
    ), '')::uuid
$$;
grant execute on function auth.uid() to anon, authenticated, service_role;

-- R-204 (D-153): the parts of Supabase Auth that docs/sql/v2/002_accounts.sql
-- reads — the columns it uses only. `if not exists`: on a database where the
-- real Supabase Auth server (GoTrue) already created its schema
-- (tests/test_r204_accounts_live.py), its full tables are kept as they are.
create table if not exists auth.users (
  id                 uuid primary key,
  email              text,
  email_confirmed_at timestamptz,
  banned_until       timestamptz,
  deleted_at         timestamptz
);
create table if not exists auth.sessions (
  id        uuid primary key,
  user_id   uuid not null,
  not_after timestamptz
);
