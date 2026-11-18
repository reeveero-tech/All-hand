"""R-206 (D-163, PROPOSED): migration 007 — the call log outlives a deleted
workspace, and billed failures count on the key's totals.

Real Supabase Auth (GoTrue) + PostgreSQL with migrations 001-007 + server.py's
/api/v2 routes + the BYOK vault + a loopback fake provider. FAKE keys and a
FAKE provider only: this proves the schema and the server's wiring on a TEST
database. It says nothing about production, where 007 is NOT applied.
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.test_r204_accounts_live import (ADMIN_URI, GOTRUE_BIN, SQL1, SQL2, SQL3, STUB,  # noqa: E402
                                           _uri, live_stack, new_ws, register, sql)
from tests.test_r205_byok_live import add, fake_key  # noqa: E402
from tests.test_r206_gateway_live import ROOT, SQL4, SQL5, SQL6, SQL6_RB  # noqa: E402

SQL7 = ROOT / "docs" / "sql" / "v2" / "007_model_calls_retention.sql"
SQL7_RB = ROOT / "docs" / "sql" / "v2" / "007_model_calls_retention_rollback.sql"
DB = "agos_v2_r206_007_test"
ANSWER = "ANSWER-" + uuid.uuid4().hex

pytestmark = pytest.mark.skipif(not (ADMIN_URI and GOTRUE_BIN),
                                reason="needs GOTRUE_BIN and V2_SCHEMA_ADMIN_URI (CI job storage)")


class Provider:
    """OpenAI-compatible loopback: a registered key answers; a prompt with
    EMPTY-ANSWER spends its tokens and writes nothing (finish_reason length)."""

    def __init__(self):
        self.valid = set()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                key = (self.headers.get("Authorization") or "")[7:]
                if key not in outer.valid:
                    out, code = {"error": "bad key"}, 401
                else:
                    empty = "EMPTY-ANSWER" in json.dumps(body.get("messages"))
                    out, code = {"id": f"gen-{uuid.uuid4().hex}", "choices": [
                        {"message": {"role": "assistant", "content": "" if empty else ANSWER},
                         "finish_reason": "length" if empty else "stop"}],
                        "usage": {"prompt_tokens": 13, "completion_tokens": 16 if empty else 5}}, 200
                raw = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def st():
    from agos_v2.byok import KeyVault
    from agos_v2.gateway import ADAPTERS, Gateway
    prov = Provider()
    gateway = Gateway(endpoints={p: f"{prov.url}/v1" for p in ADAPTERS if p != "ollama"},
                      allow_private=True, sleep=lambda s: None)
    with live_stack(DB, (STUB, SQL1, SQL2, SQL3, SQL4, SQL5, SQL6, SQL7),
                    {"vault": KeyVault(os.urandom(32)), "gateway": gateway}) as s:
        s.prov = prov
        yield s
    prov.srv.shutdown()


def user_with_key(st, tag):
    u = register(st, tag)
    ws = new_ws(st, u)
    k = fake_key(tag)
    st.prov.valid.add(k)
    kid = add(st, u, ws, "openai", k, tag).json()["id"]
    return u, ws, kid


def call(st, u, ws, prompt="hello"):
    return st.client.post(f"/api/v2/workspaces/{ws}/model-calls", headers=u.h, json={
        "messages": [{"role": "user", "content": prompt}], "routes": [{"provider": "openai", "model": "m"}]})


def totals(st, kid):
    return sql(st, "select tokens_in, tokens_out from agos.provider_keys where id = %s", (kid,))[0]


# ================================================================ finding 2: retention

def test_deleting_a_workspace_keeps_its_call_rows_without_a_workspace(st):
    u, ws, _ = user_with_key(st, "ret")
    r = call(st, u, ws)
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    cid = r.json()["call_id"]
    assert st.client.delete(f"/api/v2/workspaces/{ws}", headers=u.h).status_code == 204
    row = sql(st, "select workspace_id, user_id::text, key_id, ok, tokens_in, provider_request_id "
                  "from agos.model_calls where id = %s", (cid,))
    assert len(row) == 1, "006 behaviour: the row went with the workspace"
    ws_id, uid, key_id, ok, tin, req = row[0]
    assert ws_id is None and uid == u.id and ok is True and tin == 13 and req.startswith("gen-")
    assert key_id is None                     # the workspace's keys went with it (004) -> set null (006)


def test_a_row_without_a_workspace_is_visible_to_no_user(st):
    u, ws, _ = user_with_key(st, "vis")
    cid = call(st, u, ws).json()["call_id"]
    assert st.client.delete(f"/api/v2/workspaces/{ws}", headers=u.h).status_code == 204
    # as the very user who made the call, through RLS (role authenticated + their JWT claims)
    sid = sql(st, "select id::text from auth.sessions where user_id = %s order by created_at desc limit 1",
              (u.id,))[0][0]
    with psycopg.connect(st.db, autocommit=False) as c:
        c.execute("set local role authenticated")
        c.execute("select set_config('request.jwt.claims', %s, true)",
                  (json.dumps({"sub": u.id, "session_id": sid, "role": "authenticated"}),))
        assert c.execute("select count(*) from agos.model_calls where id = %s", (cid,)).fetchone()[0] == 0
        assert c.execute("select count(*) from agos.model_calls").fetchone()[0] == 0
    # and through the API, in a new workspace of theirs
    ws2 = new_ws(st, u, "after")
    listed = st.client.get(f"/api/v2/workspaces/{ws2}/model-calls", headers=u.h).json()
    assert cid not in json.dumps(listed)
    # the operator (service_role / owner) still sees it, for bill reconciliation
    assert sql(st, "select count(*) from agos.model_calls where id = %s", (cid,)) == [(1,)]


def test_deleting_the_user_still_erases_every_row_including_kept_ones(st):
    u, ws, _ = user_with_key(st, "erase")
    call(st, u, ws)
    assert st.client.delete(f"/api/v2/workspaces/{ws}", headers=u.h).status_code == 204
    ws2 = new_ws(st, u, "second")
    st.prov.valid.add(k := fake_key("erase2"))
    add(st, u, ws2, "openai", k, "e2")
    call(st, u, ws2)
    assert sql(st, "select count(*) from agos.model_calls where user_id = %s", (u.id,)) == [(2,)]
    sql(st, "delete from auth.users where id = %s", (u.id,))
    assert sql(st, "select count(*) from agos.model_calls where user_id = %s", (u.id,)) == [(0,)]


def test_a_new_row_still_needs_a_workspace_the_caller_belongs_to(st):
    u, ws, _ = user_with_key(st, "need")
    other, ows, _ = user_with_key(st, "needo")
    r = call(st, u, ows)                       # not a member there
    assert r.status_code in (403, 404), r.text
    assert sql(st, "select count(*) from agos.model_calls where workspace_id = %s", (ows,)) == [(0,)]


# ================================================================ billed failures count on the key

def test_a_billed_empty_answer_adds_its_tokens_to_the_key(st):
    u, ws, kid = user_with_key(st, "bill")
    before = totals(st, kid)
    r = call(st, u, ws, "EMPTY-ANSWER please")
    out = r.json()
    assert out["ok"] is False and out["reason"] == "empty_output", out
    assert (out["tokens_in"], out["tokens_out"]) == (13, 16)
    assert totals(st, kid) == (before[0] + 13, before[1] + 16)
    ok = call(st, u, ws).json()
    assert ok["ok"] is True and totals(st, kid) == (before[0] + 26, before[1] + 21)


def test_a_failure_with_no_usage_adds_nothing(st):
    u, ws, kid = user_with_key(st, "nousage")
    before = totals(st, kid)
    st.prov.valid.discard(next(k for k in list(st.prov.valid) if "nousage" in k))   # provider now says 401
    out = call(st, u, ws).json()
    assert out["ok"] is False and out.get("tokens_in") is None
    assert totals(st, kid) == before


# ================================================================ schema, grants, rollback

def _fresh(name):
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {name} with (force)")
        c.execute(f"create database {name}")


def _fk(c):
    return c.execute("""select confdeltype, a.attnotnull from pg_constraint k
                         join pg_attribute a on a.attrelid = k.conrelid and a.attname = 'workspace_id'
                         where k.conname = 'model_calls_workspace_id_fkey'""").fetchone()


def _fn(c):
    return c.execute("""select p.prosrc, p.prosecdef, p.proconfig from pg_proc p join pg_namespace n
                         on n.oid = p.pronamespace where n.nspname = 'agos'
                         and p.proname = 'model_call_record'""").fetchone()


def test_007_rollback_restores_006_and_reapplies():
    name = "agos_v2_r206_007_rollback_test"
    _fresh(name)
    try:
        with psycopg.connect(_uri(name), autocommit=True) as c:
            for f in (STUB, SQL1, SQL2, SQL3, SQL4, SQL5, SQL6):
                c.execute(f.read_text(encoding="utf-8"))
            assert _fk(c) == ("c", True)               # 006: cascade, not null
            fn006 = _fn(c)
            c.execute(SQL7.read_text(encoding="utf-8"))
            assert _fk(c) == ("n", False)              # 007: set null, nullable
            fn007 = _fn(c)
            assert fn007[0] != fn006[0] and fn007[1] is True and any(
                x.startswith("search_path=") for x in fn007[2])
            assert c.execute("select has_function_privilege('anon', 'agos.model_call_record(uuid, uuid, text, "
                             "text, text, boolean, text, integer, text, boolean, jsonb, bigint, bigint, numeric, "
                             "text, text, integer)', 'execute')").fetchone()[0] is False
            assert c.execute("select relrowsecurity from pg_class where oid = 'agos.model_calls'::regclass"
                             ).fetchone()[0] is True
            # a kept row (no workspace) is what the rollback must remove to restore NOT NULL
            uid = uuid.uuid4()
            c.execute("insert into auth.users (id, email) values (%s, 'rb@r206.test')", (uid,))
            c.execute("insert into agos.model_calls (workspace_id, user_id, ok, reason) "
                      "values (null, %s, false, 'empty_output')", (uid,))
            c.execute(SQL7_RB.read_text(encoding="utf-8"))
            assert _fk(c) == ("c", True)
            assert _fn(c) == fn006                       # byte-for-byte the 006 body
            assert c.execute("select count(*) from agos.model_calls where workspace_id is null"
                             ).fetchone()[0] == 0
            c.execute(SQL7.read_text(encoding="utf-8"))  # and 007 applies again
            assert _fk(c) == ("n", False) and _fn(c) == fn007
            # 006's own rollback still works on top of 007 (drops the table and the function)
            c.execute(SQL6_RB.read_text(encoding="utf-8"))
            assert c.execute("select to_regclass('agos.model_calls')").fetchone()[0] is None
    finally:
        with psycopg.connect(_uri("postgres"), autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
