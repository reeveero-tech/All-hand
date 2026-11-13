"""R-204 (D-153): workspace isolation, roles, invitations and sessions — proven
in the DATABASE itself (docs/sql/v2/001_core.sql + 002_accounts.sql + 003).

Live only (``V2_SCHEMA_ADMIN_URI`` = PostgreSQL superuser URI with pgvector;
CI job ``storage``). A fresh database is created; users are rows of the
``auth.users`` / ``auth.sessions`` stub (scripts/v2/supabase_auth_stub.sql).
Each "as user X" block is exactly what the web server does per request
(agos_v2/db.py): one transaction, request.jwt.claims set locally, role
``authenticated`` — so RLS and the role checks are what is being tested.

The same rules are tested end-to-end through the HTTP API with the real
Supabase Auth server in tests/test_r204_accounts_live.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
import uuid
from contextlib import contextmanager

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = pathlib.Path(__file__).resolve().parent.parent
STUB = ROOT / "scripts" / "v2" / "supabase_auth_stub.sql"
SQL1 = ROOT / "docs" / "sql" / "v2" / "001_core.sql"
SQL2 = ROOT / "docs" / "sql" / "v2" / "002_accounts.sql"
SQL3 = ROOT / "docs" / "sql" / "v2" / "003_function_search_path.sql"
# R-205: 004 is applied too, so the schema-wide checks below (search_path,
# anon access) also cover the BYOK functions and table.
SQL4 = ROOT / "docs" / "sql" / "v2" / "004_byok.sql"
ADMIN_URI = os.environ.get("V2_SCHEMA_ADMIN_URI", "")
DB = "agos_v2_r204_db_test"

pytestmark = pytest.mark.skipif(not ADMIN_URI, reason="needs V2_SCHEMA_ADMIN_URI (CI job storage)")

DATA_TABLES = ("conversations", "tasks", "steps", "messages", "artifacts", "task_events",
               "memory_items", "agent_specs")


def _uri(db: str) -> str:
    base, _, _ = ADMIN_URI.rpartition("/")
    return f"{base}/{db}"


def h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class U:
    def __init__(self, name: str):
        self.name = name
        self.id = str(uuid.uuid4())
        self.sid = str(uuid.uuid4())
        self.email = f"{name}@r204.test"

    def claims(self, sid=None):
        return {"sub": self.id, "role": "authenticated", "aud": "authenticated",
                "email": self.email, "session_id": sid or self.sid}


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {DB}")
        c.execute(f"create database {DB}")
    with psycopg.connect(_uri(DB), autocommit=True) as c:
        for f in (STUB, SQL1, SQL2, SQL3, SQL4):
            c.execute(f.read_text(encoding="utf-8"))
    yield _uri(DB)
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {DB} with (force)")


def admin(db, sql, params=()):
    with psycopg.connect(db, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else None


def make_user(db, name, confirmed=True) -> U:
    u = U(name)
    admin(db, "insert into auth.users (id, email, email_confirmed_at) "
              "values (%s, %s, case when %s then now() end)", (u.id, u.email, confirmed))
    admin(db, "insert into auth.sessions (id, user_id) values (%s, %s)", (u.sid, u.id))
    return u


@contextmanager
def as_user(db, u: U, sid=None, claims=None):
    """One request of user u: a transaction as role authenticated."""
    with psycopg.connect(db) as c:
        with c.transaction():
            c.execute("select set_config('request.jwt.claims', %s, true)",
                      (json.dumps(claims if claims is not None else u.claims(sid)),))
            c.execute("set local role authenticated")
            yield c


def q(db, u, sql, params=(), **kw):
    with as_user(db, u, **kw) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else cur.rowcount


def refused(db, u, sql, params=(), state=None, **kw):
    with pytest.raises(psycopg.Error) as ei:
        q(db, u, sql, params, **kw)
    if state:
        assert ei.value.sqlstate == state, (ei.value.sqlstate, str(ei.value))
    return ei.value


def new_ws(db, u, name="ws"):
    return str(q(db, u, "select agos.create_workspace(%s)", (name,))[0][0])


def invite(db, u, ws, email, role="member", token=None, ttl="72 hours"):
    token = token or uuid.uuid4().hex + uuid.uuid4().hex
    rows = q(db, u, "select id from agos.create_invitation(%s, %s, %s, %s, %s::interval)",
             (ws, email, role, h(token), ttl))
    return str(rows[0][0]), token


def accept(db, u, token):
    return q(db, u, "select workspace_id, role from agos.accept_invitation(%s)", (h(token),))[0]


def seed(db, ws, owner: U):
    """One row of every data table in workspace ws (server-side insert)."""
    ids = {k: str(uuid.uuid4()) for k in ("conv", "task", "step", "art", "mem", "spec", "msg")}
    admin(db, "insert into agos.conversations (id, workspace_id, created_by, title) values (%s,%s,%s,'c')",
          (ids["conv"], ws, owner.id))
    admin(db, "insert into agos.tasks (id, workspace_id, created_by, title, goal) values (%s,%s,%s,'t','g')",
          (ids["task"], ws, owner.id))
    admin(db, "insert into agos.steps (id, workspace_id, task_id, seq, kind, title) values (%s,%s,%s,0,'plan','s')",
          (ids["step"], ws, ids["task"]))
    admin(db, "insert into agos.messages (id, workspace_id, conversation_id, seq, role, content) "
              "values (%s,%s,%s,0,'user','hello')", (ids["msg"], ws, ids["conv"]))
    admin(db, "insert into agos.artifacts (id, workspace_id, task_id, kind, name, mime_type, inline_content, "
              "size_bytes, sha256) values (%s,%s,%s,'text','a','text/plain','x',1,%s)",
          (ids["art"], ws, ids["task"], "0" * 64))
    admin(db, "insert into agos.task_events (workspace_id, task_id, type) values (%s,%s,'task.created')",
          (ws, ids["task"]))
    admin(db, "insert into agos.memory_items (id, workspace_id, scope, content) values (%s,%s,'workspace','m')",
          (ids["mem"], ws))
    slug = "s" + uuid.uuid4().hex[:8]
    admin(db, "insert into agos.agent_specs (id, workspace_id, slug, version, name, spec) "
              "values (%s,%s,%s,1,'n',%s)", (ids["spec"], ws, slug, json.dumps({"slug": slug, "version": 1})))
    return ids


# ======================================================= isolation A vs B

def test_user_sees_only_own_workspaces_and_members(db):
    a, b = make_user(db, "iso_a"), make_user(db, "iso_b")
    wa, wb = new_ws(db, a, "A"), new_ws(db, b, "B")
    assert [str(r[0]) for r in q(db, a, "select id from agos.workspaces")] == [wa]
    assert [str(r[0]) for r in q(db, b, "select id from agos.workspaces")] == [wb]
    assert {str(r[0]) for r in q(db, b, "select workspace_id from agos.workspace_members")} == {wb}
    assert q(db, a, "select role from agos.workspace_members where user_id = %s", (a.id,)) == [("owner",)]


@pytest.mark.parametrize("table", DATA_TABLES)
def test_b_cannot_read_update_or_delete_a_rows(db, table):
    a, b = make_user(db, f"t_{table}_a"), make_user(db, f"t_{table}_b")
    wa, wb = new_ws(db, a), new_ws(db, b)
    seed(db, wa, a)
    seed(db, wb, b)
    assert q(db, a, f"select count(*) from agos.{table} where workspace_id = %s", (wa,))[0][0] == 1
    # B: nothing of A visible, even when asking for A's workspace_id explicitly
    assert q(db, b, f"select count(*) from agos.{table} where workspace_id = %s", (wa,))[0][0] == 0
    assert q(db, b, f"select count(*) from agos.{table}")[0][0] == 1  # only B's own row
    if table != "task_events":  # append-only: UPDATE is refused by trigger for everyone
        assert q(db, b, f"update agos.{table} set created_at = created_at where workspace_id = %s", (wa,)) == 0
    assert q(db, b, f"delete from agos.{table} where workspace_id = %s", (wa,)) == 0
    assert admin(db, f"select count(*) from agos.{table} where workspace_id = %s", (wa,))[0][0] == 1


def test_forged_workspace_id_on_insert_is_refused(db):
    a, b = make_user(db, "forge_a"), make_user(db, "forge_b")
    wa, wb = new_ws(db, a), new_ws(db, b)
    ids_a = seed(db, wa, a)
    # B writes a row claiming A's workspace -> RLS with check
    refused(db, b, "insert into agos.tasks (workspace_id, title, goal) values (%s,'x','y')", (wa,), "42501")
    refused(db, b, "insert into agos.conversations (workspace_id) values (%s)", (wa,), "42501")
    refused(db, b, "insert into agos.memory_items (workspace_id, scope, content) values (%s,'workspace','x')",
            (wa,), "42501")
    # B attaches a child of ITS workspace to A's task -> composite FK (task, workspace) fails
    refused(db, b, "insert into agos.steps (workspace_id, task_id, seq, kind, title) values (%s,%s,1,'act','x')",
            (wb, ids_a["task"]), "23503")
    refused(db, b, "insert into agos.messages (workspace_id, conversation_id, seq, role, content) "
                   "values (%s,%s,5,'user','x')", (wb, ids_a["conv"]), "23503")
    assert admin(db, "select count(*) from agos.tasks where workspace_id = %s", (wa,))[0][0] == 1


def test_rows_never_move_between_workspaces(db):
    a = make_user(db, "move_a")
    w1, w2 = new_ws(db, a, "one"), new_ws(db, a, "two")
    t = q(db, a, "insert into agos.tasks (workspace_id, title, goal) values (%s,'x','y') returning id", (w1,))[0][0]
    refused(db, a, "update agos.tasks set workspace_id = %s where id = %s", (w2, t), "AG422")
    with pytest.raises(psycopg.Error) as ei:  # the server/worker cannot either
        admin(db, "update agos.tasks set workspace_id = %s where id = %s", (w2, t))
    assert ei.value.sqlstate == "AG422"


def test_created_by_is_always_the_caller(db):
    a, b = make_user(db, "stamp_a"), make_user(db, "stamp_b")
    wa = new_ws(db, a)
    t = q(db, a, "insert into agos.tasks (workspace_id, created_by, title, goal) values (%s,%s,'x','y') "
                 "returning created_by", (wa, b.id))[0][0]
    assert str(t) == a.id


def test_direct_writes_to_workspaces_members_invitations_are_refused(db):
    a, b = make_user(db, "direct_a"), make_user(db, "direct_b")
    wa = new_ws(db, a)
    refused(db, a, "insert into agos.workspaces (name) values ('x')", (), "42501")
    refused(db, a, "update agos.workspaces set name = 'x' where id = %s", (wa,), "42501")
    refused(db, a, "delete from agos.workspaces where id = %s", (wa,), "42501")
    # self-escalation / adding someone without an invitation
    refused(db, b, "insert into agos.workspace_members (workspace_id, user_id, role) values (%s,%s,'owner')",
            (wa, b.id), "42501")
    refused(db, a, "update agos.workspace_members set role = 'member' where workspace_id = %s", (wa,), "42501")
    refused(db, a, "insert into agos.workspace_invitations (workspace_id, email, role, token_sha256, invited_by, "
                   "expires_at) values (%s,'x@y.z','member',%s,%s, now() + interval '1 day')",
            (wa, "0" * 64, a.id), "42501")
    refused(db, a, "select token_sha256 from agos.workspace_invitations", (), "42501")


def test_non_member_gets_the_same_404_as_a_missing_workspace(db):
    a, b = make_user(db, "probe_a"), make_user(db, "probe_b")
    wa = new_ws(db, a)
    missing = str(uuid.uuid4())
    for ws in (wa, missing):
        refused(db, b, "select agos.rename_workspace(%s, 'x')", (ws,), "AG404")
        refused(db, b, "select agos.delete_workspace(%s)", (ws,), "AG404")
        refused(db, b, "select * from agos.list_members(%s)", (ws,), "AG404")
        refused(db, b, "select agos.set_member_role(%s, %s, 'member')", (ws, a.id), "AG404")
        refused(db, b, "select agos.remove_member(%s, %s)", (ws, a.id), "AG404")
        refused(db, b, "select * from agos.create_invitation(%s,'z@z.z','member',%s,'1 day')",
                (ws, "1" * 64), "AG404")
    assert admin(db, "select name from agos.workspaces where id = %s", (wa,))[0][0] == "ws"


def test_anon_has_no_access(db):
    with psycopg.connect(db) as c:
        with c.transaction():
            c.execute("set local role anon")
            with pytest.raises(psycopg.Error) as ei:
                c.execute("select count(*) from agos.workspaces")
            assert ei.value.sqlstate == "42501"
    # R-205: nor to the BYOK table or any of its functions
    for stmt in ("select count(*) from agos.provider_keys",
                 "select agos.byok_revoke_key(gen_random_uuid())",
                 "select * from agos.byok_key_material(gen_random_uuid(), 'use')"):
        with psycopg.connect(db) as c:
            with c.transaction():
                c.execute("set local role anon")
                with pytest.raises(psycopg.Error) as ei:
                    c.execute(stmt)
                assert ei.value.sqlstate == "42501", stmt


# ================================================================== roles

def _team(db, tag):
    """owner O, admin D, member M in one workspace (through real invitations)."""
    o, d, m = make_user(db, f"{tag}_o"), make_user(db, f"{tag}_d"), make_user(db, f"{tag}_m")
    ws = new_ws(db, o, tag)
    _, t = invite(db, o, ws, d.email, "admin")
    assert accept(db, d, t)[1] == "admin"
    _, t = invite(db, o, ws, m.email, "member")
    assert accept(db, m, t)[1] == "member"
    return ws, o, d, m


def test_member_role_limits(db):
    ws, o, d, m = _team(db, "rm")
    assert len(q(db, m, "select * from agos.list_members(%s)", (ws,))) == 3  # members see the team
    refused(db, m, "select agos.rename_workspace(%s, 'x')", (ws,), "AG403")
    refused(db, m, "select agos.delete_workspace(%s)", (ws,), "AG403")
    refused(db, m, "select * from agos.create_invitation(%s,'n@r204.test','member',%s,'1 day')",
            (ws, "2" * 64), "AG403")
    refused(db, m, "select agos.set_member_role(%s, %s, 'owner')", (ws, m.id), "AG403")  # no self-promotion
    refused(db, m, "select agos.remove_member(%s, %s)", (ws, d.id), "AG403")
    refused(db, m, "select agos.remove_member(%s, %s)", (ws, o.id), "AG403")
    assert q(db, m, "select count(*) from agos.workspace_invitations")[0][0] == 0  # invitations hidden
    # but a member works with the workspace's data
    assert q(db, m, "insert into agos.tasks (workspace_id, title, goal) values (%s,'x','y')", (ws,)) == 1


def test_admin_role_limits(db):
    ws, o, d, m = _team(db, "ra")
    q(db, d, "select agos.rename_workspace(%s, 'renamed')", (ws,))
    refused(db, d, "select agos.delete_workspace(%s)", (ws,), "AG403")
    invite(db, d, ws, "new_member@r204.test", "member")
    refused(db, d, "select * from agos.create_invitation(%s,'x@r204.test','admin',%s,'1 day')",
            (ws, "3" * 64), "AG403")
    refused(db, d, "select agos.set_member_role(%s, %s, 'admin')", (ws, m.id), "AG403")
    refused(db, d, "select agos.remove_member(%s, %s)", (ws, o.id), "AG403")
    assert q(db, d, "select count(*) from agos.workspace_invitations where workspace_id = %s", (ws,))[0][0] >= 1
    q(db, d, "select agos.remove_member(%s, %s)", (ws, m.id))
    assert q(db, m, "select count(*) from agos.workspaces where id = %s", (ws,))[0][0] == 0  # m lost access


def test_owner_powers_and_last_owner_protection(db):
    ws, o, d, m = _team(db, "ro")
    refused(db, o, "select agos.set_member_role(%s, %s, 'member')", (ws, o.id), "AG409")
    refused(db, o, "select agos.remove_member(%s, %s)", (ws, o.id), "AG409")
    refused(db, o, "select agos.set_member_role(%s, %s, 'king')", (ws, m.id), "AG422")
    q(db, o, "select agos.set_member_role(%s, %s, 'owner')", (ws, d.id))
    q(db, o, "select agos.remove_member(%s, %s)", (ws, o.id))  # two owners: O may leave
    refused(db, d, "select agos.remove_member(%s, %s)", (ws, d.id), "AG409")  # now the last owner
    q(db, m, "select agos.remove_member(%s, %s)", (ws, m.id))  # a member may leave
    q(db, d, "select agos.delete_workspace(%s)", (ws,))
    assert admin(db, "select count(*) from agos.workspace_members where workspace_id = %s", (ws,))[0][0] == 0


def test_two_owners_demoting_each_other_concurrently_leave_one_owner(db):
    o1, o2 = make_user(db, "cc_o1"), make_user(db, "cc_o2")
    ws = new_ws(db, o1)
    _, t = invite(db, o1, ws, o2.email, "admin")
    accept(db, o2, t)
    q(db, o1, "select agos.set_member_role(%s, %s, 'owner')", (ws, o2.id))
    result = {}
    c1 = psycopg.connect(db)
    try:
        c1.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(o1.claims()),))
        c1.execute("set local role authenticated")
        c1.execute("select agos.set_member_role(%s, %s, 'member')", (ws, o2.id))  # holds the lock

        def other():
            try:
                q(db, o2, "select agos.set_member_role(%s, %s, 'member')", (ws, o1.id))
                result["o2"] = "ok"
            except psycopg.Error as e:
                result["o2"] = e.sqlstate

        th = threading.Thread(target=other)
        th.start()
        th.join(1.0)
        assert th.is_alive(), "the second change must wait for the first (row lock)"
        c1.commit()
        th.join(10)
    finally:
        c1.close()
    # o2 is no longer an owner when its change runs -> refused; one owner remains
    assert result["o2"] in ("AG403", "AG409")
    owners = admin(db, "select count(*) from agos.workspace_members where workspace_id = %s and role = 'owner'",
                   (ws,))[0][0]
    assert owners == 1


# ============================================================ invitations

def test_invitation_lifecycle_and_refusals(db):
    o, c, x = make_user(db, "inv_o"), make_user(db, "inv_c"), make_user(db, "inv_x")
    unconfirmed = make_user(db, "inv_u", confirmed=False)
    ws = new_ws(db, o)
    _, tok = invite(db, o, ws, c.email.upper(), "member")  # email normalized
    # someone else holding the link cannot use it
    refused(db, x, "select * from agos.accept_invitation(%s)", (h(tok),), "AG403")
    refused(db, unconfirmed, "select * from agos.accept_invitation(%s)", (h(tok),), "AG403")
    refused(db, c, "select * from agos.accept_invitation(%s)", (h("wrong-token"),), "AG404")
    assert accept(db, c, tok)[1] == "member"
    refused(db, c, "select * from agos.accept_invitation(%s)", (h(tok),), "AG409")  # single use
    refused(db, o, "select * from agos.create_invitation(%s,%s,'member',%s,'1 day')",
            (ws, c.email, "4" * 64), "AG409")  # already a member
    for ttl in ("1 minute", "31 days"):
        refused(db, o, "select * from agos.create_invitation(%s,'t@r204.test','member',%s,%s::interval)",
                (ws, "5" * 64, ttl), "AG422")


def test_expired_revoked_and_superseded_invitations(db):
    o, e = make_user(db, "exp_o"), make_user(db, "exp_e")
    ws = new_ws(db, o)
    inv, tok = invite(db, o, ws, e.email)
    admin(db, "update agos.workspace_invitations set created_at = now() - interval '2 days', "
              "expires_at = now() - interval '1 second' where id = %s", (inv,))
    refused(db, e, "select * from agos.accept_invitation(%s)", (h(tok),), "AG410")
    inv2, tok2 = invite(db, o, ws, e.email)
    q(db, o, "select agos.revoke_invitation(%s)", (inv2,))
    refused(db, e, "select * from agos.accept_invitation(%s)", (h(tok2),), "AG410")
    _, tok3 = invite(db, o, ws, e.email)
    _, tok4 = invite(db, o, ws, e.email)  # supersedes tok3
    refused(db, e, "select * from agos.accept_invitation(%s)", (h(tok3),), "AG410")
    assert accept(db, e, tok4)[1] == "member"
    assert q(db, e, "select count(*) from agos.workspaces where id = %s", (ws,))[0][0] == 1


def test_invitations_of_another_workspace_cannot_be_revoked(db):
    a, b = make_user(db, "rv_a"), make_user(db, "rv_b")
    wa = new_ws(db, a)
    new_ws(db, b)
    inv, _ = invite(db, a, wa, "someone@r204.test")
    refused(db, b, "select agos.revoke_invitation(%s)", (inv,), "AG404")
    assert admin(db, "select revoked_at from agos.workspace_invitations where id = %s", (inv,))[0][0] is None


# =============================================================== sessions

def test_a_signed_out_session_is_refused(db):
    u = make_user(db, "sess_out")
    new_ws(db, u)
    assert q(db, u, "select agos.session_is_active()")[0][0] is True
    admin(db, "delete from auth.sessions where id = %s", (u.sid,))  # what GoTrue logout does
    assert q(db, u, "select agos.session_is_active()")[0][0] is False
    refused(db, u, "select agos.create_workspace('x')", (), "AG401")


def test_rls_itself_refuses_a_dead_session_on_every_table(db):
    """Direct database access (not through the API's per-request check): a
    member whose session ended sees and changes nothing (002 RLS policies)."""
    u = make_user(db, "rls_dead")
    ws = new_ws(db, u)
    seed(db, ws, u)
    tables = ("workspaces", "workspace_members") + DATA_TABLES
    for t in tables:
        assert q(db, u, f"select count(*) from agos.{t}")[0][0] >= 1, t
    admin(db, "delete from auth.sessions where id = %s", (u.sid,))
    for t in tables:
        assert q(db, u, f"select count(*) from agos.{t}")[0][0] == 0, t
    for t in DATA_TABLES:
        if t != "task_events":
            assert q(db, u, f"update agos.{t} set workspace_id = workspace_id") == 0, t
    refused(db, u, "insert into agos.tasks (workspace_id, title, goal) values (%s, 't', 'g')", (ws,), "42501")
    # a new login (new session) restores access
    u.sid = str(uuid.uuid4())
    admin(db, "insert into auth.sessions (id, user_id) values (%s, %s)", (u.sid, u.id))
    assert q(db, u, "select count(*) from agos.tasks")[0][0] == 1


def test_expired_banned_deleted_and_foreign_sessions_are_refused(db):
    u, v = make_user(db, "sess_u"), make_user(db, "sess_v")
    admin(db, "update auth.sessions set not_after = now() - interval '1 second' where id = %s", (u.sid,))
    assert q(db, u, "select agos.session_is_active()")[0][0] is False
    admin(db, "update auth.sessions set not_after = null where id = %s", (u.sid,))
    admin(db, "update auth.users set banned_until = now() + interval '1 day' where id = %s", (u.id,))
    assert q(db, u, "select agos.session_is_active()")[0][0] is False
    admin(db, "update auth.users set banned_until = null, deleted_at = now() where id = %s", (u.id,))
    assert q(db, u, "select agos.session_is_active()")[0][0] is False
    # v's token cannot borrow u's session id (session belongs to another user)
    assert q(db, v, "select agos.session_is_active()", sid=u.sid)[0][0] is False
    # no claims at all
    refused(db, v, "select agos.create_workspace('x')", (), "AG401", claims={})


def test_claims_do_not_leak_to_the_next_transaction(db):
    u = make_user(db, "leak_u")
    new_ws(db, u)
    with psycopg.connect(db) as c:
        with c.transaction():
            c.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(u.claims()),))
            c.execute("set local role authenticated")
            assert c.execute("select auth.uid()").fetchone()[0] is not None
        # same connection, next transaction: no identity, superuser role again
        assert c.execute("select auth.uid()").fetchone()[0] is None
        assert c.execute("select current_user").fetchone()[0] != "authenticated"


def test_every_agos_function_pins_its_search_path(db):
    """R-204 live (Supabase advisor 0011 function_search_path_mutable): a function
    whose search_path follows the caller can be steered by objects the caller
    puts earlier in the path. Every function in schema agos must pin it."""
    rows = admin(db, """
        select p.proname
          from pg_proc p join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'agos'
           and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c
                            where c like 'search_path=%%')
         order by 1""")
    assert [r[0] for r in rows] == []


def test_trigger_functions_still_work_with_the_pinned_path(db):
    """The 003 fix must not break the four trigger functions it pins."""
    u = make_user(db, "sp_u")
    ws, other = new_ws(db, u, "sp1"), new_ws(db, u, "sp2")
    # touch_updated_at (rename through the API function, separate transaction)
    q(db, u, "select agos.rename_workspace(%s, 'renamed')", (ws,))
    assert admin(db, "select updated_at > created_at from agos.workspaces where id = %s", (ws,))[0][0] is True
    # forbid_workspace_change — even the superuser cannot move a row between workspaces
    with pytest.raises(psycopg.Error) as ei:
        admin(db, "update agos.workspace_members set workspace_id = %s where workspace_id = %s", (other, ws))
    assert ei.value.sqlstate == "AG422"
    # stamp_created_by — a user-session insert gets created_by = auth.uid()
    conv = str(uuid.uuid4())
    with psycopg.connect(db, autocommit=True) as c:
        with c.transaction():
            c.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(u.claims()),))
            c.execute("insert into agos.conversations (id, workspace_id, created_by, title) "
                      "values (%s, %s, %s, 'c')", (conv, ws, str(uuid.uuid4())))
    assert str(admin(db, "select created_by from agos.conversations where id = %s", (conv,))[0][0]) == u.id
    # forbid_update — task_events stay append-only
    task = str(uuid.uuid4())
    admin(db, "insert into agos.tasks (id, workspace_id, created_by, title, goal) values (%s,%s,%s,'t','g')",
          (task, ws, u.id))
    admin(db, "insert into agos.task_events (workspace_id, task_id, type) values (%s,%s,'task.created')",
          (ws, task))
    with pytest.raises(psycopg.Error) as ei:
        admin(db, "update agos.task_events set type = 'task.failed' where task_id = %s", (task,))
    assert "append-only" in str(ei.value)
