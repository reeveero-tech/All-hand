-- R-206 (D-158): AGOS v2 — one row per model-gateway call — migration 006
--
-- Why: R-206's acceptance needs "the recorded cost matches the provider's
-- bill on a sample", and platform-key calls have no provider_keys row to
-- count on. provider_keys.cost_usd (004) is numeric(14,6), so a single small
-- call (OpenRouter bills 1e-7 USD steps) would be rounded there.
--
-- What a row holds: METADATA ONLY — who, which workspace, which key (or the
-- platform key), the provider/model that answered (or the last one tried),
-- ok + reason code, every attempt (provider, model, key source, reason,
-- status, retries, latency), tokens, cost at full precision with its source
-- (provider-reported | catalog_estimate | NULL = unknown, never 0), and the
-- provider's own request id (to find the provider's bill line).
-- NO prompt, NO answer, NO key material.
--
-- Rules:
--   * the caller writes only through agos.model_call_record (SECURITY
--     DEFINER, pinned search_path): user = auth.uid(), workspace membership
--     checked, the key (if any) must be the caller's own key in that
--     workspace; the call's tokens/cost are added to that key's totals only
--     when the key is still active (a key revoked mid-call keeps its totals).
--   * a member sees only their own calls, only while still a member (RLS);
--     no update/delete grant — the log is append-only for the user.
--   * user deleted -> their calls go (cascade, like their keys, D-155);
--     workspace deleted -> its calls go; key deleted -> the row stays with
--     key_id NULL.
--
-- Requirements: 001-005 applied.
-- Rollback: docs/sql/v2/006_model_calls_rollback.sql.
-- Proven by tests/test_r206_gateway_live.py.

create table agos.model_calls (
  id                  uuid primary key default gen_random_uuid(),
  workspace_id        uuid not null references agos.workspaces(id) on delete cascade,
  user_id             uuid not null references auth.users(id) on delete cascade,
  key_id              uuid references agos.provider_keys(id) on delete set null,
  key_source          text check (key_source is null or key_source in ('byok', 'platform')),
  provider            text check (provider is null or provider in
                        ('openai', 'anthropic', 'google', 'openrouter', 'mistral', 'groq', 'ollama')),
  model               text check (model is null or length(model) between 1 and 200),
  ok                  boolean not null,
  reason              text not null check (reason ~ '^[a-z_]{1,40}$'),
  http_status         integer check (http_status is null or http_status between 100 and 599),
  finish_reason       text check (finish_reason is null or length(finish_reason) <= 40),
  fallback_used       boolean not null default false,
  attempts            jsonb not null default '[]'::jsonb check (jsonb_typeof(attempts) = 'array'
                        and jsonb_array_length(attempts) <= 50),
  tokens_in           bigint check (tokens_in is null or tokens_in >= 0),
  tokens_out          bigint check (tokens_out is null or tokens_out >= 0),
  cost_usd            numeric check (cost_usd is null or cost_usd >= 0),
  cost_source         text check (cost_source is null or cost_source in ('provider', 'catalog_estimate')),
  provider_request_id text check (provider_request_id is null or length(provider_request_id) <= 200),
  latency_ms          integer check (latency_ms is null or latency_ms >= 0),
  created_at          timestamptz not null default now(),
  constraint model_calls_cost_has_source check ((cost_usd is null) = (cost_source is null)),
  constraint model_calls_ok_names_route check (not ok or (provider is not null and model is not null
                                                        and key_source is not null))
);
create index idx_model_calls_owner on agos.model_calls (workspace_id, user_id, created_at desc);
create index idx_model_calls_user on agos.model_calls (user_id);
create index idx_model_calls_key on agos.model_calls (key_id);

alter table agos.model_calls enable row level security;
create policy model_calls_read on agos.model_calls for select to authenticated
  using ((select agos.session_is_active()) and user_id = (select auth.uid())
         and agos.is_member(workspace_id));

create or replace function agos.model_call_record(
  ws uuid, p_key_id uuid, p_key_source text, p_provider text, p_model text, p_ok boolean,
  p_reason text, p_http_status integer, p_finish_reason text, p_fallback_used boolean,
  p_attempts jsonb, p_tokens_in bigint, p_tokens_out bigint, p_cost_usd numeric,
  p_cost_source text, p_provider_request_id text, p_latency_ms integer)
returns uuid
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
  k   agos.provider_keys;
  rid uuid;
begin
  if agos.member_role(ws) is null then
    raise exception 'workspace not found' using errcode = 'AG404';
  end if;
  if p_key_id is not null and p_key_source is distinct from 'byok' then
    raise exception 'only a byok call names a key' using errcode = 'AG422';
  end if;
  if p_key_id is not null then
    select * into k from agos.provider_keys pk
      where pk.id = p_key_id and pk.user_id = uid and pk.workspace_id = ws for update;
    if not found then
      -- deleted while the call ran (or not the caller's): the row keeps no key
      p_key_id := null;
    end if;
  end if;
  insert into agos.model_calls (workspace_id, user_id, key_id, key_source, provider, model, ok, reason,
                                http_status, finish_reason, fallback_used, attempts, tokens_in,
                                tokens_out, cost_usd, cost_source, provider_request_id, latency_ms)
  values (ws, uid, p_key_id, p_key_source, p_provider, p_model, p_ok, p_reason, p_http_status,
          p_finish_reason, coalesce(p_fallback_used, false), coalesce(p_attempts, '[]'::jsonb),
          p_tokens_in, p_tokens_out, p_cost_usd, p_cost_source, p_provider_request_id, p_latency_ms)
  returning id into rid;
  -- the key's running totals (004): known numbers only; an unknown cost adds nothing there
  -- and stays NULL in this row
  if p_key_id is not null and k.status = 'active' and p_ok then
    update agos.provider_keys pk set tokens_in = pk.tokens_in + coalesce(p_tokens_in, 0),
      tokens_out = pk.tokens_out + coalesce(p_tokens_out, 0),
      cost_usd = pk.cost_usd + coalesce(p_cost_usd, 0)
    where pk.id = k.id;
  end if;
  return rid;
end $$;

revoke all on agos.model_calls from public, anon, authenticated;
grant select on agos.model_calls to authenticated;
grant select, insert, update, delete on agos.model_calls to service_role;
revoke execute on function agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer,
  text, boolean, jsonb, bigint, bigint, numeric, text, text, integer) from public;
grant execute on function agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer,
  text, boolean, jsonb, bigint, bigint, numeric, text, text, integer) to authenticated, service_role;
