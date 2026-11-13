"""R-204 (D-153): verification of Supabase Auth access tokens.

The web server trusts a user ONLY through a Supabase Auth access token that it
verifies itself:

* signature: asymmetric keys from the project's JWKS
  (``<SUPABASE_URL>/auth/v1/.well-known/jwks.json``) — ES256 (the live project
  uses it) or RS256. No shared JWT secret is needed or accepted: ``alg`` must be
  one of the asymmetric algorithms AND match the key type, so ``none`` and the
  HS256-with-public-key confusion are rejected;
* claims: ``iss`` = ``<SUPABASE_URL>/auth/v1``, ``aud`` contains
  ``authenticated``, ``role`` = ``authenticated``, ``exp`` in the future (small
  leeway), ``nbf``/``iat`` not in the future, ``sub`` and ``session_id`` are UUIDs;
* a valid signature is NOT the end: the session must still be alive in the
  database (``agos.session_is_active()`` — logout deletes it). That check runs
  inside the request transaction (``agos_v2/db.py``).

Nothing here logs or returns a token. Errors carry a fixed reason code only.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ALLOWED_ALGS = {"ES256": "EC", "RS256": "RSA"}
LEEWAY_S = 5
MAX_TOKEN_LEN = 8192
JWKS_MIN_REFRESH_S = 30.0   # an unknown kid refetches the JWKS at most this often
JWKS_MAX_AGE_S = 600.0      # keys are re-read at least this often (key rotation)


class TokenError(Exception):
    """Invalid access token. ``reason`` is a fixed code — never token content."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    user_id: str
    session_id: str
    email: Optional[str]
    claims: Dict[str, Any] = field(repr=False)


def _b64url(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, TypeError):
        raise TokenError("malformed") from None


def _is_uuid(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    try:
        uuid.UUID(v)
    except ValueError:
        return False
    return True


def _public_key(jwk: Dict[str, Any]):
    kty = jwk.get("kty")
    if kty == "EC":
        if jwk.get("crv") != "P-256":
            raise TokenError("unsupported_key")
        x = int.from_bytes(_b64url(jwk["x"]), "big")
        y = int.from_bytes(_b64url(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    if kty == "RSA":
        n = int.from_bytes(_b64url(jwk["n"]), "big")
        e = int.from_bytes(_b64url(jwk["e"]), "big")
        return rsa.RSAPublicNumbers(e, n).public_key()
    raise TokenError("unsupported_key")


def _verify_signature(alg: str, key, signing_input: bytes, sig: bytes) -> None:
    try:
        if alg == "ES256":
            if len(sig) != 64:
                raise TokenError("bad_signature")
            der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        elif alg == "RS256":
            key.verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
        else:  # pragma: no cover - guarded by ALLOWED_ALGS
            raise TokenError("bad_alg")
    except InvalidSignature:
        raise TokenError("bad_signature") from None


JwksFetcher = Callable[[], Awaitable[Dict[str, Any]]]


class JwksVerifier:
    """Verifies access tokens against a JWKS; caches keys, refetches on an
    unknown ``kid`` (bounded) and periodically (rotation)."""

    def __init__(self, fetch_jwks: JwksFetcher, issuer: str, audience: str = "authenticated",
                 clock: Callable[[], float] = time.time):
        self._fetch = fetch_jwks
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._clock = clock
        self._keys: Dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self, force: bool) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._keys and not force and now - self._fetched_at < JWKS_MAX_AGE_S:
                return
            if self._keys and force and now - self._fetched_at < JWKS_MIN_REFRESH_S:
                return
            doc = await self._fetch()
            keys: Dict[str, Any] = {}
            for jwk in (doc or {}).get("keys", []):
                kid = jwk.get("kid")
                alg = jwk.get("alg")
                if not kid or alg not in ALLOWED_ALGS or ALLOWED_ALGS[alg] != jwk.get("kty"):
                    continue
                try:
                    keys[kid] = (alg, _public_key(jwk))
                except (TokenError, KeyError, ValueError):
                    continue
            self._keys = keys
            self._fetched_at = now

    async def _key_for(self, kid: str):
        if time.monotonic() - self._fetched_at >= JWKS_MAX_AGE_S or not self._keys:
            await self._refresh(force=False)
        if kid not in self._keys:
            await self._refresh(force=True)
        return self._keys.get(kid)

    async def verify(self, token: Optional[str]) -> Identity:
        if not token or not isinstance(token, str):
            raise TokenError("missing")
        if len(token) > MAX_TOKEN_LEN or token.count(".") != 2:
            raise TokenError("malformed")
        h64, p64, s64 = token.split(".")
        try:
            header = json.loads(_b64url(h64))
            claims = json.loads(_b64url(p64))
        except (ValueError, UnicodeDecodeError):
            raise TokenError("malformed") from None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise TokenError("malformed")
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGS:
            raise TokenError("bad_alg")
        if not isinstance(kid, str) or not kid:
            raise TokenError("unknown_key")
        entry = await self._key_for(kid)
        if entry is None:
            raise TokenError("unknown_key")
        key_alg, key = entry
        if key_alg != alg:
            raise TokenError("bad_alg")
        _verify_signature(alg, key, f"{h64}.{p64}".encode("ascii"), _b64url(s64))
        return self._check_claims(claims)

    def _check_claims(self, c: Dict[str, Any]) -> Identity:
        now = self._clock()
        exp, nbf, iat = c.get("exp"), c.get("nbf"), c.get("iat")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            raise TokenError("no_expiry")
        if now >= exp + LEEWAY_S:
            raise TokenError("expired")
        if isinstance(nbf, (int, float)) and now + LEEWAY_S < nbf:
            raise TokenError("not_yet_valid")
        if isinstance(iat, (int, float)) and now + LEEWAY_S < iat:
            raise TokenError("not_yet_valid")
        if str(c.get("iss", "")).rstrip("/") != self.issuer:
            raise TokenError("bad_issuer")
        aud = c.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if self.audience not in auds:
            raise TokenError("bad_audience")
        if c.get("role") != "authenticated":
            raise TokenError("bad_role")
        if c.get("is_anonymous") is True:
            raise TokenError("anonymous")
        sub, sid = c.get("sub"), c.get("session_id")
        if not _is_uuid(sub):
            raise TokenError("bad_subject")
        if not _is_uuid(sid):
            raise TokenError("no_session")
        email = c.get("email") if isinstance(c.get("email"), str) else None
        return Identity(user_id=str(uuid.UUID(sub)), session_id=str(uuid.UUID(sid)),
                        email=email, claims=c)
