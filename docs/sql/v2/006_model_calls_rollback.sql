-- R-206 (D-158): rollback of 006_model_calls.sql — run ONLY with the owner's approval.
-- Drops the call log (its rows are lost: metadata only, no prompt/answer/key).
-- provider_keys totals already added by recorded calls are NOT subtracted.
-- 001-005 objects are untouched.
-- Proven by tests/test_r206_gateway_live.py::test_006_rollback_is_clean_and_reapplies.
drop function if exists agos.model_call_record(uuid, uuid, text, text, text, boolean, text, integer,
  text, boolean, jsonb, bigint, bigint, numeric, text, text, integer);
drop table if exists agos.model_calls;
