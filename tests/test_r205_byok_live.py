"""R-205 (D-154): BYOK end-to-end — real Supabase Auth (GoTrue) + PostgreSQL with
migrations 001-004 + server.py's /api/v2 routes + a loopback fake provider.

FAKE keys only; no real provider is contacted and no credit is spent. What
this does NOT prove: the hosted Supabase project (004 is not applied live
until the owner approves) and a real provider (needs an owner-supplied key —
the live check is prepared, not run).

Leak surfaces covered: every API response (success AND error, headers too),
every log record of every logger, every parameter the server sends to
PostgreSQL, every row of every table in every schema (what a logical backup
contains), the diagnostics endpoint, and the provider's own error bodies.
"""
from __future__ import annotations

import base64
import http.server
import json
import logging
import os
import pathlib
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.test_r204_accounts_live import (ADMIN_URI, GOTRUE_BIN, SQL1, SQL2, SQL3, STUB,  # noqa: E402
                                           accept, invite, live_stack, new_ws, register, sql)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL4 = ROOT / "docs" / "sql" / "v2" / "004_byok.sql"
DB = "agos_v2_r205_byok_test"

pytestmark = pytest.mark.skipif(not (ADMIN_URI and GOTRUE_BIN),
                                reason="needs GOTRUE_BIN and V2_SCHEMA_ADMIN_URI (CI job storage)")


def fake_key(tag="k"):
    return f"sk-FAKE-{tag}-{uuid.uuid4().hex}{uuid.uuid4().hex[:6]}"


class Provider:
    """Loopback stand-in for every provider: a key is valid iff registered;
    otherwise 401 with a body that ECHOES the key (as real providers do)."""

    def __init__(self):
        self.valid, self.requests, self.mode = set(), [], "normal"
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hdr = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append((self.path, hdr))
                presented = (hdr.get("authorization", "")[7:] or hdr.get("x-api-key")
                             or hdr.get("x-goog-api-key") or "")
                if outer.mode == "outage":
                    code, body = 503, b'{"error":"overloaded"}'
                elif outer.mode == "slow":
                    time.sleep(1.5)
                    code, body = 200, b"{}"
                elif self.path.endswith("/api/tags") or presented in outer.valid:
                    code, body = 200, b'{"data":[]}'
                else:
                    code, body = 401, json.dumps({"error": f"Incorrect API key provided: {presented}"}).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class ParamSpy:
    """Every parameter the server sends to PostgreSQL (psycopg AsyncConnection.execute)."""

    def __init__(self):
        self.params = []


@pytest.fixture(scope="module")
def byok():
    from agos_v2.byok import PROVIDERS, KeyTester, KeyVault
    prov = Provider()
    kek = os.urandom(32)
    vault = KeyVault(kek)
    tester = KeyTester(endpoints={p: prov.url + "/v1/models" for p in PROVIDERS if p != "ollama"},
                       allow_private=True, timeout=1.0)
    with live_stack(DB, (STUB, SQL1, SQL2, SQL3, SQL4),
                    {"vault": vault, "key_tester": tester}) as st:
        st.prov, st.vault, st.kek = prov, vault, kek
        yield st
    prov.srv.shutdown()


@pytest.fixture
def spy(monkeypatch):
    s = ParamSpy()
    orig = psycopg.AsyncConnection.execute

    async def execute(self, query, params=None, *a, **kw):
        s.params.append(params)
        return await orig(self, query, params, *a, **kw)
    monkeypatch.setattr(psycopg.AsyncConnection, "execute", execute)
    return s


def claims_of(u) -> dict:
    """The verified claims of a real access token (payload of the JWT GoTrue signed)."""
    payload = u.token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def kpath(ws, kid=None, tail=""):
    return f"/api/v2/workspaces/{ws}/provider-keys" + (f"/{kid}" if kid else "") + tail


def add(st, u, ws, provider="openai", key=None, label="mine", **extra):
    body = {"provider": provider, "label": label, **extra}
    if key is not None:
        body["api_key"] = key
    return st.client.post(kpath(ws), json=body, headers=u.h)


def _contains(blob, secret):
    secret_b = secret.encode()
    variants = [secret_b, secret_b[:-4], base64.b64encode(secret_b), secret_b.hex().encode()]
    if isinstance(blob, (bytes, bytearray, memoryview)):
        b = bytes(blob)
        return any(v in b for v in variants)
    return any(v.decode() in str(blob) for v in variants)


def dump_everything(st):
    """What a logical backup holds: every row of every table in every schema."""
    rows = []
    tables = sql(st, "select table_schema, table_name from information_schema.tables "
                     "where table_type = 'BASE TABLE' and table_schema not in ('pg_catalog', 'information_schema')")
    for schema, table in tables:
        for (txt,) in sql(st, f'select t::text from "{schema}"."{table}" t'):
            rows.append(txt)
        for (raw,) in sql(st, f'select convert_to(t::text, \'UTF8\') from "{schema}"."{table}" t'):
            rows.append(bytes(raw))
    return rows


# ================================================================ lifecycle

def test_lifecycle_add_list_edit_test_revoke_delete(byok):
    st, c = byok, byok.client
    u = register(st, "life")
    ws = new_ws(st, u, "keys")
    k1 = fake_key("life")
    st.prov.valid.add(k1)
    r = add(st, u, ws, "openai", k1, "work key")
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["last4"] == k1[-4:] and meta["status"] == "active" and meta["has_secret"] is True
    assert meta["provider"] == "openai" and meta["label"] == "work key"
    assert r.headers["cache-control"] == "no-store"
    kid = meta["id"]
    assert [k["id"] for k in c.get(kpath(ws), headers=u.h).json()] == [kid]

    t = c.post(kpath(ws, kid, "/test"), headers=u.h)
    assert t.status_code == 200 and t.json()["ok"] is True and t.json()["reason"] == "ok"
    listed = c.get(kpath(ws), headers=u.h).json()[0]
    assert listed["last_test_ok"] is True and listed["last_test_reason"] == "ok"

    # edit: label only, then replace the key with a wrong one
    e = c.patch(kpath(ws, kid), json={"label": "renamed"}, headers=u.h)
    assert e.status_code == 200 and e.json()["label"] == "renamed" and e.json()["last4"] == k1[-4:]
    wrong = fake_key("wrong")
    e = c.patch(kpath(ws, kid), json={"api_key": wrong}, headers=u.h)
    assert e.status_code == 200 and e.json()["last4"] == wrong[-4:] and e.json()["last_test_ok"] is None
    t = c.post(kpath(ws, kid, "/test"), headers=u.h).json()
    assert t["ok"] is False and t["reason"] == "invalid_key" and t["http_status"] == 401

    # revoke: stops use NOW, ciphertext is wiped, metadata stays visible
    n_before = len(st.prov.requests)
    rv = c.post(kpath(ws, kid, "/revoke"), headers=u.h)
    assert rv.status_code == 200 and rv.json()["status"] == "revoked" and rv.json()["has_secret"] is False
    assert sql(st, "select key_ct, dek_wrapped, kek_id from agos.provider_keys where id = %s", (kid,)) == \
        [(None, None, None)]
    assert c.post(kpath(ws, kid, "/test"), headers=u.h).status_code == 410
    assert c.patch(kpath(ws, kid), json={"api_key": k1}, headers=u.h).status_code == 410
    assert len(st.prov.requests) == n_before  # nothing reached the provider after revoke

    # delete: gone
    assert c.delete(kpath(ws, kid), headers=u.h).status_code == 204
    assert c.post(kpath(ws, kid, "/test"), headers=u.h).status_code == 404
    assert c.delete(kpath(ws, kid), headers=u.h).status_code == 404
    assert c.get(kpath(ws), headers=u.h).json() == []
    assert sql(st, "select count(*) from agos.provider_keys where id = %s", (kid,)) == [(0,)]


def test_all_seven_providers_and_ollama_url_rules(byok):
    st, c = byok, byok.client
    u = register(st, "prov")
    ws = new_ws(st, u)
    for pid in ("openai", "anthropic", "google", "openrouter", "mistral", "groq"):
        k = fake_key(pid)
        st.prov.valid.add(k)
        r = add(st, u, ws, pid, k, pid)
        assert r.status_code == 201, r.text
        assert c.post(kpath(ws, r.json()["id"], "/test"), headers=u.h).json()["reason"] == "ok", pid
        assert add(st, u, ws, pid, None, "no key").status_code == 422  # a cloud key needs a key
        assert add(st, u, ws, pid, k, "url", base_url=st.prov.url).status_code == 422
    # Ollama: a URL, key optional; loopback reachable only because the test allows private networks
    r = add(st, u, ws, "ollama", None, "local", base_url=st.prov.url + "/")
    assert r.status_code == 201 and r.json()["base_url"] == st.prov.url and r.json()["has_secret"] is False
    assert c.post(kpath(ws, r.json()["id"], "/test"), headers=u.h).json()["reason"] == "ok"
    assert add(st, u, ws, "ollama", None, "nourl").status_code == 422
    assert add(st, u, ws, "ollama", None, "creds", base_url="http://a:b@host:1").status_code == 422
    assert add(st, u, ws, "nope", fake_key(), "x").status_code == 422
    assert {p["id"] for p in c.get("/api/v2/provider-keys/providers").json()} == \
        {"openai", "anthropic", "google", "openrouter", "mistral", "groq", "ollama"}


# ================================================================ no leak

def test_the_key_never_leaves_in_responses_logs_db_params_or_backups(byok, spy, caplog):
    st, c = byok, byok.client
    caplog.set_level(logging.DEBUG)
    u = register(st, "leak")
    ws = new_ws(st, u)
    good, bad = fake_key("good"), fake_key("bad")
    st.prov.valid.add(good)
    responses = []

    def keep(r):
        responses.append(r)
        return r
    kid = keep(add(st, u, ws, "anthropic", good, "leak-test")).json()["id"]
    keep(c.get(kpath(ws), headers=u.h))
    keep(c.post(kpath(ws, kid, "/test"), headers=u.h))
    keep(c.patch(kpath(ws, kid), json={"api_key": bad}, headers=u.h))
    keep(c.post(kpath(ws, kid, "/test"), headers=u.h))           # provider echoes `bad` in its 401 body
    # errors carrying the key in the request
    keep(add(st, u, ws, "openai", good, "x", apiKey=good))         # extra field
    keep(add(st, u, ws, "openai", good + " tail", "x"))           # invalid char
    keep(add(st, u, ws, "openai", good * 20, "x"))                # too long
    keep(add(st, u, ws, "openai", good, good * 3))                # label too long, holds the key
    keep(add(st, u, str(uuid.uuid4()), "openai", good, "x"))      # not my workspace
    keep(c.post(kpath(ws), content=b'{"api_key": "' + good.encode() + b'", broken', headers=u.h))
    keep(c.get("/api/system/status"))
    assert {r.status_code for r in responses} >= {200, 201, 404, 422}

    for secret in (good, bad):
        for r in responses:
            assert not _contains(r.text, secret), (r.request.method, r.request.url, r.text[:200])
            assert not _contains(repr(dict(r.headers)), secret)
        assert not _contains(caplog.text, secret)
        for rec in caplog.records:
            assert not _contains(rec.getMessage(), secret) and not _contains(repr(rec.args), secret)
        for p in spy.params:
            for v in (p.values() if isinstance(p, dict) else (p or ())):
                assert not _contains(v if isinstance(v, (bytes, bytearray, memoryview)) else str(v), secret)
        for row in dump_everything(st):
            assert not _contains(row, secret)
    # the KEK itself is nowhere either
    kek_b64 = base64.b64encode(st.kek).decode()
    for blob in [r.text for r in responses] + [caplog.text] + [str(x) for x in dump_everything(st)]:
        assert kek_b64 not in blob and st.kek.hex() not in blob
    assert spy.params, "the spy must have seen the server's queries"


def test_ciphertext_columns_are_not_readable_even_by_the_owner(byok):
    st = byok
    u = register(st, "cols")
    ws = new_ws(st, u)
    kid = add(st, u, ws, "groq", fake_key(), "g").json()["id"]
    claims = json.dumps(claims_of(u))
    with psycopg.connect(st.db) as conn:
        conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        conn.execute("set local role authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select key_ct from agos.provider_keys where id = %s", (kid,))
    with psycopg.connect(st.db) as conn:
        conn.execute("set local role authenticated")
        for q in ("insert into agos.provider_keys (id) values (gen_random_uuid())",
                  "update agos.provider_keys set label = 'x'", "delete from agos.provider_keys",
                  "select agos.byok_own_key(gen_random_uuid())"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(q)
            conn.rollback()
            conn.execute("set local role authenticated")


# ================================================================ isolation

def test_other_users_and_other_members_cannot_see_or_touch_a_key(byok):
    st, c = byok, byok.client
    a, b, m = register(st, "isoa"), register(st, "isob"), register(st, "isom")
    wa, wb = new_ws(st, a, "A"), new_ws(st, b, "B")
    tok = invite(st, a, wa, m.email, "admin").json()["token"]
    assert accept(st, m, tok).status_code == 200
    ka = fake_key("a")
    st.prov.valid.add(ka)
    kid = add(st, a, wa, "openai", ka, "A's").json()["id"]
    km = add(st, m, wa, "mistral", fake_key("m"), "M's").json()["id"]
    n = len(st.prov.requests)
    for user, ws in ((b, wa), (b, wb), (m, wa)):  # stranger, stranger via own ws, admin of the same ws
        assert c.post(kpath(ws, kid, "/test"), headers=user.h).status_code == 404
        assert c.patch(kpath(ws, kid), json={"label": "pwned"}, headers=user.h).status_code == 404
        assert c.patch(kpath(ws, kid), json={"api_key": fake_key()}, headers=user.h).status_code == 404
        assert c.post(kpath(ws, kid, "/revoke"), headers=user.h).status_code == 404
        assert c.delete(kpath(ws, kid), headers=user.h).status_code == 404
    assert c.get(kpath(wa), headers=b.h).status_code == 404
    assert [k["id"] for k in c.get(kpath(wa), headers=m.h).json()] == [km]   # only M's own
    assert [k["id"] for k in c.get(kpath(wa), headers=a.h).json()] == [kid]  # owner does not see M's
    assert len(st.prov.requests) == n
    got = c.get(kpath(wa), headers=a.h).json()[0]
    assert got["label"] == "A's" and got["status"] == "active" and got["last4"] == ka[-4:]
    # the database refuses too (not only the API): B's session, A's key
    claims = json.dumps(claims_of(b))
    with psycopg.connect(st.db) as conn:
        conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        conn.execute("set local role authenticated")
        assert conn.execute("select count(*) from agos.provider_keys").fetchone()[0] == 0


def test_removed_member_loses_keys_and_copied_ciphertext_does_not_decrypt(byok):
    st, c = byok, byok.client
    a, m = register(st, "rma"), register(st, "rmm")
    wa = new_ws(st, a)
    assert accept(st, m, invite(st, a, wa, m.email).json()["token"]).status_code == 200
    km = add(st, m, wa, "openai", fake_key("m"), "m").json()["id"]
    ka_secret = fake_key("victim")
    st.prov.valid.add(ka_secret)
    ka = add(st, a, wa, "openai", ka_secret, "a").json()["id"]
    wm = new_ws(st, m, "m-own")
    km2 = add(st, m, wm, "openai", fake_key("m2"), "m2").json()["id"]
    # an attacker with database write access copies A's ciphertext into M's row
    sql(st, "update agos.provider_keys d set key_ct = s.key_ct, key_nonce = s.key_nonce, "
            "dek_wrapped = s.dek_wrapped, dek_nonce = s.dek_nonce, kek_id = s.kek_id "
            "from agos.provider_keys s where s.id = %s and d.id = %s", (ka, km2))
    n = len(st.prov.requests)
    r = c.post(kpath(wm, km2, "/test"), headers=m.h)
    assert r.status_code == 409 and "integrity" in r.json()["detail"]
    assert len(st.prov.requests) == n  # A's key was never used
    # removing M from A's workspace deletes M's keys there
    assert c.delete(f"/api/v2/workspaces/{wa}/members/{m.id}", headers=a.h).status_code == 204
    assert sql(st, "select count(*) from agos.provider_keys where id = %s", (km,)) == [(0,)]


# ================================================================ failures

def test_provider_outage_timeout_and_wrong_vault_key_are_honest(byok):
    st, c = byok, byok.client
    u = register(st, "fail")
    ws = new_ws(st, u)
    k = fake_key("out")
    st.prov.valid.add(k)
    kid = add(st, u, ws, "openrouter", k, "o").json()["id"]
    try:
        st.prov.mode = "outage"
        t = c.post(kpath(ws, kid, "/test"), headers=u.h).json()
        assert t == {**t, "ok": False, "reason": "provider_unavailable", "http_status": 503}
        st.prov.mode = "slow"
        t = c.post(kpath(ws, kid, "/test"), headers=u.h).json()
        assert t["ok"] is False and t["reason"] == "timeout" and t["http_status"] is None
    finally:
        st.prov.mode = "normal"
    # the server's vault key changed (e.g. a wrong AGOS_BYOK_KEK): refuse, never call the provider
    from agos_v2.byok import KeyVault
    saved = st.svc.vault
    st.svc.vault = KeyVault(os.urandom(32))
    n = len(st.prov.requests)
    try:
        r = c.post(kpath(ws, kid, "/test"), headers=u.h)
        assert r.status_code == 409 and "different server vault key" in r.json()["detail"]
    finally:
        st.svc.vault = saved
    assert len(st.prov.requests) == n
    # vault not configured at all: 503, not a fallback
    st.svc.vault = None
    try:
        for r in (c.get(kpath(ws), headers=u.h), add(st, u, ws, "openai", fake_key(), "x"),
                  c.post(kpath(ws, kid, "/test"), headers=u.h)):
            assert r.status_code == 503 and "AGOS_BYOK_KEK" in r.json()["detail"]
        # identity comes first: without a session the answer is 401 and says
        # nothing about the server's vault (found on the live deploy, 2026-11-14)
        anon = (c.get(kpath(ws)), c.post(kpath(ws), json={"provider": "openai"}),
                c.patch(kpath(ws, kid), json={"label": "x"}), c.post(kpath(ws, kid, "/revoke")),
                c.delete(kpath(ws, kid)), c.post(kpath(ws, kid, "/test")))
        for r in anon:
            assert r.status_code == 401, (r.request.method, r.request.url, r.status_code, r.text[:120])
            assert "AGOS_BYOK_KEK" not in r.text and "vault" not in r.text.lower()
    finally:
        st.svc.vault = saved


def test_abuse_caps_key_count_and_test_rate(byok):
    st, c = byok, byok.client
    u = register(st, "abuse")
    ws = new_ws(st, u)
    ids = [add(st, u, ws, "groq", fake_key(str(i)), f"k{i}").json()["id"] for i in range(20)]
    r = add(st, u, ws, "groq", fake_key("21"), "k21")
    assert r.status_code == 409 and "limit 20" in r.json()["detail"]
    codes = [c.post(kpath(ws, ids[0], "/test"), headers=u.h).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    st.svc.byok_test_limiter._hits.clear()


def test_resolve_for_the_gateway_stops_at_once_and_usage_is_recorded(byok):
    """The R-206 entry point: no cache — revoke/delete refuse the very next use."""
    import asyncio
    from agos_v2.byok import resolve_key
    st, c = byok, byok.client
    u = register(st, "gw")
    ws = new_ws(st, u)
    k = fake_key("gw")
    kid = add(st, u, ws, "mistral", k, "gw").json()["id"]

    from agos_v2.auth import Identity
    from agos_v2.db import AccountsDB
    cl = claims_of(u)
    ident = Identity(user_id=cl["sub"], session_id=cl["session_id"], email=cl.get("email"), claims=cl)

    async def use(fn):  # a pool of its own: this runs on its own event loop
        db = AccountsDB(st.db, min_size=1, max_size=1)
        try:
            async with db.user_tx(ident) as conn:
                return await fn(conn)
        finally:
            await db.close()

    async def scenario():
        p, base, secret = await use(lambda conn: resolve_key(conn, st.vault, kid))
        assert (p.id, base, secret) == ("mistral", None, k)
        await use(lambda conn: conn.execute("select agos.byok_record_usage(%s, 120, 30, 0.0042)", (kid,)))
        with pytest.raises(psycopg.Error) as e:
            await use(lambda conn: conn.execute("select agos.byok_record_usage(%s, -1, 0, 0)", (kid,)))
        assert e.value.sqlstate == "AG422"
    asyncio.run(scenario())
    meta = c.get(kpath(ws), headers=u.h).json()[0]
    assert (meta["use_count"], meta["tokens_in"], meta["tokens_out"], meta["cost_usd"]) == (1, 120, 30, "0.004200")
    assert meta["last_used_at"] is not None
    assert c.post(kpath(ws, kid, "/revoke"), headers=u.h).status_code == 200

    async def after_revoke():
        with pytest.raises(psycopg.Error) as e:
            await use(lambda conn: resolve_key(conn, st.vault, kid))
        assert e.value.sqlstate == "AG410"
    asyncio.run(after_revoke())
    assert c.delete(kpath(ws, kid), headers=u.h).status_code == 204

    async def after_delete():
        with pytest.raises(psycopg.Error) as e:
            await use(lambda conn: resolve_key(conn, st.vault, kid))
        assert e.value.sqlstate == "AG404"
    asyncio.run(after_delete())


def test_signed_out_session_cannot_use_keys(byok):
    st, c = byok, byok.client
    u = register(st, "out")
    ws = new_ws(st, u)
    kid = add(st, u, ws, "openai", fake_key(), "x").json()["id"]
    assert c.post("/api/v2/auth/logout", headers=u.h).status_code in (200, 204)
    assert c.get(kpath(ws), headers=u.h).status_code == 401
    assert c.post(kpath(ws, kid, "/test"), headers=u.h).status_code == 401


# ================================================================ rollback

def test_rollback_is_clean_and_004_reapplies():
    """The owner-approval plan: 004 forward, its rollback, and forward again —
    on a database of its own; 001-003 objects survive the rollback."""
    from tests.test_r204_accounts_live import _uri
    rollback = ROOT / "docs" / "sql" / "v2" / "004_byok_rollback.sql"
    name = "agos_v2_r205_rollback_test"
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {name} with (force)")
        c.execute(f"create database {name}")
    try:
        with psycopg.connect(_uri(name), autocommit=True) as c:
            for f in (STUB, SQL1, SQL2, SQL3, SQL4):
                c.execute(f.read_text(encoding="utf-8"))

            def objects():
                return c.execute("""
                    select count(*) filter (where p.proname like 'byok%%'),
                           count(*) filter (where p.proname not like 'byok%%')
                      from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'agos'""").fetchone()
            byok_fns, other_fns = objects()
            assert byok_fns == 9
            c.execute(rollback.read_text(encoding="utf-8"))
            assert objects() == (0, other_fns)
            assert c.execute("select to_regclass('agos.provider_keys')").fetchone()[0] is None
            assert c.execute("select to_regclass('agos.workspace_members')").fetchone()[0] is not None
            assert c.execute("select count(*) from pg_trigger where tgname = 'trg_members_drop_keys'").fetchone()[0] == 0
            c.execute(SQL4.read_text(encoding="utf-8"))  # forward again, cleanly
            assert objects() == (9, other_fns)
    finally:
        with psycopg.connect(_uri("postgres"), autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
