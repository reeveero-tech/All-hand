-- R-205 (D-155): AGOS v2 — a deleted user leaves nothing behind — migration 005
--
-- Finding (lahiq 166, live): agos.* user columns had NO foreign key to
-- auth.users, so deleting a user (Supabase dashboard or Auth admin API) left
-- their memberships, provider keys (ciphertext), invitations and solo
-- workspaces orphaned.
--
-- After this migration, deleting a row of auth.users:
--   * deletes the user's memberships        workspace_members.user_id   ON DELETE CASCADE
--     (trg_members_drop_keys, 004, then drops their keys in that workspace)
--   * deletes the user's provider keys      provider_keys.user_id       ON DELETE CASCADE
--   * deletes invitations the user sent or accepted  workspace_invitations.invited_by/accepted_by
--     CASCADE (accepted_at/accepted_by are all-or-nothing, so the row cannot keep one without the other)
--   * deletes the user's memories           memory_items.user_id        ON DELETE CASCADE
--   * keeps shared work, without the author workspaces/conversations/tasks.created_by,
--                                           messages.author_id  SET NULL
--   * a workspace left with NO member is deleted (with everything in it);
--     a workspace that lost its LAST OWNER gets one: the earliest admin, else
--     the earliest member (R-204 rule "a workspace always has an owner").
--     trigger trg_members_keep_owner, after delete on workspace_members.
--
-- Existing orphans (users deleted before 005) are cleaned the same way BEFORE
-- the keys are added, so the migration cannot fail on old data.
-- Soft-deleted users (auth.users.deleted_at set, row kept) are not affected:
-- sign-in already refuses them (R-204); their data stays until a hard delete.
--
-- Requirements: 001-004 applied; Supabase `auth.users` (TEST: scripts/v2/supabase_auth_stub.sql).
-- Rollback: docs/sql/v2/005_user_fk_rollback.sql (removes the keys and the trigger; the
-- cleaned orphan rows are not restored — they belonged to users that no longer exist).
-- Proven by tests/test_r205_byok_live.py (test_deleting_a_user_*, test_005_*).

-- --------------------------------------------- keep every workspace owned
create or replace function agos.members_keep_owner() returns trigger
language plpgsql security definer set search_path = pg_catalog, pg_temp as $$
declare
  heir uuid;
begin
  if old.role <> 'owner'
     and exists (select 1 from agos.workspace_members m where m.workspace_id = old.workspace_id) then
    return old;
  end if;
  if not exists (select 1 from agos.workspaces w where w.id = old.workspace_id) then
    return old;                                  -- the workspace itself is being deleted
  end if;
  if not exists (select 1 from agos.workspace_members m where m.workspace_id = old.workspace_id) then
    delete from agos.workspaces w where w.id = old.workspace_id;
  elsif not exists (select 1 from agos.workspace_members m
                     where m.workspace_id = old.workspace_id and m.role = 'owner') then
    select m.user_id into heir from agos.workspace_members m
     where m.workspace_id = old.workspace_id
     order by (m.role = 'admin') desc, m.created_at, m.user_id
     limit 1;
    update agos.workspace_members m set role = 'owner'
     where m.workspace_id = old.workspace_id and m.user_id = heir;
  end if;
  return old;
end $$;
revoke execute on function agos.members_keep_owner() from public;

drop trigger if exists trg_members_keep_owner on agos.workspace_members;
create trigger trg_members_keep_owner after delete on agos.workspace_members
  for each row execute function agos.members_keep_owner();

-- ------------------------------------------------- clean existing orphans
delete from agos.workspace_members m
 where not exists (select 1 from auth.users u where u.id = m.user_id);
delete from agos.provider_keys k
 where not exists (select 1 from auth.users u where u.id = k.user_id);
delete from agos.workspace_invitations i
 where not exists (select 1 from auth.users u where u.id = i.invited_by);
delete from agos.workspace_invitations i
 where i.accepted_by is not null and not exists (select 1 from auth.users u where u.id = i.accepted_by);
delete from agos.memory_items x
 where x.user_id is not null and not exists (select 1 from auth.users u where u.id = x.user_id);
update agos.workspaces t set created_by = null
 where t.created_by is not null and not exists (select 1 from auth.users u where u.id = t.created_by);
update agos.conversations t set created_by = null
 where t.created_by is not null and not exists (select 1 from auth.users u where u.id = t.created_by);
update agos.tasks t set created_by = null
 where t.created_by is not null and not exists (select 1 from auth.users u where u.id = t.created_by);
update agos.messages t set author_id = null
 where t.author_id is not null and not exists (select 1 from auth.users u where u.id = t.author_id);
-- a workspace with no member at all (never reachable again)
delete from agos.workspaces w
 where not exists (select 1 from agos.workspace_members m where m.workspace_id = w.id);

-- -------------------------------------------------------- the foreign keys
alter table agos.workspace_members
  add constraint workspace_members_user_fk foreign key (user_id) references auth.users(id) on delete cascade;
alter table agos.provider_keys
  add constraint provider_keys_user_fk foreign key (user_id) references auth.users(id) on delete cascade;
alter table agos.workspace_invitations
  add constraint workspace_invitations_invited_by_fk foreign key (invited_by) references auth.users(id) on delete cascade,
  add constraint workspace_invitations_accepted_by_fk foreign key (accepted_by) references auth.users(id) on delete cascade;
alter table agos.memory_items
  add constraint memory_items_user_fk foreign key (user_id) references auth.users(id) on delete cascade;
alter table agos.workspaces
  add constraint workspaces_created_by_fk foreign key (created_by) references auth.users(id) on delete set null;
alter table agos.conversations
  add constraint conversations_created_by_fk foreign key (created_by) references auth.users(id) on delete set null;
alter table agos.tasks
  add constraint tasks_created_by_fk foreign key (created_by) references auth.users(id) on delete set null;
alter table agos.messages
  add constraint messages_author_fk foreign key (author_id) references auth.users(id) on delete set null;

create index if not exists idx_provider_keys_user on agos.provider_keys (user_id);
create index if not exists idx_invitations_invited_by on agos.workspace_invitations (invited_by);
