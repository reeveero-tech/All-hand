-- R-206 (D-163): rollback of 007_model_calls_retention.sql — run ONLY with the owner's approval.
-- DATA LOSS: rows kept without a workspace (workspace_id NULL) are DELETED,
-- because 006's NOT NULL cannot hold them. Count them first:
--   select count(*) from agos.model_calls where workspace_id is null;
-- provider_keys totals added by failed calls under 007 are NOT subtracted.
-- Restores 006 exactly: NOT NULL + ON DELETE CASCADE + the 006 function body.
-- Proven by tests/test_r206_007_live.py::test_007_rollback_restores_006_and_reapplies.
delete from agos.model_calls where workspace_id is null;
alter table agos.model_calls drop constraint model_calls_workspace_id_fkey;
alter table agos.model_calls add constraint model_calls_workspace_id_fkey
  foreign key (workspace_id) references agos.workspaces(id) on delete cascade;
alter table agos.model_calls alter column workspace_id set not null;

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
