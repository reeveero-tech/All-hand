-- R-205 (D-155): rollback of 005_user_fk.sql — run ONLY with the owner's approval.
-- Removes the nine foreign keys to auth.users, the owner-keeping trigger and
-- the two indexes. Orphan rows cleaned by 005 are NOT restored (their users no
-- longer exist). 001-004 objects are untouched.
-- Proven by tests/test_r205_byok_live.py::test_005_removes_existing_orphans_then_its_rollback_is_clean.
alter table agos.messages              drop constraint if exists messages_author_fk;
alter table agos.tasks                 drop constraint if exists tasks_created_by_fk;
alter table agos.conversations         drop constraint if exists conversations_created_by_fk;
alter table agos.workspaces            drop constraint if exists workspaces_created_by_fk;
alter table agos.memory_items          drop constraint if exists memory_items_user_fk;
alter table agos.workspace_invitations drop constraint if exists workspace_invitations_accepted_by_fk;
alter table agos.workspace_invitations drop constraint if exists workspace_invitations_invited_by_fk;
alter table agos.provider_keys         drop constraint if exists provider_keys_user_fk;
alter table agos.workspace_members     drop constraint if exists workspace_members_user_fk;
drop index if exists agos.idx_invitations_invited_by;
drop index if exists agos.idx_provider_keys_user;
drop trigger if exists trg_members_keep_owner on agos.workspace_members;
drop function if exists agos.members_keep_owner();
