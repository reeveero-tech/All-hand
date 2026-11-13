"""R-204 (D-153): accounts end-to-end — the REAL Supabase Auth server (GoTrue,
the binary Supabase runs) + PostgreSQL + server.py's /api/v2 routes.

Live only: ``GOTRUE_BIN`` (path to the pinned supabase/auth release binary,
its ``migrations/`` next to it) and ``V2_SCHEMA_ADMIN_URI`` (CI job ``storage``).

GoTrue runs like the live project is configured (checked 2026-11-13 through
the project's public /auth/v1/settings): e-mail sign-up on, e-mail
confirmation REQUIRED, ES256 access tokens from a JWKS. Confirming the e-mail
is done in SQL (the step the user's click on the e-mailed link performs) — no
mail server in CI. What this does NOT prove: the hosted Supabase project itself
(R-204 live verification, after the owner adds SUPABASE_DB_URL) and OAuth
providers (need the owner's Google/GitHub apps).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import pathlib
import socket
import subprocess
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STUB = ROOT / "scripts" / "v2" / "supabase_auth_stub.sql"
SQL1 = ROOT / "docs" / "sql" / "v2" / "001_core.sql"
SQL2 = ROOT / "docs" / "sql" / "v2" / "002_accounts.sql"
SQL3 = ROOT / "docs" / "sql" / "v2" / "003_function_search_path.sql"
ADMIN_URI = os.environ.get("V2_SCHEMA_ADMIN_URI", "")
GOTRUE_BIN = os.environ.get("GOTRUE_BIN", "")
DB = "agos_v2_r204_live_test"
PASSWORD = "Correct-Horse-9-battery"

pytestmark = pytest.mark.skipif(not (ADMIN_URI and GOTRUE_BIN),
                                reason="needs GOTRUE_BIN and V2_SCHEMA_ADMIN_URI (CI job storage)")


def _uri(db: str, user: str | None = None, password: str | None = None) -> str:
    base, _, _ = ADMIN_URI.rpartition("/")
    if user:
        scheme, _, rest = base.partition("://")
        host = rest.rpartition("@")[2]
        base = f"{scheme}://{user}:{password}@{host}"
    return f"{base}/{db}"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sign_es256(key, kid: str, claims: dict, alg: str = "ES256") -> str:
    h = b64u(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    p = b64u(json.dumps(claims).encode())
    der = key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{h}.{p}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class Stack:
    pass


@pytest.fixture(scope="module")
def stack():
    st = Stack()
    with psycopg.connect(_uri("postgres"), autocommit=True) as c:
        c.execute(f"drop database if exists {DB} with (force)")
        c.execute(f"create database {DB}")
        c.execute("""do $$ begin
          if not exists (select 1 from pg_roles where rolname = 'supabase_auth_admin') then
            create role supabase_auth_admin login password 'authadmin' noinherit createrole; end if;
          if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
          if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
          if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
        end $$""")
        c.execute("alter role supabase_auth_admin set search_path = auth")
    st.db = _uri(DB)
    with psycopg.connect(st.db, autocommit=True) as c:
        c.execute("create schema auth authorization supabase_auth_admin")

    # the signing key GoTrue uses (and the test may use to forge claims)
    st.key = ec.generate_private_key(ec.SECP256R1())
    st.kid = str(uuid.uuid4())
    n = st.key.private_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "kid": st.kid, "alg": "ES256", "use": "sig",
           "key_ops": ["sign", "verify"],
           "x": b64u(n.public_numbers.x.to_bytes(32, "big")),
           "y": b64u(n.public_numbers.y.to_bytes(32, "big")),
           "d": b64u(n.private_value.to_bytes(32, "big"))}
    port = _free_port()
    st.url = f"http://127.0.0.1:{port}"
    env = {**os.environ,
           "GOTRUE_DB_DRIVER": "postgres",
           "DATABASE_URL": _uri(DB, "supabase_auth_admin", "authadmin").replace("postgresql://", "postgres://")
           + "?sslmode=disable",
           "GOTRUE_DB_MIGRATIONS_PATH": str(pathlib.Path(GOTRUE_BIN).resolve().parent / "migrations"),
           "API_EXTERNAL_URL": st.url, "GOTRUE_SITE_URL": "https://agos.test",
           "GOTRUE_API_HOST": "127.0.0.1", "PORT": str(port),
           "GOTRUE_JWT_SECRET": "test-only-hs256-secret-never-accepted-by-agos-000",
           "GOTRUE_JWT_KEYS": json.dumps([jwk]), "GOTRUE_JWT_EXP": "3600",
           "GOTRUE_JWT_AUD": "authenticated", "GOTRUE_JWT_ISSUER": st.url,
           "GOTRUE_JWT_DEFAULT_GROUP_NAME": "authenticated", "GOTRUE_JWT_ADMIN_ROLES": "service_role",
           "GOTRUE_MAILER_AUTOCONFIRM": "false", "GOTRUE_EXTERNAL_EMAIL_ENABLED": "true",
           "GOTRUE_DISABLE_SIGNUP": "false", "GOTRUE_SMTP_MAX_FREQUENCY": "1ns",
           "GOTRUE_RATE_LIMIT_EMAIL_SENT": "100000", "GOTRUE_RATE_LIMIT_TOKEN_REFRESH": "100000",
           "GOTRUE_RATE_LIMIT_VERIFY": "100000", "GOTRUE_RATE_LIMIT_OTP": "100000",
           "GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED": "true",
           "GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL": "1",
           "GOTRUE_LOG_LEVEL": "warn"}
    m = subprocess.run([GOTRUE_BIN, "migrate"], env=env, capture_output=True, text=True, timeout=120)
    assert m.returncode == 0, m.stderr[-2000:]
    st.log = open(pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"gotrue_{port}.log", "wb")
    st.proc = subprocess.Popen([GOTRUE_BIN, "serve"], env=env, stdout=st.log, stderr=st.log)
    import urllib.request
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{st.url}/health", timeout=1) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        st.proc.kill()
        pytest.fail("GoTrue did not start")
    with psycopg.connect(st.db, autocommit=True) as c:
        for f in (STUB, SQL1, SQL2, SQL3):
            c.execute(f.read_text(encoding="utf-8"))

    os.environ.setdefault("AGOS_ENV", "test")
    import server
    from agos_v2.accounts_api import AccountsConfig, AccountsService
    from fastapi.testclient import TestClient

    st.server = server
    st.svc = AccountsService(AccountsConfig(supabase_url=st.url, public_key="test-public-key",
                                            db_url=st.db, public_url="https://agos.test",
                                            auth_url_override=st.url))
    saved = (server.V2_ACCOUNTS, server.RATE_LIMIT_REQUESTS)
    server.V2_ACCOUNTS = st.svc
    server.RATE_LIMIT_REQUESTS = 100000
    try:
        with TestClient(server.app, base_url="https://testserver") as client:
            st.client = client
            yield st
    finally:
        server.V2_ACCOUNTS, server.RATE_LIMIT_REQUESTS = saved
        st.proc.terminate()
        st.proc.wait(10)
        st.log.close()
        with psycopg.connect(_uri("postgres"), autocommit=True) as c:
            c.execute(f"drop database if exists {DB} with (force)")


# ---------------------------------------------------------------- helpers

class User:
    def __init__(self, email, token, refresh, user_id):
        self.email, self.token, self.refresh, self.id = email, token, refresh, user_id

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.token}"}


def sql(st, query, params=()):
    with psycopg.connect(st.db, autocommit=True) as c:
        cur = c.execute(query, params)
        return cur.fetchall() if cur.description else None


def register(st, tag) -> User:
    c = st.client
    email = f"{tag}.{uuid.uuid4().hex[:8]}@r204.test"
    r = c.post("/api/v2/auth/signup", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json() == {"confirmation_required": True, "session": None}
    r = c.post("/api/v2/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 403 and "not confirmed" in r.json()["detail"]
    sql(st, "update auth.users set email_confirmed_at = now() where email = %s", (email,))
    return login(st, email)


def login(st, email) -> User:
    c = st.client
    r = c.post("/api/v2/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    rt = r.cookies.get("agos_rt")
    c.cookies.clear()
    body = r.json()
    return User(email, body["access_token"], rt, body["user"]["id"])


def refresh(st, rt):
    st.client.cookies.clear()
    r = st.client.post("/api/v2/auth/refresh", headers={"Cookie": f"agos_rt={rt}"})
    st.client.cookies.clear()
    return r


def new_ws(st, u, name="ws"):
    r = st.client.post("/api/v2/workspaces", json={"name": name}, headers=u.h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def invite(st, u, ws, email, role="member"):
    return st.client.post(f"/api/v2/workspaces/{ws}/invitations", json={"email": email, "role": role},
                          headers=u.h)


def accept(st, u, token):
    return st.client.post("/api/v2/invitations/accept", json={"token": token}, headers=u.h)


# ================================================================ sign-up

def test_signup_confirm_login_session_shape(stack):
    c = stack.client
    email = f"shape.{uuid.uuid4().hex[:8]}@r204.test"
    assert c.post("/api/v2/auth/signup", json={"email": email, "password": PASSWORD}).status_code == 200
    sql(stack, "update auth.users set email_confirmed_at = now() where email = %s", (email,))
    r = c.post("/api/v2/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "token_type", "expires_at", "expires_in", "user"}
    assert "refresh_token" not in r.text  # never in the body
    sc = r.headers["set-cookie"].lower()
    for flag in ("agos_rt=", "httponly", "secure", "samesite=strict", "path=/api/v2/auth"):
        assert flag in sc, sc
    assert r.headers["cache-control"] == "no-store"
    c.cookies.clear()
    me = c.get("/api/v2/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json() == {"user": {"id": body["user"]["id"], "email": email}, "workspaces": []}


def test_duplicate_signup_and_wrong_password_reveal_nothing(stack):
    u = register(stack, "dup")
    c = stack.client
    r = c.post("/api/v2/auth/signup", json={"email": u.email, "password": "Another-pass-123"})
    assert r.status_code == 200 and r.json() == {"confirmation_required": True, "session": None}
    bad = c.post("/api/v2/auth/login", json={"email": u.email, "password": "wrong-password-1"})
    nobody = c.post("/api/v2/auth/login", json={"email": "nobody@r204.test", "password": "wrong-password-1"})
    assert bad.status_code == nobody.status_code == 401
    assert bad.json() == nobody.json() == {"detail": "invalid email or password"}
    # the old password still works (duplicate sign-up changed nothing)
    assert login(stack, u.email).token


def test_weak_password_is_refused(stack):
    r = stack.client.post("/api/v2/auth/signup", json={"email": "weak@r204.test", "password": "short"})
    assert r.status_code == 422


# ============================================================== isolation

def test_user_b_cannot_reach_user_a_workspace_through_any_route(stack):
    a, b = register(stack, "isoa"), register(stack, "isob")
    wa, wb = new_ws(stack, a, "A-space"), new_ws(stack, b, "B-space")
    inv = invite(stack, a, wa, "pending@r204.test").json()
    c = stack.client
    missing = str(uuid.uuid4())
    for ws in (wa, missing, "not-a-uuid"):
        assert c.get(f"/api/v2/workspaces/{ws}", headers=b.h).status_code == 404
        assert c.patch(f"/api/v2/workspaces/{ws}", json={"name": "pwned"}, headers=b.h).status_code == 404
        assert c.delete(f"/api/v2/workspaces/{ws}", headers=b.h).status_code == 404
        assert c.get(f"/api/v2/workspaces/{ws}/members", headers=b.h).status_code == 404
        assert c.get(f"/api/v2/workspaces/{ws}/invitations", headers=b.h).status_code == 404
        assert invite(stack, b, ws, b.email).status_code == 404
        assert c.patch(f"/api/v2/workspaces/{ws}/members/{b.id}", json={"role": "owner"},
                       headers=b.h).status_code == 404
        assert c.delete(f"/api/v2/workspaces/{ws}/members/{a.id}", headers=b.h).status_code == 404
    # A's invitation cannot be revoked through B's own workspace path either
    assert c.delete(f"/api/v2/workspaces/{wb}/invitations/{inv['id']}", headers=b.h).status_code == 404
    assert c.delete(f"/api/v2/workspaces/{wa}/invitations/{inv['id']}", headers=b.h).status_code == 404
    assert [w["id"] for w in c.get("/api/v2/workspaces", headers=b.h).json()] == [wb]
    # nothing of A changed
    got = c.get(f"/api/v2/workspaces/{wa}", headers=a.h).json()
    assert got["name"] == "A-space" and got["role"] == "owner"
    assert [m["user_id"] for m in c.get(f"/api/v2/workspaces/{wa}/members", headers=a.h).json()] == [a.id]
    assert sql(stack, "select revoked_at from agos.workspace_invitations where id = %s", (inv["id"],))[0][0] is None


def test_server_api_key_is_not_a_v2_identity(stack, monkeypatch):
    from agent_civilization.core.tenancy import Principal, key_digest
    srv = stack.server
    key = "r204-operator-key-0123456789"
    monkeypatch.setattr(srv, "CREDENTIALS", {**srv.CREDENTIALS, key_digest(key): Principal("ops", "admin")})
    monkeypatch.setattr(srv, "ENABLE_AUTH", True)
    c = stack.client
    assert c.get("/api/tasks", headers={"X-API-Key": key}).status_code == 200  # v1 automation key works
    for path in ("/api/v2/me", "/api/v2/workspaces"):
        r = c.get(path, headers={"X-API-Key": key})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        r = c.get(path, headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401


# ======================================================== roles + invites

def test_invitation_roles_and_membership_through_the_api(stack):
    o, m, x = register(stack, "ownr"), register(stack, "mmbr"), register(stack, "other")
    c = stack.client
    ws = new_ws(stack, o, "team")
    r = invite(stack, o, ws, m.email.upper(), "member")
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    inv = r.json()
    assert inv["accept_url"] == f"https://agos.test/#invite={inv['token']}"
    assert inv["email"] == m.email and inv["role"] == "member"
    # only the hash is stored
    stored = sql(stack, "select token_sha256 from agos.workspace_invitations where id = %s", (inv["id"],))[0][0]
    assert stored == hashlib.sha256(inv["token"].encode()).hexdigest() and inv["token"] not in stored
    assert accept(stack, x, inv["token"]).status_code == 403  # someone else's link
    r = accept(stack, m, inv["token"])
    assert r.status_code == 200 and r.json() == {"workspace_id": ws, "role": "member"}
    assert accept(stack, m, inv["token"]).status_code == 409  # single use
    assert c.get(f"/api/v2/workspaces/{ws}", headers=m.h).json()["role"] == "member"
    emails = {mm["email"]: mm["role"] for mm in c.get(f"/api/v2/workspaces/{ws}/members", headers=m.h).json()}
    assert emails == {o.email: "owner", m.email: "member"}
    # member limits
    assert c.patch(f"/api/v2/workspaces/{ws}", json={"name": "x"}, headers=m.h).status_code == 403
    assert c.delete(f"/api/v2/workspaces/{ws}", headers=m.h).status_code == 403
    assert invite(stack, m, ws, "n1@r204.test").status_code == 403
    assert c.get(f"/api/v2/workspaces/{ws}/invitations", headers=m.h).status_code == 403
    assert c.patch(f"/api/v2/workspaces/{ws}/members/{m.id}", json={"role": "owner"},
                   headers=m.h).status_code == 403
    # promoted to admin: may invite members, not admins; may not delete the workspace
    assert c.patch(f"/api/v2/workspaces/{ws}/members/{m.id}", json={"role": "admin"},
                   headers=o.h).status_code == 200
    assert invite(stack, m, ws, "n2@r204.test", "member").status_code == 201
    assert invite(stack, m, ws, "n3@r204.test", "admin").status_code == 403
    assert c.delete(f"/api/v2/workspaces/{ws}", headers=m.h).status_code == 403
    assert c.patch(f"/api/v2/workspaces/{ws}", json={"name": "renamed"}, headers=m.h).status_code == 200
    # the last owner stays
    assert c.delete(f"/api/v2/workspaces/{ws}/members/{o.id}", headers=o.h).status_code == 409
    assert c.patch(f"/api/v2/workspaces/{ws}/members/{o.id}", json={"role": "member"},
                   headers=o.h).status_code == 409
    states = {i["email"]: i["state"] for i in c.get(f"/api/v2/workspaces/{ws}/invitations", headers=o.h).json()}
    assert states[m.email] == "accepted" and states["n2@r204.test"] == "pending"
    assert all("token" not in i and "token_sha256" not in i
               for i in c.get(f"/api/v2/workspaces/{ws}/invitations", headers=o.h).json())


def test_expired_and_revoked_invitations_through_the_api(stack):
    o, e = register(stack, "exo"), register(stack, "exe")
    c = stack.client
    ws = new_ws(stack, o)
    inv = invite(stack, o, ws, e.email).json()
    sql(stack, "update agos.workspace_invitations set created_at = now() - interval '4 days', "
               "expires_at = now() - interval '1 second' where id = %s", (inv["id"],))
    r = accept(stack, e, inv["token"])
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    inv2 = invite(stack, o, ws, e.email).json()
    assert c.delete(f"/api/v2/workspaces/{ws}/invitations/{inv2['id']}", headers=o.h).status_code == 204
    r = accept(stack, e, inv2["token"])
    assert r.status_code == 410 and "revoked" in r.json()["detail"]
    assert accept(stack, e, "x" * 43).status_code == 404
    assert c.get(f"/api/v2/workspaces/{ws}", headers=e.h).status_code == 404


def test_switching_workspace_and_losing_membership(stack):
    o, u = register(stack, "swo"), register(stack, "swu")
    c = stack.client
    w1, w2 = new_ws(stack, o, "one"), new_ws(stack, o, "two")
    accept(stack, u, invite(stack, o, w1, u.email).json()["token"])
    assert c.get(f"/api/v2/workspaces/{w1}", headers=u.h).status_code == 200
    assert c.get(f"/api/v2/workspaces/{w2}", headers=u.h).status_code == 404  # switching by id: no
    assert [w["id"] for w in c.get("/api/v2/me", headers=o.h).json()["workspaces"]] == [w1, w2]
    assert c.delete(f"/api/v2/workspaces/{w1}/members/{u.id}", headers=o.h).status_code == 204
    # the same, still valid, token loses access at once
    assert c.get(f"/api/v2/workspaces/{w1}", headers=u.h).status_code == 404
    assert c.get("/api/v2/workspaces", headers=u.h).json() == []
    assert c.delete(f"/api/v2/workspaces/{w2}", headers=o.h).status_code == 204
    assert [w["id"] for w in c.get("/api/v2/workspaces", headers=o.h).json()] == [w1]


# =============================================================== sessions

def test_logout_ends_the_session_immediately(stack):
    u = register(stack, "out")
    c = stack.client
    assert c.get("/api/v2/me", headers=u.h).status_code == 200
    c.cookies.clear()
    r = c.post("/api/v2/auth/logout", headers={**u.h, "Cookie": f"agos_rt={u.refresh}"})
    assert r.status_code == 204
    sc = r.headers["set-cookie"].lower()
    assert "agos_rt=" in sc and ("max-age=0" in sc or "expires=" in sc)
    c.cookies.clear()
    r = c.get("/api/v2/me", headers=u.h)  # access token not expired, but its session is gone
    assert r.status_code == 401 and r.json()["detail"] == "session ended"
    assert refresh(stack, u.refresh).status_code == 401


def test_refresh_rotates_and_a_reused_refresh_token_is_refused(stack):
    u = register(stack, "rot")
    r = refresh(stack, u.refresh)
    assert r.status_code == 200
    new_token, new_rt = r.json()["access_token"], r.cookies.get("agos_rt")
    assert new_token != u.token and new_rt and new_rt != u.refresh
    assert stack.client.get("/api/v2/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    # Supabase Auth behaviour (supabase/auth v2.197.0 internal/tokens/service.go):
    # the IMMEDIATE parent of the active token is accepted and answered with the
    # SAME active token (a client that lost the response) — no new branch.
    again = refresh(stack, u.refresh)
    assert again.status_code == 200 and again.cookies.get("agos_rt") == new_rt
    # rotate once more: the original token is now two generations back
    r2 = refresh(stack, new_rt)
    assert r2.status_code == 200
    newest = r2.cookies.get("agos_rt")
    time.sleep(1.5)  # past the reuse interval (1 s here; Supabase default 10 s)
    reused = refresh(stack, u.refresh)
    assert reused.status_code == 401
    # reuse detection revoked the whole family (GoTrue stamps updated_at=now()
    # while revoking, so wait past the interval again): the newest dies too
    time.sleep(1.5)
    assert refresh(stack, newest).status_code == 401
    assert refresh(stack, "").status_code == 401


def test_expired_access_token_is_refused(stack):
    u = register(stack, "exp")
    v = stack.svc.verifier
    real = v._clock
    try:
        v._clock = lambda: time.time() + 3600 + 60
        r = stack.client.get("/api/v2/me", headers=u.h)
        assert r.status_code == 401 and r.json()["detail"] == "session expired"
    finally:
        v._clock = real
    assert stack.client.get("/api/v2/me", headers=u.h).status_code == 200


def test_banned_user_is_refused_with_a_valid_token(stack):
    u = register(stack, "ban")
    sql(stack, "update auth.users set banned_until = now() + interval '1 day' where id = %s", (u.id,))
    assert stack.client.get("/api/v2/me", headers=u.h).status_code == 401


def test_oauth_pkce_exchange_with_a_bogus_code_is_refused(stack):
    """The PKCE exchange route reaches the real auth server and turns its refusal
    into 401 with no session and no cookie; malformed input never reaches it."""
    c = stack.client
    r = c.post("/api/v2/auth/oauth/exchange",
               json={"auth_code": str(uuid.uuid4()), "code_verifier": "v" * 43})
    assert r.status_code == 401 and "access_token" not in r.text
    assert "agos_rt" not in r.headers.get("set-cookie", "")
    assert r.headers["cache-control"] == "no-store"
    for bad in ({"auth_code": "x", "code_verifier": "short"}, {"auth_code": "", "code_verifier": "v" * 43},
                {"auth_code": "x", "code_verifier": "v" * 43, "extra": 1}):
        assert c.post("/api/v2/auth/oauth/exchange", json=bad).status_code == 422


def test_forged_and_malformed_tokens_are_refused(stack):
    u = register(stack, "forge")
    other = register(stack, "victim")
    c = stack.client
    h64, p64, s64 = u.token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4)))

    def get(tok):
        return c.get("/api/v2/me", headers={"Authorization": f"Bearer {tok}"}).status_code

    # tampered payload (swap the user) with the original signature
    swapped = b64u(json.dumps({**claims, "sub": other.id}).encode())
    assert get(f"{h64}.{swapped}.{s64}") == 401
    # signed by an attacker's key under the real kid
    assert get(sign_es256(ec.generate_private_key(ec.SECP256R1()), stack.kid, claims)) == 401
    # alg=none, HS256
    none = b64u(json.dumps({"alg": "none", "kid": stack.kid}).encode())
    assert get(f"{none}.{p64}.") == 401
    hs = b64u(json.dumps({"alg": "HS256", "kid": stack.kid}).encode())
    assert get(f"{hs}.{p64}.{s64}") == 401
    # correctly signed (the real key) but wrong claims
    for bad in ({"role": "service_role"}, {"role": "anon"}, {"aud": "other"}, {"iss": "https://evil"},
                {"session_id": str(uuid.uuid4())}, {"session_id": None}, {"exp": int(time.time()) - 60}):
        tok = sign_es256(stack.key, stack.kid, {**claims, **bad})
        assert get(tok) == 401, bad
    # correctly signed, right claims, but another user's session id
    assert get(sign_es256(stack.key, stack.kid, {**claims, "session_id": json.loads(
        base64.urlsafe_b64decode(other.token.split(".")[1] + "==="))["session_id"]})) == 401
    for junk in ("", "abc", "a.b.c", "x" * 9000):
        assert get(junk) == 401
    assert c.get("/api/v2/me").status_code == 401
    assert get(u.token) == 200  # the real one still works


# =============================================================== leakage

def test_no_token_or_password_reaches_the_logs(stack, caplog):
    caplog.set_level(logging.DEBUG)
    o, u = register(stack, "logo"), register(stack, "logu")
    ws = new_ws(stack, o)
    inv = invite(stack, o, ws, u.email).json()
    accept(stack, u, inv["token"])
    r = refresh(stack, u.refresh)
    secrets_seen = [o.token, o.refresh, u.token, u.refresh, inv["token"], PASSWORD,
                    r.json()["access_token"], r.cookies.get("agos_rt")]
    stack.client.post("/api/v2/auth/logout", headers=o.h)
    stack.client.cookies.clear()
    text = "\n".join(f"{rec.getMessage()} {rec.args!r}" for rec in caplog.records)
    assert caplog.records, "the flow produced log records (the check is not vacuous)"
    for s in secrets_seen:
        assert s and s not in text


def test_routes_answer_503_when_accounts_are_not_configured(stack, monkeypatch):
    monkeypatch.setattr(stack.server, "V2_ACCOUNTS", None)
    c = stack.client
    assert c.get("/api/v2/me").status_code == 503
    assert c.post("/api/v2/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 503
    assert c.get("/api/v2/auth/config").json()["configured"] is False
