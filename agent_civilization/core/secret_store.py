"""R-189: encrypted per-tenant secret storage with a full lifecycle.

The accomplished base (R-100) hardened the process and sanitized outputs;
this module adds what that base does not cover: STORAGE, ROTATION and
runtime ACCESS for secrets — a key per tenant (SEC-02) — with an explicit
lifecycle (create / rotate / revoke / refuse-when-absent).

Design (each point is load-bearing, see tests/test_r189_secret_store.py):

* **Authenticated encryption at rest** — AES-256-GCM (the ``cryptography``
  package, a new declared dependency for this batch). The AAD binds
  ``(tenant, name, version)`` to the ciphertext, so a ciphertext moved to
  another tenant or name fails to decrypt (fail-closed). Plaintext never
  enters the SQLite file, its WAL or its journal: values are encrypted
  BEFORE they are handed to SQLite.

* **Master key** — a 32-byte file with mode 0600, deliberately OUTSIDE the
  tracked source tree (the rate-limit-store precedent). The path comes from
  the non-secret configuration ``AGOS_SECRETS_MASTER_KEY_FILE`` (default
  ``<tmp>/agos_secrets_master_key``). Initialization is EXPLICIT
  (``initialize()``): it creates the key file and the schema. Normal
  operation REFUSES a missing, unreadable or wrong-length key
  (``MasterKeyError``) — the store never silently regenerates a key for an
  existing database (that would silently invalidate every stored secret).

* **Fail-closed everywhere** — a missing secret raises ``SecretNotFound``
  (never ``None``, never a default value); a corrupt ciphertext or an AAD
  mismatch raises ``SecretStoreError``; storage failures propagate. The
  store never falls back to a mode without the key.

* **No value ever reaches an output path** — public/inspection APIs return
  metadata only (names, versions, status). ``get_secret``/``rotate_secret``
  hand the value to the CALLER (trusted internal code that needs it for an
  authorized operation); what the caller does with it is the caller's
  contract. Exceptions carry the tenant/name, never the value. Logging the
  value is a caller-side violation this module cannot prevent — the no-leak
  tests pin this module's own paths (exceptions, list, file bytes).

* **No plaintext cache** — every access resolves from the store, so
  rotation and revocation are observed immediately by every process
  sharing the file (the price is a per-access SQLite round-trip; honest
  and documented).

Honest limits (documented, not solved here):

* Revoking a stored secret does NOT revoke it at the external provider it
  authenticates to (store revocation != provider revocation).
* Previously fetched plaintext and calls already started with it cannot be
  recalled.
* The master key file protects at-rest bytes only within the host's trust
  boundary (root/file-system access reads it); it is not multi-host
  distributed access (a documented R-189 remainder).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _secrets
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TENANT_ID_RE_OK = __import__("re").compile(r"^[A-Za-z0-9_-]{1,64}$")
SECRET_NAME_RE_OK = __import__("re").compile(r"^[a-z0-9_]{1,64}$")
API_KEY_SECRET_NAME = "api_key"
_MASTER_KEY_LEN = 32  # AES-256
_NONCE_LEN = 12
_VALUE_MAX = 4096  # secrets are keys/tokens, not documents


class SecretStoreError(RuntimeError):
    """Base — storage/cipher failures (fail-closed, never a silent pass)."""


class MasterKeyError(SecretStoreError):
    """The master key is missing/unreadable/wrong — the store refuses to run."""


class SecretNotFound(SecretStoreError):
    """A lifecycle operation referenced a secret that does not exist."""


def default_master_key_file() -> str:
    """The default master-key location — OUTSIDE the tracked source tree."""
    return os.environ.get("AGOS_SECRETS_MASTER_KEY_FILE") or os.path.join(
        tempfile.gettempdir(), "agos_secrets_master_key")


class SecretStore:
    """SQLite-backed encrypted secret store (one instance per process; the
    file is the shared state — same pattern as the rate-limit store)."""

    def __init__(self, db_path: str, master_key_file: str,
                 busy_timeout_s: float = 2.0) -> None:
        self._db_path = db_path
        self._key_file = master_key_file
        self._busy_timeout_ms = max(1, int(busy_timeout_s * 1000))

    # ------------------------------------------------------------------ key
    def _load_master_key(self) -> bytes:
        """Load the master key; refuse anything unreadable or wrong-sized."""
        try:
            with open(self._key_file, "rb") as fh:
                key = fh.read()
        except OSError as exc:
            raise MasterKeyError(
                f"secrets master key file unreadable: {type(exc).__name__} "
                f"(path is configuration; create it explicitly via initialize())"
            ) from None
        if len(key) != _MASTER_KEY_LEN:
            raise MasterKeyError(
                "secrets master key file must hold exactly "
                f"{_MASTER_KEY_LEN} bytes (got {len(key)})")
        return key

    def _encrypt(self, key: bytes, tenant: str, name: str, version: int,
                 value: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(_NONCE_LEN)
        aad = self._aad(tenant, name, version)
        ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), aad)
        return nonce, ct

    def _decrypt(self, key: bytes, tenant: str, name: str, version: int,
                 nonce: bytes, ct: bytes) -> str:
        aad = self._aad(tenant, name, version)
        try:
            pt = AESGCM(key).decrypt(nonce, ct, aad)
        except Exception:  # InvalidTag and any cipher failure
            raise SecretStoreError(
                f"secret ciphertext integrity failure for '{name}' "
                f"(tenant/name/version mismatch or corruption) — refusing"
            ) from None
        return pt.decode("utf-8")

    @staticmethod
    def _aad(tenant: str, name: str, version: int) -> bytes:
        return f"{tenant}\x00{name}\x00{version}".encode("utf-8")

    # ------------------------------------------------------------------ db
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path, timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode=WAL")  # same-host cross-process pattern as the rate-limit store
        conn.row_factory = sqlite3.Row
        return conn

    def _schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS secrets ("
            " tenant TEXT NOT NULL, name TEXT NOT NULL,"
            " version INTEGER NOT NULL, nonce BLOB NOT NULL, value_ct BLOB NOT NULL,"
            " meta TEXT NOT NULL DEFAULT '{}',"
            " created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            " PRIMARY KEY (tenant, name))")
        # key-check marker: verifies the PRESENTED key against the store at
        # initialize() — a wrong (but well-formed) key is refused at startup,
        # not per-request; a populated store without a marker is refused too
        conn.execute(
            "CREATE TABLE IF NOT EXISTS store_key_check ("
            " k INTEGER PRIMARY KEY CHECK (k=1), nonce BLOB NOT NULL, ct BLOB NOT NULL)")

    _MARKER = b"agos-secret-store-key-check-v1"

    def initialize(self) -> None:
        """EXPLICIT initialization: create the master-key file (0600, random)
        and the schema, and VERIFY the key against the store (a wrong but
        well-formed key is refused HERE — at startup, not per-request). Never
        regenerates a key for an existing database."""
        key_exists = os.path.exists(self._key_file)
        db_exists = os.path.exists(self._db_path)
        if not key_exists and db_exists:
            # An existing store without its key is unusable BY DESIGN —
            # regenerating would silently invalidate every stored secret.
            raise MasterKeyError(
                "secrets DB exists but the master key file does not — "
                "the store refuses to regenerate a key for existing data")
        if not key_exists:
            fd = os.open(self._key_file,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, os.urandom(_MASTER_KEY_LEN))
            finally:
                os.close(fd)
        os.chmod(self._key_file, 0o600)  # idempotent hardening
        key = self._load_master_key()  # wrong length refused HERE
        with self._conn() as conn:
            self._schema(conn)
            self._key_check(conn, key)

    def _key_check(self, conn: sqlite3.Connection, key: bytes) -> None:
        """Verify the key against the store's marker (or write one for a
        fresh store). A populated store without a marker cannot be verified
        and is refused — fail-closed."""
        row = conn.execute(
            "SELECT nonce, ct FROM store_key_check WHERE k=1").fetchone()
        if row is not None:
            try:
                pt = AESGCM(key).decrypt(row["nonce"], row["ct"],
                                         b"store_key_check")
            except Exception:
                raise MasterKeyError(
                    "wrong master key for this secrets DB — refusing") from None
            if pt != self._MARKER:
                raise MasterKeyError(
                    "key-check marker mismatch — refusing")
            return
        have_secrets = conn.execute(
            "SELECT COUNT(*) FROM secrets").fetchone()[0]
        if have_secrets:
            raise MasterKeyError(
                "existing secrets DB lacks a key-check marker — the "
                "presented key cannot be verified against it; refusing")
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(key).encrypt(nonce, self._MARKER, b"store_key_check")
        conn.execute(
            "INSERT INTO store_key_check (k, nonce, ct) VALUES (1, ?, ?)",
            (nonce, ct))

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _check_tenant_name(tenant: str, name: str) -> None:
        if not isinstance(tenant, str) or not TENANT_ID_RE_OK.match(tenant):
            raise SecretStoreError("tenant must match [A-Za-z0-9_-]{1,64}")
        if not isinstance(name, str) or not SECRET_NAME_RE_OK.match(name):
            raise SecretStoreError("secret name must match [a-z0-9_]{1,64}")

    def _row(self, conn: sqlite3.Connection, tenant: str, name: str) -> Optional[sqlite3.Row]:
        cur = conn.execute(
            "SELECT version, nonce, value_ct, meta FROM secrets "
            "WHERE tenant=? AND name=?", (tenant, name))
        return cur.fetchone()

    # ----------------------------------------------------------- lifecycle
    def put_secret(self, tenant: str, name: str, value: str,
                   meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create or replace (rotate) a secret. Returns METADATA only — the
        value is never echoed. Replacing bumps the version; the previous
        value is invalid immediately (single row, new ciphertext)."""
        self._check_tenant_name(tenant, name)
        if not isinstance(value, str) or not value or len(value) > _VALUE_MAX:
            raise SecretStoreError(
                f"secret value for '{name}' must be a non-empty string "
                f"of at most {_VALUE_MAX} chars")
        meta_json = json.dumps(meta or {}, sort_keys=True)
        key = self._load_master_key()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, tenant, name)
            version = (row["version"] + 1) if row else 1
            nonce, ct = self._encrypt(key, tenant, name, version, value)
            conn.execute(
                "INSERT INTO secrets (tenant, name, version, nonce, value_ct, meta)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(tenant, name) DO UPDATE SET version=excluded.version,"
                " nonce=excluded.nonce, value_ct=excluded.value_ct,"
                " meta=excluded.meta, created_at=excluded.created_at",
                (tenant, name, version, nonce, ct, meta_json))
            conn.execute("COMMIT")
        return {"tenant": tenant, "name": name, "version": version}

    def get_secret(self, tenant: str, name: str) -> str:
        """The value for an authorized internal consumer — or an explicit
        refusal (never None, never a default)."""
        self._check_tenant_name(tenant, name)
        key = self._load_master_key()
        with self._conn() as conn:
            row = self._row(conn, tenant, name)
            if row is None:
                raise SecretNotFound(f"secret '{name}' not found")
            return self._decrypt(key, tenant, name, row["version"],
                                 row["nonce"], row["value_ct"])

    def rotate_secret(self, tenant: str, name: str,
                      new_value: Optional[str] = None) -> str:
        """Rotate: replace with a new value (random 32-byte url-safe token
        when none is given) and return the NEW value to the trusted caller.
        The rotation is ONE ``BEGIN IMMEDIATE`` transaction — existence
        check, version bump, re-encryption and update are atomic, so a
        revoke racing the rotation can never be overridden by a recreated
        row (whoever commits first wins; the other sees the final state).
        The secret's METADATA (e.g. role) is PRESERVED — rotation changes
        the value, not the identity. Store rotation does NOT revoke the
        old value at any external provider — that is the operator's
        separate action."""
        self._check_tenant_name(tenant, name)
        value = new_value or _secrets.token_urlsafe(_MASTER_KEY_LEN)
        if not isinstance(value, str) or not value or len(value) > _VALUE_MAX:
            raise SecretStoreError(
                f"secret value for '{name}' must be a non-empty string "
                f"of at most {_VALUE_MAX} chars")
        key = self._load_master_key()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, tenant, name)
            if row is None:
                conn.execute("ROLLBACK")
                raise SecretNotFound(f"secret '{name}' not found")
            version = row["version"] + 1
            nonce, ct = self._encrypt(key, tenant, name, version, value)
            conn.execute(
                "UPDATE secrets SET version=?, nonce=?, value_ct=?"
                " WHERE tenant=? AND name=?",
                (version, nonce, ct, tenant, name))
            conn.execute("COMMIT")
        return value

    def revoke_secret(self, tenant: str, name: str) -> None:
        """Revoke: remove the row — the secret stops resolving immediately.
        Revoking a secret that does not exist is an explicit error (no
        silent no-op), matching the fail-closed discipline."""
        self._check_tenant_name(tenant, name)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "DELETE FROM secrets WHERE tenant=? AND name=?", (tenant, name))
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise SecretNotFound(f"secret '{name}' not found")
            conn.execute("COMMIT")

    def list_secrets(self, tenant: str) -> List[Dict[str, Any]]:
        """Metadata only — names, versions, created_at, meta. NEVER values."""
        if not isinstance(tenant, str) or not TENANT_ID_RE_OK.match(tenant):
            raise SecretStoreError("tenant must match [A-Za-z0-9_-]{1,64}")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name, version, meta, created_at FROM secrets"
                " WHERE tenant=? ORDER BY name", (tenant,)).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"])
            except ValueError:
                meta = {}
            out.append({"name": r["name"], "version": r["version"],
                        "meta": meta, "created_at": r["created_at"]})
        return out

    # ---------------------------------------------------------------- auth
    def authenticate_key(self, presented: str) -> Optional[Dict[str, str]]:
        """Resolve a presented API key against every tenant's ``api_key``
        secret (constant-time compare). Returns ``{"tenant", "role"}`` or
        None — never the stored value. The tenant identity comes ONLY from
        this server-side resolution (SEC-02), never from the caller."""
        if not presented or len(presented) > _VALUE_MAX:
            return None
        key = self._load_master_key()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT tenant, version, nonce, value_ct, meta FROM secrets"
                " WHERE name=?", (API_KEY_SECRET_NAME,)).fetchall()
        for r in rows:
            try:
                stored = self._decrypt(key, r["tenant"], API_KEY_SECRET_NAME,
                                       r["version"], r["nonce"], r["value_ct"])
            except SecretStoreError:
                continue  # a corrupt row never authenticates anything
            if hmac.compare_digest(stored, presented):
                try:
                    meta = json.loads(r["meta"])
                except ValueError:
                    meta = {}
                role = meta.get("role") if isinstance(meta, dict) else None
                return {"tenant": r["tenant"],
                        "role": role if role in ("user", "admin") else "user"}
        return None


def digest_of(value: str) -> str:
    """A stable non-secret digest (for evidence/tests — never a revocation)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
