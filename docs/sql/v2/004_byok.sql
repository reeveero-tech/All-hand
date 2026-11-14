-- R-205 (D-154): AGOS v2 — user AI provider keys (BYOK) — migration 004.
--
-- What the database holds, and what it does NOT:
--   * agos.provider_keys keeps ONLY ciphertext: the user's key is encrypted
--     by the server BEFORE it is sent here (AES-256-GCM with a fresh per-key
--     data key; that data key is itself encrypted with the server's key
--     encryption key AGOS_BYOK_KEK — envelope encryption, the R-189 cipher).
--     The plaintext key never reaches PostgreSQL: not in a row, not in a
--     query parameter, not in a statement log. The KEK never reaches it either.
--   * last4 is the only part of the key stored readable (shown to the owner).
--
-- Access rules (enforced here, not only in the API):
--   * a key belongs to ONE user inside ONE workspace. Only that user, while
--     still a member with a live session, sees its metadata — not other
--     members, not workspace owners/admins, not other workspaces;
--   * nobody reads ciphertext columns through table grants (column grants
--     below); the server gets the encrypted material only through
--     agos.byok_key_material(), which refuses a revoked, deleted or foreign
--     key — so revoke/delete stop use at the next request;
--   * writes go only through the functions below (no direct insert/update);
--   * revoke wipes the ciphertext in the same statement (crypto-shredding);
--     delete removes the row; leaving/being removed from the workspace deletes
--     the member's keys there (trigger).
--   * usage/cost counters are stored per key; R-205 only records and shows
--     them (no quota, no billing). R-206 (model gateway) feeds them.
--
-- Errors: AG404 not found / not yours · AG409 too many keys · AG410 revoked ·
--         AG422 invalid input (same mapping as 002_accounts.sql).

create table agos.provider_keys (
  id               uuid primary key,          -- chosen by the server: bound into the cipher's AAD before insert
  workspace_id     uuid not null references agos.workspaces(id) on delete cascade,
  user_id          uuid not null,             -- auth.users.id (like workspace_members.user_id)
  provider         text not null check (provider in
                     ('openai', 'anthropic', 'google', 'openrouter', 'mistral', 'groq', 'ollama')),
  label            text not null check (length(btrim(label)) between 1 and 80),
  base_url         text check (base_url is null or length(base_url) between 8 and 300),
  status           text not null default 'active' check (status in ('active', 'revoked')),
  last4            text check (last4 is null or length(last4) = 4),
  key_ct           bytea,
  key_nonce        bytea,
  dek_wrapped      bytea,
  dek_nonce        bytea,
  kek_id           text check (kek_id is null or kek_id ~ '^[0-9a-f]{16}$'),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  revoked_at       timestamptz,
  last_test_at     timestamptz,
  last_test_ok     boolean,
  last_test_reason text check (last_test_reason is null or last_test_reason ~ '^[a-z_]{1,40}$'),
  use_count        bigint not null default 0 check (use_count >= 0),
  last_used_at     timestamptz,
  tokens_in        bigint not null default 0 check (tokens_in >= 0),
  tokens_out       bigint not null default 0 check (tokens_out >= 0),
  cost_usd         numeric(14, 6) not null default 0 check (cost_usd >= 0),
  -- the encrypted material is all-or-nothing
  constraint provider_keys_material_whole check (
    (key_ct is null) = (key_nonce is null) and (key_ct is null) = (dek_wrapped is null)
    and (key_ct is null) = (dek_nonce is null) and (key_ct is null) = (kek_id is null)
    and (key_ct is null or last4 is not null)),
  -- last4 belongs to a stored key: a key-less Ollama entry has none (kept after revoke for display)
  constraint provider_keys_last4_has_key check (last4 is null or key_ct is not null or status = 'revoked'),
  -- a revoked key keeps NO ciphertext; an active cloud key always has one
  constraint provider_keys_revoked_is_shredded check (status = 'active' or key_ct is null),
  constraint provider_keys_cloud_needs_key check (
    status = 'revoked' or provider = 'ollama' or key_ct is not null),
  -- only the local Ollama entry has a URL (cloud endpoints are fixed in the server)
  constraint provider_keys_url_only_ollama check ((provider = 'ollama') = (base_url is not null)),
  constraint provider_keys_revoked_at check ((status = 'revoked') = (revoked_at is not null))
);
create index idx_provider_keys_owner on agos.provider_keys (workspace_id, user_id);

create trigger trg_provider_keys_touch before update on agos.provider_keys
  for each row execute function agos.touch_updated_at();
create trigger trg_provider_keys_ws before update on agos.provider_keys
  for each row execute function agos.forbid_workspace_change();

alter table agos.provider_keys enable row level security;
create policy provider_keys_read on agos.provider_keys for select to authenticated
  using ((select agos.session_is_active()) and user_id = (select auth.uid())
         and agos.is_member(workspace_id));

-- A member who leaves (or is removed from) a workspace loses its keys there.
create or replace function agos.byok_drop_member_keys() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
begin
  delete from agos.provider_keys k where k.workspace_id = old.workspace_id and k.user_id = old.user_id;
  return old;
end $$;
create trigger trg_members_drop_keys after delete on agos.workspace_members
  for each row execute function agos.byok_drop_member_keys();

-- --------------------------------------------------------------- functions

-- The caller's own key, locked; AG404 when missing, foreign, or the caller is
-- no longer a member of its workspace.
create or replace function agos.byok_own_key(p_id uuid) returns agos.provider_keys
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
  r   agos.provider_keys;
begin
  select * into r from agos.provider_keys k where k.id = p_id and k.user_id = uid for update;
  if not found or agos.member_role(r.workspace_id) is null then
    raise exception 'key not found' using errcode = 'AG404';
  end if;
  return r;
end $$;

create or replace function agos.byok_put_key(
  p_id uuid, ws uuid, p_provider text, p_label text, p_base_url text, p_last4 text,
  p_key_ct bytea, p_key_nonce bytea, p_dek_wrapped bytea, p_dek_nonce bytea, p_kek_id text)
returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
begin
  if agos.member_role(ws) is null then
    raise exception 'workspace not found' using errcode = 'AG404';
  end if;
  perform 1 from agos.workspace_members m where m.workspace_id = ws and m.user_id = uid for update;
  if (select count(*) from agos.provider_keys k where k.workspace_id = ws and k.user_id = uid) >= 20 then
    raise exception 'too many keys in this workspace (limit 20)' using errcode = 'AG409';
  end if;
  insert into agos.provider_keys (id, workspace_id, user_id, provider, label, base_url, last4,
                                  key_ct, key_nonce, dek_wrapped, dek_nonce, kek_id)
  values (p_id, ws, uid, p_provider, btrim(p_label), p_base_url, p_last4,
          p_key_ct, p_key_nonce, p_dek_wrapped, p_dek_nonce, p_kek_id);
end $$;

-- Edit: label and/or URL, and/or replace the secret (p_replace_secret).
create or replace function agos.byok_update_key(
  p_id uuid, p_label text, p_base_url text, p_replace_secret boolean, p_last4 text,
  p_key_ct bytea, p_key_nonce bytea, p_dek_wrapped bytea, p_dek_nonce bytea, p_kek_id text)
returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  if r.status <> 'active' then
    raise exception 'key is revoked' using errcode = 'AG410';
  end if;
  update agos.provider_keys k set
    label    = coalesce(btrim(p_label), k.label),
    base_url = case when k.provider = 'ollama' then coalesce(p_base_url, k.base_url) else null end
  where k.id = p_id;
  if p_replace_secret then
    update agos.provider_keys k set
      last4 = p_last4, key_ct = p_key_ct, key_nonce = p_key_nonce, dek_wrapped = p_dek_wrapped,
      dek_nonce = p_dek_nonce, kek_id = p_kek_id,
      last_test_at = null, last_test_ok = null, last_test_reason = null
    where k.id = p_id;
  end if;
end $$;

create or replace function agos.byok_revoke_key(p_id uuid) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  update agos.provider_keys k set status = 'revoked', revoked_at = coalesce(k.revoked_at, now()),
    key_ct = null, key_nonce = null, dek_wrapped = null, dek_nonce = null, kek_id = null
  where k.id = r.id;
end $$;

create or replace function agos.byok_delete_key(p_id uuid) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  delete from agos.provider_keys k where k.id = r.id;
end $$;

-- The ONLY way to the encrypted material. p_purpose 'use' counts a use.
create or replace function agos.byok_key_material(p_id uuid, p_purpose text)
returns table (id uuid, workspace_id uuid, user_id uuid, provider text, base_url text,
               key_ct bytea, key_nonce bytea, dek_wrapped bytea, dek_nonce bytea, kek_id text)
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  if p_purpose is null or p_purpose not in ('test', 'use') then
    raise exception 'invalid purpose' using errcode = 'AG422';
  end if;
  if r.status <> 'active' then
    raise exception 'key is revoked' using errcode = 'AG410';
  end if;
  if p_purpose = 'use' then
    update agos.provider_keys k set use_count = k.use_count + 1, last_used_at = now() where k.id = r.id;
  end if;
  return query select r.id, r.workspace_id, r.user_id, r.provider, r.base_url,
                      r.key_ct, r.key_nonce, r.dek_wrapped, r.dek_nonce, r.kek_id;
end $$;

create or replace function agos.byok_record_test(p_id uuid, p_ok boolean, p_reason text) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  if r.status <> 'active' then
    raise exception 'key is revoked' using errcode = 'AG410';
  end if;
  update agos.provider_keys k set last_test_at = now(), last_test_ok = p_ok, last_test_reason = p_reason
  where k.id = r.id;
end $$;

-- Usage/cost recording for the model gateway (R-206). No quota, no billing.
create or replace function agos.byok_record_usage(p_id uuid, p_tokens_in bigint, p_tokens_out bigint,
                                                  p_cost_usd numeric) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  r agos.provider_keys := agos.byok_own_key(p_id);
begin
  if r.status <> 'active' then
    raise exception 'key is revoked' using errcode = 'AG410';
  end if;
  if coalesce(p_tokens_in, -1) < 0 or coalesce(p_tokens_out, -1) < 0 or coalesce(p_cost_usd, -1) < 0 then
    raise exception 'invalid usage' using errcode = 'AG422';
  end if;
  update agos.provider_keys k set tokens_in = k.tokens_in + p_tokens_in,
    tokens_out = k.tokens_out + p_tokens_out, cost_usd = k.cost_usd + p_cost_usd
  where k.id = r.id;
end $$;

-- ------------------------------------------------------------------ grants
revoke all on agos.provider_keys from public, anon, authenticated;
grant select (id, workspace_id, user_id, provider, label, base_url, status, last4, created_at,
              updated_at, revoked_at, last_test_at, last_test_ok, last_test_reason, use_count,
              last_used_at, tokens_in, tokens_out, cost_usd)
  on agos.provider_keys to authenticated;
grant select, insert, update, delete on agos.provider_keys to service_role;

revoke execute on function agos.byok_drop_member_keys(), agos.byok_own_key(uuid),
  agos.byok_put_key(uuid, uuid, text, text, text, text, bytea, bytea, bytea, bytea, text),
  agos.byok_update_key(uuid, text, text, boolean, text, bytea, bytea, bytea, bytea, text),
  agos.byok_revoke_key(uuid), agos.byok_delete_key(uuid), agos.byok_key_material(uuid, text),
  agos.byok_record_test(uuid, boolean, text), agos.byok_record_usage(uuid, bigint, bigint, numeric)
  from public;
grant execute on function
  agos.byok_put_key(uuid, uuid, text, text, text, text, bytea, bytea, bytea, bytea, text),
  agos.byok_update_key(uuid, text, text, boolean, text, bytea, bytea, bytea, bytea, text),
  agos.byok_revoke_key(uuid), agos.byok_delete_key(uuid), agos.byok_key_material(uuid, text),
  agos.byok_record_test(uuid, boolean, text), agos.byok_record_usage(uuid, bigint, bigint, numeric)
  to authenticated, service_role;
