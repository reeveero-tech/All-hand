-- R-042: emulate the Supabase role model on a plain PostgreSQL so that
-- docs/sql/001_init.sql and supabase_store.py are verified against the same
-- engine Supabase uses (PostgreSQL + PostgREST). On a real Supabase project
-- these roles/grants already exist — do NOT run this file there.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticator') then
    create role authenticator login noinherit password 'authenticator'; end if;
end $$;
grant anon to authenticator;
grant usage on schema public to anon;
-- Supabase default: anon has table privileges on public; RLS (if enabled) restricts rows.
alter default privileges in schema public grant all on tables to anon;
alter default privileges in schema public grant all on sequences to anon;
