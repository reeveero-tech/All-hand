-- R-204 (D-153): AGOS v2 accounts, workspace roles, invitations — migration 002.
--
-- Builds on 001_core.sql (R-202). Users are Supabase Auth users (auth.users);
-- the web server verifies the user's access token, then runs every user query
-- in ONE transaction as the role `authenticated` with request.jwt.claims set,
-- so the RLS policies below decide what the user sees and changes — even a bug
-- in the server cannot read another workspace's rows.
--
-- What this migration adds:
--   * workspace roles enforced in the database: owner / admin / member
--     (who may rename, delete, invite, change roles, remove members);
--   * invitations: stored as the SHA-256 of a random token (the token itself is
--     never stored), with expiry, revocation and single use; accepted only by
--     the user whose CONFIRMED auth email equals the invited email;
--   * agos.session_is_active(): the access token's session_id must still exist
--     in auth.sessions (logout deletes it), the user must exist, not be deleted
--     and not banned — so a signed-out access token stops working at once,
--     not only when it expires — checked by the API in every transaction AND
--     by every RLS policy below (so direct database access with a dead
--     session's claims sees nothing either);
--   * membership and workspace changes ONLY through SECURITY DEFINER functions
--     that check the caller's role and serialize per workspace (row lock), so
--     the last owner can never be removed or demoted, even concurrently;
--   * workspace_id can never be changed on an existing row (no moving data
--     between workspaces), and created_by is always the caller.
--
-- Error codes raised by the functions (mapped to HTTP by agos_v2/accounts_api.py):
--   AG401 no active session · AG403 role not allowed · AG404 not found / not a
--   member (deliberately the same answer: no workspace enumeration) ·
--   AG409 conflict · AG410 invitation expired or revoked · AG422 invalid input.
--
-- Requirements: 001_core.sql applied; Supabase `auth.users`, `auth.sessions`,
-- `auth.uid()` (on a plain PostgreSQL TEST database: scripts/v2/supabase_auth_stub.sql).

-- ------------------------------------------------------------- workspaces

alter table agos.workspaces add column if not exists created_by uuid;
alter table agos.workspaces add column if not exists updated_at timestamptz not null default now();
create trigger trg_workspaces_touch before update on agos.workspaces
  for each row execute function agos.touch_updated_at();

-- ------------------------------------------------------ caller / session

-- The caller's role in a workspace (NULL = not a member).
create or replace function agos.member_role(ws uuid) returns text
language sql stable security definer set search_path = pg_catalog, pg_temp as $$
  select m.role from agos.workspace_members m
  where m.workspace_id = ws and m.user_id = auth.uid()
$$;

-- The session of the verified access token is still alive (not signed out,
-- not past not_after) and its user exists, is not deleted and not banned.
create or replace function agos.session_is_active() returns boolean
language sql stable security definer set search_path = pg_catalog, pg_temp as $$
  select exists (
    select 1
    from auth.sessions s
    join auth.users u on u.id = s.user_id
    where s.id = nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb
                        ->> 'session_id', '')::uuid
      and s.user_id = auth.uid()
      and (s.not_after is null or s.not_after > now())
      and u.deleted_at is null
      and (u.banned_until is null or u.banned_until <= now())
  )
$$;

create or replace function agos.require_caller() returns uuid
language plpgsql stable security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null or not agos.session_is_active() then
    raise exception 'no active session' using errcode = 'AG401';
  end if;
  return uid;
end $$;

-- ------------------------------------------------- data-row protections

-- No row moves to another workspace (applies to every role, server included).
create or replace function agos.forbid_workspace_change() returns trigger
language plpgsql as $$
begin
  if new.workspace_id is distinct from old.workspace_id then
    raise exception 'agos.%: workspace_id cannot change', tg_table_name
      using errcode = 'AG422';
  end if;
  return new;
end $$;

-- created_by is the caller when the row comes from a user session; rows
-- written by the server/worker (no auth.uid()) keep what they set.
create or replace function agos.stamp_created_by() returns trigger
language plpgsql as $$
begin
  if auth.uid() is not null then
    new.created_by := auth.uid();
  end if;
  return new;
end $$;

create trigger trg_workspace_members_ws before update on agos.workspace_members
  for each row execute function agos.forbid_workspace_change();
create trigger trg_agent_specs_ws before update on agos.agent_specs
  for each row execute function agos.forbid_workspace_change();
create trigger trg_conversations_ws before update on agos.conversations
  for each row execute function agos.forbid_workspace_change();
create trigger trg_tasks_ws before update on agos.tasks
  for each row execute function agos.forbid_workspace_change();
create trigger trg_steps_ws before update on agos.steps
  for each row execute function agos.forbid_workspace_change();
create trigger trg_messages_ws before update on agos.messages
  for each row execute function agos.forbid_workspace_change();
create trigger trg_artifacts_ws before update on agos.artifacts
  for each row execute function agos.forbid_workspace_change();
create trigger trg_memory_items_ws before update on agos.memory_items
  for each row execute function agos.forbid_workspace_change();

create trigger trg_conversations_created_by before insert on agos.conversations
  for each row execute function agos.stamp_created_by();
create trigger trg_tasks_created_by before insert on agos.tasks
  for each row execute function agos.stamp_created_by();

-- ------------------------------------------------------------ invitations

create table agos.workspace_invitations (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references agos.workspaces(id) on delete cascade,
  email         text not null check (email = lower(btrim(email)) and length(email) <= 320
                                     and email ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
  role          text not null check (role in ('admin', 'member')),
  token_sha256  text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
  invited_by    uuid not null,
  created_at    timestamptz not null default now(),
  expires_at    timestamptz not null,
  accepted_at   timestamptz,
  accepted_by   uuid,
  revoked_at    timestamptz,
  check (expires_at > created_at),
  check ((accepted_at is null) = (accepted_by is null)),
  check (accepted_at is null or revoked_at is null)
);
-- at most one open invitation per email and workspace
create unique index uq_invitations_open on agos.workspace_invitations (workspace_id, email)
  where accepted_at is null and revoked_at is null;
create trigger trg_invitations_ws before update on agos.workspace_invitations
  for each row execute function agos.forbid_workspace_change();

alter table agos.workspace_invitations enable row level security;
create policy invitations_read on agos.workspace_invitations for select to authenticated
  using ((select agos.session_is_active()) and agos.member_role(workspace_id) in ('owner', 'admin'));

-- --------------------------------------------------- workspace functions

create or replace function agos.create_workspace(p_name text) returns uuid
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
  ws  uuid;
begin
  insert into agos.workspaces (name, created_by) values (p_name, uid) returning id into ws;
  insert into agos.workspace_members (workspace_id, user_id, role) values (ws, uid, 'owner');
  return ws;
end $$;

create or replace function agos.rename_workspace(ws uuid, p_name text) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
  my  text;
begin
  perform 1 from agos.workspaces where id = ws for update;
  my := agos.member_role(ws);
  if my is null then raise exception 'workspace not found' using errcode = 'AG404'; end if;
  if my not in ('owner', 'admin') then
    raise exception 'only an owner or admin can rename the workspace' using errcode = 'AG403';
  end if;
  update agos.workspaces set name = p_name where id = ws;
end $$;

create or replace function agos.delete_workspace(ws uuid) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid uuid := agos.require_caller();
  my  text;
begin
  perform 1 from agos.workspaces where id = ws for update;
  my := agos.member_role(ws);
  if my is null then raise exception 'workspace not found' using errcode = 'AG404'; end if;
  if my <> 'owner' then
    raise exception 'only an owner can delete the workspace' using errcode = 'AG403';
  end if;
  delete from agos.workspaces where id = ws;
end $$;

-- ------------------------------------------------------ member functions

create or replace function agos.list_members(ws uuid)
returns table (user_id uuid, email text, role text, created_at timestamptz)
language plpgsql stable security definer set search_path = pg_catalog, pg_temp as $$
begin
  perform agos.require_caller();
  if agos.member_role(ws) is null then
    raise exception 'workspace not found' using errcode = 'AG404';
  end if;
  return query
    select m.user_id, u.email::text, m.role, m.created_at
    from agos.workspace_members m
    left join auth.users u on u.id = m.user_id
    where m.workspace_id = ws
    order by m.created_at, m.user_id;
end $$;

create or replace function agos.set_member_role(ws uuid, target uuid, new_role text) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid  uuid := agos.require_caller();
  my   text;
  cur  text;
begin
  perform 1 from agos.workspaces where id = ws for update;  -- serialize membership changes
  my := agos.member_role(ws);
  if my is null then raise exception 'workspace not found' using errcode = 'AG404'; end if;
  if new_role is null or new_role not in ('owner', 'admin', 'member') then
    raise exception 'invalid role' using errcode = 'AG422';
  end if;
  if my <> 'owner' then
    raise exception 'only an owner can change roles' using errcode = 'AG403';
  end if;
  select m.role into cur from agos.workspace_members m
    where m.workspace_id = ws and m.user_id = target;
  if not found then raise exception 'member not found' using errcode = 'AG404'; end if;
  if cur = 'owner' and new_role <> 'owner'
     and (select count(*) from agos.workspace_members m
          where m.workspace_id = ws and m.role = 'owner') = 1 then
    raise exception 'the last owner cannot be demoted' using errcode = 'AG409';
  end if;
  update agos.workspace_members m set role = new_role
    where m.workspace_id = ws and m.user_id = target;
end $$;

-- owner: anyone; admin: members only; anyone: themselves (leave).
-- The last owner can never be removed.
create or replace function agos.remove_member(ws uuid, target uuid) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid  uuid := agos.require_caller();
  my   text;
  cur  text;
begin
  perform 1 from agos.workspaces where id = ws for update;
  my := agos.member_role(ws);
  if my is null then raise exception 'workspace not found' using errcode = 'AG404'; end if;
  select m.role into cur from agos.workspace_members m
    where m.workspace_id = ws and m.user_id = target;
  if not found then raise exception 'member not found' using errcode = 'AG404'; end if;
  if target <> uid and not (my = 'owner' or (my = 'admin' and cur = 'member')) then
    raise exception 'not allowed to remove this member' using errcode = 'AG403';
  end if;
  if cur = 'owner' and (select count(*) from agos.workspace_members m
                        where m.workspace_id = ws and m.role = 'owner') = 1 then
    raise exception 'the last owner cannot be removed' using errcode = 'AG409';
  end if;
  delete from agos.workspace_members m where m.workspace_id = ws and m.user_id = target;
end $$;

-- -------------------------------------------------- invitation functions

-- owner: invites admin or member; admin: member only. p_token_sha256 = the
-- SHA-256 (hex) of a random token generated by the server; the token goes to
-- the inviter once and is never stored. A new invitation for the same email
-- supersedes (revokes) the open one.
create or replace function agos.create_invitation(ws uuid, p_email text, p_role text,
                                                  p_token_sha256 text, p_ttl interval)
returns table (id uuid, expires_at timestamptz)
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid   uuid := agos.require_caller();
  my    text;
  em    text := lower(btrim(coalesce(p_email, '')));
begin
  perform 1 from agos.workspaces w where w.id = ws for update;
  my := agos.member_role(ws);
  if my is null then raise exception 'workspace not found' using errcode = 'AG404'; end if;
  if p_role is null or p_role not in ('admin', 'member') then
    raise exception 'invalid role' using errcode = 'AG422';
  end if;
  if my = 'member' or (my = 'admin' and p_role <> 'member') then
    raise exception 'not allowed to invite with this role' using errcode = 'AG403';
  end if;
  if p_ttl is null or p_ttl < interval '5 minutes' or p_ttl > interval '30 days' then
    raise exception 'invitation lifetime must be between 5 minutes and 30 days' using errcode = 'AG422';
  end if;
  if exists (select 1 from agos.workspace_members m join auth.users u on u.id = m.user_id
             where m.workspace_id = ws and lower(u.email) = em) then
    raise exception 'already a member' using errcode = 'AG409';
  end if;
  update agos.workspace_invitations i set revoked_at = now()
    where i.workspace_id = ws and i.email = em and i.accepted_at is null and i.revoked_at is null;
  return query
    insert into agos.workspace_invitations as i (workspace_id, email, role, token_sha256,
                                                 invited_by, expires_at)
    values (ws, em, p_role, p_token_sha256, uid, now() + p_ttl)
    returning i.id, i.expires_at;
end $$;

create or replace function agos.revoke_invitation(inv uuid) returns void
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid  uuid := agos.require_caller();
  r    agos.workspace_invitations%rowtype;
  my   text;
begin
  select * into r from agos.workspace_invitations i where i.id = inv for update;
  if not found then raise exception 'invitation not found' using errcode = 'AG404'; end if;
  my := agos.member_role(r.workspace_id);
  if my is null or my = 'member' then
    -- a plain member cannot see invitations: same answer as "does not exist"
    raise exception 'invitation not found' using errcode = 'AG404';
  end if;
  if my = 'admin' and r.role <> 'member' then
    raise exception 'not allowed to revoke this invitation' using errcode = 'AG403';
  end if;
  if r.accepted_at is not null then
    raise exception 'invitation already accepted' using errcode = 'AG409';
  end if;
  if r.revoked_at is null then
    update agos.workspace_invitations i set revoked_at = now() where i.id = inv;
  end if;
end $$;

create or replace function agos.accept_invitation(p_token_sha256 text)
returns table (workspace_id uuid, role text)
language plpgsql volatile security definer set search_path = pg_catalog, pg_temp as $$
declare
  uid  uuid := agos.require_caller();
  em   text;
  r    agos.workspace_invitations%rowtype;
  cur  text;
begin
  select lower(u.email) into em from auth.users u
    where u.id = uid and u.email_confirmed_at is not null;
  if em is null then
    raise exception 'a confirmed email is required to accept an invitation' using errcode = 'AG403';
  end if;
  select * into r from agos.workspace_invitations i
    where i.token_sha256 = p_token_sha256 for update;
  if not found then raise exception 'invitation not found' using errcode = 'AG404'; end if;
  if r.revoked_at is not null then raise exception 'invitation revoked' using errcode = 'AG410'; end if;
  if r.accepted_at is not null then raise exception 'invitation already used' using errcode = 'AG409'; end if;
  if r.expires_at <= now() then raise exception 'invitation expired' using errcode = 'AG410'; end if;
  if r.email <> em then
    raise exception 'this invitation is for another email address' using errcode = 'AG403';
  end if;
  perform 1 from agos.workspaces w where w.id = r.workspace_id for update;
  select m.role into cur from agos.workspace_members m
    where m.workspace_id = r.workspace_id and m.user_id = uid;
  if not found then
    insert into agos.workspace_members (workspace_id, user_id, role)
      values (r.workspace_id, uid, r.role);
    cur := r.role;
  end if;
  update agos.workspace_invitations i set accepted_at = now(), accepted_by = uid
    where i.id = r.id;
  return query select r.workspace_id, cur;
end $$;

-- ------------------------------------------- RLS: live session required
-- 001's policies check membership only. Every user policy now also needs a
-- live session. `(select ...)` makes PostgreSQL evaluate the check once per
-- statement (an InitPlan), not once per row.

alter policy ws_read on agos.workspaces
  using ((select agos.session_is_active()) and agos.is_member(id));
alter policy members_read on agos.workspace_members
  using ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy specs_read on agos.agent_specs
  using ((select agos.session_is_active()) and (workspace_id is null or agos.is_member(workspace_id)));
alter policy specs_write on agos.agent_specs
  using ((select agos.session_is_active()) and workspace_id is not null and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and workspace_id is not null and agos.is_member(workspace_id));
alter policy conversations_rw on agos.conversations
  using ((select agos.session_is_active()) and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy tasks_rw on agos.tasks
  using ((select agos.session_is_active()) and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy steps_rw on agos.steps
  using ((select agos.session_is_active()) and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy messages_rw on agos.messages
  using ((select agos.session_is_active()) and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy artifacts_rw on agos.artifacts
  using ((select agos.session_is_active()) and agos.is_member(workspace_id))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy events_read on agos.task_events
  using ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy events_insert on agos.task_events
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id));
alter policy memory_rw on agos.memory_items
  using ((select agos.session_is_active()) and agos.is_member(workspace_id)
         and (user_id is null or user_id = auth.uid()))
  with check ((select agos.session_is_active()) and agos.is_member(workspace_id)
              and (user_id is null or user_id = auth.uid()));

-- ------------------------------------------------------------------ grants
-- Least privilege: users never write workspaces, members or invitations
-- directly — only through the functions above. The token hash is not
-- readable by users at all.

revoke insert, update, delete on agos.workspaces from authenticated;
revoke insert, update, delete on agos.workspace_members from authenticated;
revoke all on agos.workspace_invitations from public, anon, authenticated;
grant select (id, workspace_id, email, role, invited_by, created_at, expires_at,
              accepted_at, accepted_by, revoked_at)
  on agos.workspace_invitations to authenticated;
grant select, insert, update, delete on agos.workspace_invitations to service_role;

revoke execute on function agos.member_role(uuid), agos.session_is_active(), agos.require_caller(),
  agos.forbid_workspace_change(), agos.stamp_created_by(),
  agos.create_workspace(text), agos.rename_workspace(uuid, text), agos.delete_workspace(uuid),
  agos.list_members(uuid), agos.set_member_role(uuid, uuid, text), agos.remove_member(uuid, uuid),
  agos.create_invitation(uuid, text, text, text, interval), agos.revoke_invitation(uuid),
  agos.accept_invitation(text)
  from public;
grant execute on function agos.member_role(uuid), agos.session_is_active(), agos.require_caller(),
  agos.create_workspace(text), agos.rename_workspace(uuid, text), agos.delete_workspace(uuid),
  agos.list_members(uuid), agos.set_member_role(uuid, uuid, text), agos.remove_member(uuid, uuid),
  agos.create_invitation(uuid, text, text, text, interval), agos.revoke_invitation(uuid),
  agos.accept_invitation(text)
  to authenticated, service_role;
