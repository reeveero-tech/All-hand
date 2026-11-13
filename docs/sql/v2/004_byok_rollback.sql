-- R-205 (D-154): rollback of 004_byok.sql — run ONLY with the owner's approval.
-- It removes the BYOK table and functions; every stored provider key
-- (ciphertext) is destroyed with it — by design, there is no copy elsewhere.
-- Workspaces, members, invitations (001-003) are untouched.
-- Proven by tests/test_r205_byok_live.py::test_rollback_is_clean_and_004_reapplies.
drop trigger if exists trg_members_drop_keys on agos.workspace_members;
drop function if exists
  agos.byok_record_usage(uuid, bigint, bigint, numeric),
  agos.byok_record_test(uuid, boolean, text),
  agos.byok_key_material(uuid, text),
  agos.byok_delete_key(uuid),
  agos.byok_revoke_key(uuid),
  agos.byok_update_key(uuid, text, text, boolean, text, bytea, bytea, bytea, bytea, text),
  agos.byok_put_key(uuid, uuid, text, text, text, text, bytea, bytea, bytea, bytea, text),
  agos.byok_own_key(uuid);
drop table if exists agos.provider_keys;
drop function if exists agos.byok_drop_member_keys();
