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
