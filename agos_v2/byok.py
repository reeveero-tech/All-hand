"""R-205 (D-154): the user's own AI provider keys (BYOK) — vault, key test, routes.

Encryption — envelope, on the R-189 cipher (``secret_store.seal/unseal``,
AES-256-GCM), no new crypto:

* every key gets a fresh random 32-byte data key (DEK); the user's key is
  encrypted with the DEK, with an AAD that binds it to
  ``(key id, workspace, user, provider)`` — a ciphertext copied to another
  row, user or workspace does not decrypt (fail-closed);
* the DEK is encrypted with the server's key-encryption key (KEK) from
  ``AGOS_BYOK_KEK`` (base64 of exactly 32 bytes; a Render secret). The KEK
  never leaves the server process; the database only ever receives
  ciphertext. ``kek_id`` (a fingerprint, not the key) detects a changed KEK
  and refuses instead of returning garbage.

Why not the R-189 SQLite store itself: it keeps its data and master key on
the local disk; the production host (Render free plan) has no persistent disk
and v2 data lives in Postgres (D-151). So R-205 reuses the R-189 cipher and
its fail-closed rules, and stores the ciphertext next to the workspace data
(docs/sql/v2/004_byok.sql) — one store, no duplicate table.

What never happens (each pinned by tests/test_r205_byok_*.py):
the full key in a response, an error, a log line, a database row/parameter,
the browser's storage, or a diagnostic output. Only ``last4`` is shown.

Key test: one GET to a fixed, free endpoint of the provider (model list /
key info — no generation, no credit spent). The answer is a reason code and
the HTTP status only; the provider's response body is never read into the
result (some providers echo part of the key in error bodies). The auth
header is "unredirected" (urllib drops it on a redirect) and any redirect
is reported as ``unexpected_redirect``. Outbound policy is the SEC-05 guard
(``safety.safe_urlopen``): a private/loopback address — e.g. a local Ollama
URL seen from the cloud server — is refused as ``blocked_address`` unless
the operator enabled private networks.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import decimal
import hashlib
import http.client
import logging
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_civilization.core import safety
from agent_civilization.core.secret_store import (MasterKeyError, SecretStoreError,
                                                  check_master_key, seal, unseal)

logger = logging.getLogger("agos.v2.byok")

KEK_ENV = "AGOS_BYOK_KEK"
SECRET_MIN, SECRET_MAX = 8, 512
TEST_TIMEOUT_S = 10.0
TEST_LIMIT, TEST_WINDOW_S = 10, 60.0  # key tests per user per minute (abuse cap)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    test_url: Optional[str]          # None = the user's own base URL (Ollama)
    auth: str                         # bearer | x-api-key | x-goog-api-key
    needs_key: bool = True
    extra_headers: tuple = ()


PROVIDERS: Dict[str, Provider] = {p.id: p for p in (
    Provider("openai", "OpenAI", "https://api.openai.com/v1/models", "bearer"),
    Provider("anthropic", "Anthropic", "https://api.anthropic.com/v1/models", "x-api-key",
             extra_headers=(("anthropic-version", "2023-06-01"),)),
    Provider("google", "Google Gemini",
             "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", "x-goog-api-key"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1/key", "bearer"),
    Provider("mistral", "Mistral", "https://api.mistral.ai/v1/models", "bearer"),
    Provider("groq", "Groq", "https://api.groq.com/openai/v1/models", "bearer"),
    Provider("ollama", "Ollama (local URL)", None, "bearer", needs_key=False),
)}

REASONS = ("ok", "invalid_key", "forbidden", "endpoint_not_found", "rate_limited",
           "provider_unavailable", "unexpected_status", "unexpected_redirect", "timeout",
           "unreachable", "blocked_address", "tls_error", "test_failed")


class VaultConfigError(MasterKeyError):
    """AGOS_BYOK_KEK is set but is not base64 of exactly 32 bytes (boot refuses)."""


class VaultKeyMismatch(SecretStoreError):
    """The stored key was sealed under a different KEK (kek_id differs)."""


# ------------------------------------------------------------------ input

class InvalidInput(ValueError):
    """A client error whose message never contains the submitted value."""


def validate_secret(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidInput("api_key must be a string")
    if not SECRET_MIN <= len(value) <= SECRET_MAX:
        raise InvalidInput(f"api_key must be {SECRET_MIN}-{SECRET_MAX} characters")
    if any(not 0x21 <= ord(ch) <= 0x7E for ch in value):
        raise InvalidInput("api_key may contain only visible ASCII characters (no spaces)")
    return value


def validate_base_url(value: Any) -> str:
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 300:
        raise InvalidInput("base_url must be an http(s) URL of 8-300 characters")
    url = value.strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidInput("base_url must be an http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise InvalidInput("base_url must not contain credentials")
    if parts.query or parts.fragment:
        raise InvalidInput("base_url must not contain a query or fragment")
    return url


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyIn(_Strict):
    provider: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=80)
    api_key: Optional[Any] = None
    base_url: Optional[Any] = None


class KeyPatch(_Strict):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    api_key: Optional[Any] = None
    base_url: Optional[Any] = None


async def parse_body(request: Request, model):
    """Validate a JSON body WITHOUT FastAPI's default 422, which echoes the
    submitted input (the key) back in ``detail[].input``."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - any undecodable body
        raise HTTPException(422, "body must be a JSON object") from None
    if not isinstance(data, dict):
        raise HTTPException(422, "body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        safe = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "invalid"), "type": e.get("type")}
                for e in exc.errors(include_url=False, include_input=False, include_context=False)]
        raise HTTPException(422, safe) from None


# ------------------------------------------------------------------ vault

def _aad(key_id: str, workspace_id: str, user_id: str, provider: str) -> bytes:
    return f"agos-byok-v1\x00{key_id}\x00{workspace_id}\x00{user_id}\x00{provider}".encode()


class KeyVault:
    """Envelope encryption of user keys under the server's KEK."""

    def __init__(self, kek: bytes):
        self._kek = check_master_key(kek)
        self.kek_id = hashlib.sha256(b"agos-byok-kek-id\x00" + self._kek).hexdigest()[:16]

    def __repr__(self) -> str:  # never the key
        return f"KeyVault(kek_id={self.kek_id})"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> Optional["KeyVault"]:
        # the process read uses os.environ.get with the literal name (== KEK_ENV)
        # so the entrypoint map (R-156) lists it; `env` is for tests only
        raw = os.environ.get("AGOS_BYOK_KEK") if env is None else env.get(KEK_ENV)
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            kek = base64.b64decode(raw.replace("-", "+").replace("_", "/") + "=" * (-len(raw) % 4),
                                   validate=True)
        except (binascii.Error, ValueError):
            raise VaultConfigError(f"{KEK_ENV} must be base64 of exactly 32 bytes") from None
        if len(kek) != 32:
            raise VaultConfigError(f"{KEK_ENV} must be base64 of exactly 32 bytes (got {len(kek)})")
        return cls(kek)

    def _dek_aad(self, key_id: str) -> bytes:
        return f"agos-byok-dek-v1\x00{self.kek_id}\x00{key_id}".encode()

    def seal(self, *, key_id: str, workspace_id: str, user_id: str, provider: str,
             secret: str) -> Dict[str, Any]:
        dek = os.urandom(32)
        key_nonce, key_ct = seal(dek, secret.encode("ascii"), _aad(key_id, workspace_id, user_id, provider))
        dek_nonce, dek_wrapped = seal(self._kek, dek, self._dek_aad(key_id))
        return {"last4": secret[-4:], "key_ct": key_ct, "key_nonce": key_nonce,
                "dek_wrapped": dek_wrapped, "dek_nonce": dek_nonce, "kek_id": self.kek_id}

    def open(self, row: Mapping[str, Any]) -> str:
        if row.get("kek_id") != self.kek_id:
            raise VaultKeyMismatch("stored key was sealed under a different vault key")
        key_id = str(row["id"])
        dek = unseal(self._kek, bytes(row["dek_nonce"]), bytes(row["dek_wrapped"]),
                     self._dek_aad(key_id), what="provider key data key")
        pt = unseal(dek, bytes(row["key_nonce"]), bytes(row["key_ct"]),
                    _aad(key_id, str(row["workspace_id"]), str(row["user_id"]), row["provider"]),
                    what="provider key")
        return pt.decode("ascii")


# --------------------------------------------------------------- key test

@dataclass(frozen=True)
class TestResult:
    ok: bool
    reason: str
    http_status: Optional[int] = None

    __test__ = False  # not a pytest class


def _reason_for_status(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    return {401: "invalid_key", 403: "forbidden", 404: "endpoint_not_found",
            429: "rate_limited"}.get(status, "provider_unavailable" if status >= 500 else "unexpected_status")


class KeyTester:
    """One free GET per test; the result is a reason code, never a body."""

    def __init__(self, endpoints: Optional[Mapping[str, str]] = None,
                 allow_private: Optional[bool] = None, timeout: float = TEST_TIMEOUT_S,
                 opener: Callable = safety.safe_urlopen):
        self._endpoints = dict(endpoints or {})
        self._allow_private = allow_private
        self._timeout = timeout
        self._opener = opener

    def url_for(self, provider: Provider, base_url: Optional[str]) -> str:
        if provider.id in self._endpoints:
            return self._endpoints[provider.id]
        if provider.test_url is None:
            return f"{base_url}/api/tags"
        return provider.test_url

    def _run(self, provider: Provider, secret: Optional[str], base_url: Optional[str]) -> TestResult:
        url = self.url_for(provider, base_url)
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "AGOS-BYOK-KeyTest/1.0",
                                              "Accept": "application/json"})
        for name, value in provider.extra_headers:
            req.add_header(name, value)
        if secret:
            name, value = {"bearer": ("Authorization", f"Bearer {secret}"),
                           "x-api-key": ("x-api-key", secret),
                           "x-goog-api-key": ("x-goog-api-key", secret)}[provider.auth]
            req.add_unredirected_header(name, value)  # never forwarded on a redirect
        try:
            with self._opener(req, timeout=self._timeout, allow_private=self._allow_private) as resp:
                status = int(getattr(resp, "status", 0) or resp.getcode())
                final = resp.geturl()
            if final != url:
                return TestResult(False, "unexpected_redirect", status)
            reason = _reason_for_status(status)
            return TestResult(reason == "ok", reason, status)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            exc.close()  # the body is never read
            if 300 <= status < 400:
                return TestResult(False, "unexpected_redirect", status)
            return TestResult(False, _reason_for_status(status), status)
        except safety.UnsafeURLError:
            return TestResult(False, "blocked_address")
        except (socket.timeout, TimeoutError):
            return TestResult(False, "timeout")
        except urllib.error.URLError as exc:
            inner = exc.reason
            if isinstance(inner, (socket.timeout, TimeoutError)):
                return TestResult(False, "timeout")
            if isinstance(inner, safety.UnsafeURLError):
                return TestResult(False, "blocked_address")
            if isinstance(inner, ssl.SSLError):
                return TestResult(False, "tls_error")
            return TestResult(False, "unreachable")
        except ssl.SSLError:
            return TestResult(False, "tls_error")
        except (OSError, ValueError, http.client.HTTPException):
            return TestResult(False, "unreachable")
        except Exception:  # noqa: BLE001 - never surface a message that may hold input
            return TestResult(False, "test_failed")

    async def test(self, provider: Provider, secret: Optional[str], base_url: Optional[str]) -> TestResult:
        result = await asyncio.to_thread(self._run, provider, secret, base_url)
        logger.info("byok key test provider=%s ok=%s reason=%s http=%s",
                    provider.id, result.ok, result.reason, result.http_status)
        return result


class TestLimiter:
    """In-process sliding window: TEST_LIMIT key tests per user per window."""

    def __init__(self, limit: int = TEST_LIMIT, window_s: float = TEST_WINDOW_S):
        self.limit, self.window_s = limit, window_s
        self._hits: Dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(user_id, collections.deque())
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    __test__ = False


# ----------------------------------------------------------------- routes

META_COLS = ("id, workspace_id, provider, label, base_url, status, last4, created_at, updated_at, "
             "revoked_at, last_test_at, last_test_ok, last_test_reason, use_count, last_used_at, "
             "tokens_in, tokens_out, cost_usd")


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, decimal.Decimal):
            v = format(v, "f")
        out[k] = v
    out["has_secret"] = out.get("status") == "active" and out.get("last4") is not None
    return out


def providers_public() -> list:
    return [{"id": p.id, "name": p.name, "needs_key": p.needs_key, "needs_url": p.test_url is None}
            for p in PROVIDERS.values()]


def add_byok_routes(r, *, svc, run, fetchall, fetchone, uuid_of) -> None:
    """Mount the BYOK routes on the v2 accounts router ``r`` (same identity,
    session check, RLS transaction, rate limit and no-store rules).

    They are declared on a router of their own with the literal prefix so the
    static route inventories (scripts/build_route_inventory.py,
    scripts/build_entrypoint_map.py) read the real paths; the finished routes
    (full path, same route class) are then appended to ``r`` as they are —
    ``r.include_router`` would prepend ``r``'s prefix a second time."""
    b = APIRouter(prefix="/api/v2", tags=["v2 provider keys"], route_class=r.route_class)
    if r.prefix != b.prefix:
        raise RuntimeError("the BYOK routes must live under the accounts router prefix")

    def vault_of(s) -> KeyVault:
        v = getattr(s, "vault", None)
        if v is None:
            raise HTTPException(503, f"key vault is not configured on this server ({KEK_ENV})")
        return v

    def provider_of(pid: Any) -> Provider:
        p = PROVIDERS.get(pid) if isinstance(pid, str) else None
        if p is None:
            raise HTTPException(422, "unknown provider")
        return p

    async def own_meta(conn, ws: str, kid: str) -> Dict[str, Any]:
        row = await fetchone(conn, f"select {META_COLS}, user_id from agos.provider_keys "
                                   "where id = %s and workspace_id = %s", (kid, ws))
        if row is None:
            raise HTTPException(404, "key not found")
        return row

    def opened(vault: KeyVault, row: Dict[str, Any]) -> Optional[str]:
        if row["key_ct"] is None:
            return None
        try:
            return vault.open(row)
        except VaultKeyMismatch:
            logger.error("byok: key %s sealed under another vault key", row["id"])
            raise HTTPException(409, "this key was saved under a different server vault key; "
                                     "enter the key again") from None
        except SecretStoreError:
            logger.error("byok: key %s failed integrity check", row["id"])
            raise HTTPException(409, "stored key failed its integrity check; enter the key again") from None

    @b.get("/provider-keys/providers")
    async def byok_providers():
        return providers_public()

    @b.get("/workspaces/{workspace_id}/provider-keys")
    async def list_keys(request: Request, workspace_id: str):
        ws = uuid_of(workspace_id, "workspace")
        vault_of(svc())

        async def q(conn, ident):
            if (await fetchone(conn, "select agos.member_role(%s) as r", (ws,)))["r"] is None:
                raise HTTPException(404, "workspace not found")
            rows = await fetchall(conn, f"select {META_COLS} from agos.provider_keys "
                                        "where workspace_id = %s order by created_at, id", (ws,))
            return [_meta(x) for x in rows]
        return await run(request, q)

    @b.post("/workspaces/{workspace_id}/provider-keys", status_code=201)
    async def create_key(request: Request, workspace_id: str):
        ws = uuid_of(workspace_id, "workspace")
        vault = vault_of(svc())
        body = await parse_body(request, KeyIn)
        p = provider_of(body.provider)
        try:
            secret = validate_secret(body.api_key) if (p.needs_key or body.api_key is not None) else None
            base_url = validate_base_url(body.base_url) if p.test_url is None else None
            if p.test_url is not None and body.base_url is not None:
                raise InvalidInput("base_url is only for ollama")
        except InvalidInput as e:
            raise HTTPException(422, str(e)) from None
        kid = str(uuid.uuid4())

        async def q(conn, ident):
            m = (vault.seal(key_id=kid, workspace_id=ws, user_id=ident.user_id, provider=p.id, secret=secret)
                 if secret else dict.fromkeys(("last4", "key_ct", "key_nonce", "dek_wrapped",
                                               "dek_nonce", "kek_id")))
            await conn.execute("select agos.byok_put_key(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (kid, ws, p.id, body.label.strip(), base_url, m["last4"], m["key_ct"],
                                m["key_nonce"], m["dek_wrapped"], m["dek_nonce"], m["kek_id"]))
            return _meta(await fetchone(conn, f"select {META_COLS} from agos.provider_keys where id = %s",
                                        (kid,)))
        return await run(request, q)

    @b.patch("/workspaces/{workspace_id}/provider-keys/{key_id}")
    async def edit_key(request: Request, workspace_id: str, key_id: str):
        ws, kid = uuid_of(workspace_id, "workspace"), uuid_of(key_id, "key")
        vault = vault_of(svc())
        body = await parse_body(request, KeyPatch)

        async def q(conn, ident):
            cur = await own_meta(conn, ws, kid)
            p = PROVIDERS[cur["provider"]]
            try:
                secret = validate_secret(body.api_key) if body.api_key is not None else None
                base_url = validate_base_url(body.base_url) if body.base_url is not None else None
                if base_url is not None and p.test_url is not None:
                    raise InvalidInput("base_url is only for ollama")
            except InvalidInput as e:
                raise HTTPException(422, str(e)) from None
            m = (vault.seal(key_id=kid, workspace_id=ws, user_id=ident.user_id, provider=p.id, secret=secret)
                 if secret else dict.fromkeys(("last4", "key_ct", "key_nonce", "dek_wrapped",
                                               "dek_nonce", "kek_id")))
            await conn.execute("select agos.byok_update_key(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (kid, body.label.strip() if body.label else None, base_url, secret is not None,
                                m["last4"], m["key_ct"], m["key_nonce"], m["dek_wrapped"], m["dek_nonce"],
                                m["kek_id"]))
            return _meta(await fetchone(conn, f"select {META_COLS} from agos.provider_keys where id = %s",
                                        (kid,)))
        return await run(request, q)

    @b.post("/workspaces/{workspace_id}/provider-keys/{key_id}/revoke")
    async def revoke_key(request: Request, workspace_id: str, key_id: str):
        ws, kid = uuid_of(workspace_id, "workspace"), uuid_of(key_id, "key")
        vault_of(svc())

        async def q(conn, ident):
            await own_meta(conn, ws, kid)
            await conn.execute("select agos.byok_revoke_key(%s)", (kid,))
            return _meta(await fetchone(conn, f"select {META_COLS} from agos.provider_keys where id = %s",
                                        (kid,)))
        return await run(request, q)

    @b.delete("/workspaces/{workspace_id}/provider-keys/{key_id}", status_code=204)
    async def delete_key(request: Request, workspace_id: str, key_id: str):
        ws, kid = uuid_of(workspace_id, "workspace"), uuid_of(key_id, "key")
        vault_of(svc())

        async def q(conn, ident):
            await own_meta(conn, ws, kid)
            await conn.execute("select agos.byok_delete_key(%s)", (kid,))
            return Response(status_code=204)
        return await run(request, q)

    @b.post("/workspaces/{workspace_id}/provider-keys/{key_id}/test")
    async def test_key(request: Request, workspace_id: str, key_id: str):
        ws, kid = uuid_of(workspace_id, "workspace"), uuid_of(key_id, "key")
        s = svc()
        vault = vault_of(s)
        limiter: TestLimiter = s.byok_test_limiter

        async def load(conn, ident):
            await own_meta(conn, ws, kid)
            if not limiter.allow(ident.user_id):
                raise HTTPException(429, "too many key tests; wait a minute", headers={"Retry-After": "60"})
            row = await fetchone(conn, "select * from agos.byok_key_material(%s, 'test')", (kid,))
            return row["provider"], row["base_url"], opened(vault, row)

        provider_id, base_url, secret = await run(request, load)
        result = await s.key_tester.test(PROVIDERS[provider_id], secret, base_url)
        del secret

        async def record(conn, ident):
            await conn.execute("select agos.byok_record_test(%s, %s, %s)", (kid, result.ok, result.reason))
            return await fetchone(conn, "select last_test_at from agos.provider_keys where id = %s", (kid,))

        row = await run(request, record)
        return {"ok": result.ok, "reason": result.reason, "http_status": result.http_status,
                "tested_at": row["last_test_at"].isoformat() if row else None}

    r.routes.extend(b.routes)


async def resolve_key(conn, vault: KeyVault, key_id: str, purpose: str = "use"):
    """For the model gateway (R-206): the caller's active key, decrypted, inside
    the caller's ``user_tx``. Revoked/deleted/foreign keys raise (AG410/AG404)
    at this call — there is no cache, so revoke/delete stop the next use."""
    cur = await conn.execute("select * from agos.byok_key_material(%s, %s)", (key_id, purpose))
    cols = [d.name for d in cur.description]
    row = dict(zip(cols, await cur.fetchone()))
    secret = vault.open(row) if row["key_ct"] is not None else None
    return PROVIDERS[row["provider"]], row["base_url"], secret
