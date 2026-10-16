"""SEC-02 (R-138, D-056): tenant identity and credentials.

The server is multi-tenant. Every request that reads or changes tenant data
(tasks, memory, learning, events, the live event stream) must carry an API key
that the server maps to a ``Principal`` (tenant id + role). The tenant id comes
ONLY from that server-side mapping — never from a request parameter, header or
body field.

Configuration (environment, read once at startup):

* ``AGOS_TENANT_KEYS`` — JSON list of credentials::

      [{"tenant": "acme", "key_sha256": "<64 hex>", "role": "user"},
       {"tenant": "ops",  "key_sha256": "<64 hex>", "role": "admin"}]

  Only the SHA-256 of each key is configured, so the secret itself is not in
  the server environment. A tenant may have several entries (key rotation: add
  the new key, deploy, remove the old one). ``role`` is ``user`` (default) or
  ``admin``. Any malformed value makes startup fail (``CredentialsError``) —
  the server never falls back to an unprotected mode because the
  configuration could not be read.
* ``AGOS_API_KEY`` (pre-SEC-02 single key, kept for compatibility) — the
  operator key: principal ``tenant="operator", role="admin"``.

With neither set there are no principals: every tenant-data route answers 401
("authentication is not configured"). There is no default identity.

The ``admin`` role may read across tenants, but only when a request asks for it
explicitly (``all_tenants=true``); by default an admin is scoped to its own
tenant like any user.

The tenant of the code that is running now is carried in a ``ContextVar``:
request handlers set it from the principal; ``asyncio.create_task`` copies it
into background work, so a task started for tenant A keeps running as A. Code
running with no tenant (``None``) gets no memory recall and stores nothing.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional

TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROLES = ("user", "admin")
LEGACY_TENANT = "operator"


class _AllTenants:
    """Explicit admin scope: no tenant filter. Never the default of anything."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "ALL_TENANTS"


ALL_TENANTS = _AllTenants()


class CredentialsError(RuntimeError):
    """Invalid authentication configuration: the server must not start."""


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_credentials(environ=None) -> Dict[str, Principal]:
    """Parse the credential configuration; raise ``CredentialsError`` on any defect."""
    env = os.environ if environ is None else environ
    creds: Dict[str, Principal] = {}
    raw = (env.get("AGOS_TENANT_KEYS") or "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError:
            raise CredentialsError("AGOS_TENANT_KEYS is not valid JSON") from None
        if not isinstance(entries, list) or not entries:
            raise CredentialsError("AGOS_TENANT_KEYS must be a non-empty JSON list")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}] must be an object")
            unknown = set(entry) - {"tenant", "key_sha256", "role"}
            if unknown:
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}] has unknown fields: {sorted(unknown)}")
            tenant = entry.get("tenant")
            digest = entry.get("key_sha256")
            role = entry.get("role", "user")
            if not isinstance(tenant, str) or not TENANT_ID_RE.match(tenant):
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}].tenant must match {TENANT_ID_RE.pattern}")
            if not isinstance(digest, str) or not _SHA256_RE.match(digest):
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}].key_sha256 must be 64 lowercase hex chars")
            if role not in ROLES:
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}].role must be one of {ROLES}")
            if digest in creds:
                raise CredentialsError(f"AGOS_TENANT_KEYS[{i}] repeats a key already configured")
            creds[digest] = Principal(tenant, role)
    legacy = env.get("AGOS_API_KEY") or ""
    if legacy:
        digest = key_digest(legacy)
        if digest in creds:
            raise CredentialsError("AGOS_API_KEY is also listed in AGOS_TENANT_KEYS")
        creds[digest] = Principal(LEGACY_TENANT, "admin")
    return creds


def authenticate(api_key: Optional[str], creds: Dict[str, Principal]) -> Optional[Principal]:
    """The principal of ``api_key``, or None. Compares every digest in constant time."""
    if not api_key or not creds:
        return None
    presented = key_digest(api_key)
    found = None
    for digest, principal in creds.items():
        if hmac.compare_digest(digest, presented):
            found = principal
    return found


# ---------------------------------------------------------------- current tenant

_CURRENT: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agos_tenant", default=None)


def current_tenant() -> Optional[str]:
    return _CURRENT.get()


def set_tenant(tenant_id: Optional[str]) -> contextvars.Token:
    if tenant_id is not None and not TENANT_ID_RE.match(tenant_id):
        raise ValueError("invalid tenant id")
    return _CURRENT.set(tenant_id)


def reset_tenant(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def check_scope(scope) -> None:
    """Data-access guard: a scope is a valid tenant id or ALL_TENANTS, nothing else."""
    if scope is ALL_TENANTS:
        return
    if not isinstance(scope, str) or not TENANT_ID_RE.match(scope):
        raise ValueError("tenant scope required")
