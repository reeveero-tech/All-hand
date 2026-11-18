-- R-206 (D-163, PROPOSED — owner decision pending): migration 007 — the call
-- log outlives a deleted workspace, and billed failures count on the key.
--
-- DRAFT: do NOT apply to any Supabase project without the owner's explicit
-- approval (same rule as 004-006). Proven on a TEST database only by
-- tests/test_r206_007_live.py.
--
-- Why (M10/028 finding 2, confirmed on production with read-only queries in
-- M10/030): with 006, deleting a workspace cascades to agos.model_calls, so
-- the record needed to match the provider's bill (R-206 acceptance: "the
-- recorded cost matches the provider's bill on a sample") disappears with it.
--
-- What changes (option B in M10/030 §D):
--   1. model_calls.workspace_id becomes NULLable and its foreign key becomes
--      ON DELETE SET NULL: the row stays, with no workspace.
--      * The member read policy (006) needs agos.is_member(workspace_id),
--        which is false for NULL: a row without a workspace is visible to
--        NO user — only to service_role (operator reconciliation).
--      * User deletion is unchanged: user deleted -> their calls go
--        (cascade, D-155). The privacy right to erasure stays whole.
--      * The row is metadata only (no prompt, no answer, no key) — 006.
--   2. agos.model_call_record adds known tokens/cost to the key's totals
--      whenever the key is still active — also for a FAILED call that the
--      provider billed (empty_output, an interrupted stream with usage).
--      006 added them only when ok, so billed failures were missing from
--      provider_keys totals (declared limit in M10/030). Unknown numbers
--      (NULL) still add nothing.
--
-- Effect on existing rows: none (no row is rewritten). On 2026-11-18 the
-- production table had 0 rows (M10/030).
-- Requirements: 001-006 applied.
-- Rollback: docs/sql/v2/007_model_calls_retention_rollback.sql.

alter table agos.model_calls alter column workspace_id drop not null;
alter table agos.model_calls drop constraint model_calls_workspace_id_fkey;
alter table agos.model_calls add constraint model_calls_workspace_id_fkey
  foreign key (workspace_id) references agos.workspaces(id) on delete set null;

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
  -- the key's running totals (004): known numbers only, ok or not (007: a billed
  -- failure is still billed); an unknown cost adds nothing there and stays NULL here
  if p_key_id is not null and k.status = 'active'
     and (p_tokens_in is not null or p_tokens_out is not null or p_cost_usd is not null) then
    update agos.provider_keys pk set tokens_in = pk.tokens_in + coalesce(p_tokens_in, 0),
      tokens_out = pk.tokens_out + coalesce(p_tokens_out, 0),
      cost_usd = pk.cost_usd + coalesce(p_cost_usd, 0)
    where pk.id = k.id;
  end if;
  return rid;
end $$;

-- create or replace keeps 006's grants; restated so the file is self-evident
revoke execute on function agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer,
  text, boolean, jsonb, bigint, bigint, numeric, text, text, integer) from public;
grant execute on function agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer,
  text, boolean, jsonb, bigint, bigint, numeric, text, text, integer) to authenticated, service_role;
