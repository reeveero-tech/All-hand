"""R-204 (D-153): offline checks of the accounts layer — token verifier,
configuration, error mapping, router guards. Always runs (no DB, no network);
the live proofs are tests/test_r204_accounts_db.py + test_r204_accounts_live.py.
"""
from __future__ import annotations

import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, hmac as chmac
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agos_v2 import auth
from agos_v2.accounts_api import AccountsConfig, SQLSTATE_HTTP, _db_error, build_router
from agos_v2.auth import JwksVerifier, TokenError

ISS = "https://proj.supabase.co/auth/v1"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Keys:
    def __init__(self):
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec_kid, self.rsa_kid = "ec-1", "rsa-1"
        self.fetches = 0

    def jwks(self):
        e = self.ec.public_key().public_numbers()
        r = self.rsa.public_key().public_numbers()
        return {"keys": [
            {"kty": "EC", "crv": "P-256", "kid": self.ec_kid, "alg": "ES256",
             "x": b64u(e.x.to_bytes(32, "big")), "y": b64u(e.y.to_bytes(32, "big"))},
            {"kty": "RSA", "kid": self.rsa_kid, "alg": "RS256",
             "n": b64u(r.n.to_bytes(256, "big")), "e": b64u(r.e.to_bytes(3, "big"))},
            {"kty": "oct", "kid": "hs", "alg": "HS256", "k": b64u(b"secret")},  # ignored
        ]}

    async def fetch(self):
        self.fetches += 1
        return self.jwks()


def claims(**over):
    now = int(time.time())
    c = {"iss": ISS, "aud": "authenticated", "role": "authenticated", "exp": now + 3600,
         "iat": now, "sub": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
         "email": "a@example.com", "is_anonymous": False}
    c.update(over)
    return {k: v for k, v in c.items() if v is not ...}


def es(key, kid, c, alg="ES256"):
    h = b64u(json.dumps({"alg": alg, "kid": kid}).encode())
    p = b64u(json.dumps(c).encode())
    r, s = decode_dss_signature(key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{h}.{p}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def rs(key, kid, c, alg="RS256"):
    h = b64u(json.dumps({"alg": alg, "kid": kid}).encode())
    p = b64u(json.dumps(c).encode())
    return f"{h}.{p}.{b64u(key.sign(f'{h}.{p}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def verifier(keys):
    return JwksVerifier(keys.fetch, ISS)


async def reason(v, tok):
    with pytest.raises(TokenError) as e:
        await v.verify(tok)
    return e.value.reason


async def test_valid_es256_and_rs256_tokens(keys, verifier):
    c = claims()
    ident = await verifier.verify(es(keys.ec, keys.ec_kid, c))
    assert (ident.user_id, ident.session_id, ident.email) == (c["sub"], c["session_id"], c["email"])
    assert (await verifier.verify(rs(keys.rsa, keys.rsa_kid, claims()))).user_id
    assert "claims" not in repr(ident)  # raw claims stay out of any repr/log


@pytest.mark.parametrize("over,code", [
    ({"exp": int(time.time()) - 60}, "expired"),
    ({"exp": ...}, "no_expiry"),
    ({"exp": True}, "no_expiry"),
    ({"nbf": int(time.time()) + 600}, "not_yet_valid"),
    ({"iat": int(time.time()) + 600}, "not_yet_valid"),
    ({"iss": "https://other.supabase.co/auth/v1"}, "bad_issuer"),
    ({"iss": ...}, "bad_issuer"),
    ({"aud": "anon"}, "bad_audience"),
    ({"role": "anon"}, "bad_role"),
    ({"role": "service_role"}, "bad_role"),
    ({"is_anonymous": True}, "anonymous"),
    ({"sub": "not-a-uuid"}, "bad_subject"),
    ({"session_id": ...}, "no_session"),
    ({"session_id": "x"}, "no_session"),
])
async def test_bad_claims_are_refused(keys, verifier, over, code):
    assert await reason(verifier, es(keys.ec, keys.ec_kid, claims(**over))) == code


async def test_audience_list_and_leeway(keys, verifier):
    assert await verifier.verify(es(keys.ec, keys.ec_kid, claims(aud=["x", "authenticated"])))
    assert await verifier.verify(es(keys.ec, keys.ec_kid, claims(exp=int(time.time()) - 2)))


async def test_signature_and_algorithm_attacks(keys, verifier):
    good = es(keys.ec, keys.ec_kid, claims())
    h, p, s = good.split(".")
    other = claims()
    assert await reason(verifier, f"{h}.{b64u(json.dumps(other).encode())}.{s}") == "bad_signature"
    assert await reason(verifier, es(ec.generate_private_key(ec.SECP256R1()), keys.ec_kid, claims())) \
        == "bad_signature"
    none = b64u(json.dumps({"alg": "none", "kid": keys.ec_kid}).encode())
    assert await reason(verifier, f"{none}.{p}.") == "bad_alg"
    # HS256 "signed" with the public key material — the classic confusion
    hs_h = b64u(json.dumps({"alg": "HS256", "kid": keys.ec_kid}).encode())
    mac = chmac.HMAC(json.dumps(keys.jwks()["keys"][0]).encode(), hashes.SHA256())
    mac.update(f"{hs_h}.{p}".encode())
    assert await reason(verifier, f"{hs_h}.{p}.{b64u(mac.finalize())}") == "bad_alg"
    # RS256 header against the EC key, ES256 header against the RSA key
    assert await reason(verifier, rs(keys.rsa, keys.ec_kid, claims())) == "bad_alg"
    assert await reason(verifier, es(keys.ec, keys.rsa_kid, claims())) == "bad_alg"
    assert await reason(verifier, es(keys.ec, "unknown", claims())) == "unknown_key"
    assert await reason(verifier, es(keys.ec, "hs", claims())) == "unknown_key"
    nokid = b64u(json.dumps({"alg": "ES256"}).encode())
    assert await reason(verifier, f"{nokid}.{p}.{s}") == "unknown_key"
    assert await reason(verifier, f"{h}.{p}.{s[:-4]}") in ("bad_signature", "malformed")


@pytest.mark.parametrize("tok,code", [
    (None, "missing"), ("", "missing"), ("abc", "malformed"), ("a.b", "malformed"),
    ("a.b.c.d", "malformed"), ("!!.!!.!!", "malformed"), ("x" * 9000, "malformed"),
    (b64u(b"[]") + "." + b64u(b"{}") + ".", "malformed"),
])
async def test_malformed_tokens(verifier, tok, code):
    assert await reason(verifier, tok) == code


async def test_unknown_kid_refetch_is_bounded(keys, verifier):
    await verifier.verify(es(keys.ec, keys.ec_kid, claims()))
    assert keys.fetches == 1
    for _ in range(20):  # an attacker spraying random kids
        await reason(verifier, es(keys.ec, str(uuid.uuid4()), claims()))
    assert keys.fetches == 1  # still inside JWKS_MIN_REFRESH_S


async def test_key_rotation_is_picked_up(keys, verifier, monkeypatch):
    await verifier.verify(es(keys.ec, keys.ec_kid, claims()))
    new = ec.generate_private_key(ec.SECP256R1())
    keys.ec, keys.ec_kid = new, "ec-2"
    monkeypatch.setattr(auth, "JWKS_MIN_REFRESH_S", 0.0)
    assert await verifier.verify(es(new, "ec-2", claims()))
    assert await reason(verifier, es(ec.generate_private_key(ec.SECP256R1()), "ec-1", claims())) \
        == "unknown_key"  # the retired key is gone


async def test_injected_clock(keys):
    v = JwksVerifier(keys.fetch, ISS, clock=lambda: time.time() + 7200)
    assert await reason(v, es(keys.ec, keys.ec_kid, claims())) == "expired"


# ---------------------------------------------------------------- config

def test_config_needs_all_three_settings(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_DB_URL", "AGOS_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    assert AccountsConfig.from_env() is None
    monkeypatch.setenv("SUPABASE_URL", "https://p.supabase.co/")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "pub")
    assert AccountsConfig.from_env() is None  # no DB URL -> accounts stay off
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@h:5432/postgres")
    cfg = AccountsConfig.from_env()
    assert cfg.auth_url == "https://p.supabase.co/auth/v1" and cfg.public_url is None


def test_service_key_is_never_read(monkeypatch):
    import inspect
    from agos_v2 import accounts_api, db, gotrue
    for mod in (accounts_api, db, gotrue, auth):
        assert "SUPABASE_SERVICE_KEY" not in inspect.getsource(mod)


# ----------------------------------------------------------- error mapping

class _Diag:
    def __init__(self, m):
        self.message_primary = m


class _PgErr(Exception):
    def __init__(self, state, msg="m"):
        self.sqlstate, self.diag = state, _Diag(msg)


@pytest.mark.parametrize("state", sorted(SQLSTATE_HTTP))
def test_agos_sqlstates_map_to_http(state):
    e = _db_error(_PgErr(state, "workspace not found"))
    assert e.status_code == SQLSTATE_HTTP[state] and e.detail == "workspace not found"


def test_other_db_errors_hide_details(caplog):
    e = _db_error(_PgErr("42501", "permission denied for table secret_stuff"))
    assert e.status_code == 503 and "secret_stuff" not in e.detail
    assert "secret_stuff" not in caplog.text
    assert _db_error(_PgErr("23505", "dup key (email)=(x@y)")).detail == "invalid input"
    assert _db_error(_PgErr("57014")).status_code == 503


# ------------------------------------------------------------------ router

def _app(svc=None):
    app = FastAPI()
    app.include_router(build_router(lambda: svc))
    return TestClient(app, base_url="https://testserver")


def test_unconfigured_router_answers_503_everywhere():
    c = _app()
    assert c.get("/api/v2/auth/config").json()["configured"] is False
    ws = str(uuid.uuid4())
    for method, path in [("get", "/api/v2/me"), ("get", "/api/v2/workspaces"),
                         ("post", "/api/v2/auth/refresh"), ("post", "/api/v2/auth/logout"),
                         ("get", f"/api/v2/workspaces/{ws}/members")]:
        assert getattr(c, method)(path).status_code == 503, path


def test_v2_routes_are_registered_in_server():
    import server
    paths = set(server.app.openapi()["paths"])
    assert "/api/v2/me" in paths and "/api/v2/auth/login" in paths
    assert "/api/v2/invitations/accept" in paths
