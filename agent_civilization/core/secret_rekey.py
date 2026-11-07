"""SEC-09 (D-137): OFFLINE master-key rotation for the R-189 secret store.

Re-encrypts every stored secret (and the key-check marker) from an OLD
master key to a NEW one inside ONE exclusive SQLite transaction, verifies
every row under the new key BEFORE committing, and rolls back on any
mismatch. It is a tool for the OWNER to run, under an approved change
window, with the server STOPPED. Nothing in the server calls it.

What it does NOT do (load-bearing, see the runbook
docs/audit/M8/prep/SEC09_EXPOSURE_RUNBOOK.md):

* It does not rotate the secret VALUES. If the old key and the database
  were both exposed, the plaintext values must be considered exposed: the
  values themselves must be rotated at their providers (api keys via
  ``rotate_secret``, connector tokens at the provider). Re-keying only
  protects future copies of the file.
* It does not create the new key file (the owner provisions it, 32 bytes,
  mode 0600, outside the source tree) and it never deletes the old key file
  or the backup — destruction of old key material is an owner step after
  verification.
* It never prints, logs or returns a secret value — only counts and
  a fingerprint keyed by the old master key (HMAC), never an unkeyed hash.

Default is DRY-RUN: the whole re-encryption and verification runs and is
then ROLLED BACK, so an operator can prove the procedure on the real file
without changing it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import stat
from typing import Any, Dict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_civilization.core.secret_store import (
    MasterKeyError,
    SecretStore,
    SecretStoreError,
    _MASTER_KEY_LEN,
    _NONCE_LEN,
)

_MARKER_AAD = b"store_key_check"


class RekeyError(SecretStoreError):
    """The rotation refused to run or was rolled back — the store is unchanged."""


def _read_key(path: str, label: str) -> bytes:
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            key = fh.read()
    except OSError as exc:
        raise RekeyError(f"{label} key file unreadable: {type(exc).__name__}") from None
    if len(key) != _MASTER_KEY_LEN:
        raise RekeyError(f"{label} key file must hold exactly {_MASTER_KEY_LEN} bytes")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise RekeyError(f"{label} key file must not be group/other accessible (chmod 600)")
    return key


def rekey_secret_store(db_path: str, old_key_file: str, new_key_file: str,
                       *, dry_run: bool = True) -> Dict[str, Any]:
    """Re-encrypt the store at ``db_path`` from the old key to the new key.

    Returns a report with counts only. Raises ``RekeyError`` (store
    unchanged) on any refusal or verification failure.
    """
    if not os.path.exists(db_path):
        raise RekeyError("secrets DB does not exist — nothing to re-key")
    old_key = _read_key(old_key_file, "old")
    new_key = _read_key(new_key_file, "new")
    if old_key == new_key:
        raise RekeyError("new key equals old key — refusing a no-op rotation")
    if os.path.realpath(old_key_file) == os.path.realpath(new_key_file):
        raise RekeyError("old and new key paths are the same file")

    conn = sqlite3.connect(db_path, isolation_level=None, timeout=1.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute("PRAGMA secure_delete=ON")  # overwrite freed pages (old ciphertext)
        try:
            # EXCLUSIVE: fails fast if any process (a running server) holds the DB
            conn.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise RekeyError(
                f"secrets DB is in use ({exc}) — stop the server before re-keying") from None
        try:
            marker = conn.execute(
                "SELECT nonce, ct FROM store_key_check WHERE k=1").fetchone()
            if marker is None:
                raise RekeyError("store has no key-check marker — cannot verify the old key")
            try:
                pt = AESGCM(old_key).decrypt(marker["nonce"], marker["ct"], _MARKER_AAD)
            except Exception:
                raise RekeyError("old key does not match this store — refusing") from None
            if pt != SecretStore._MARKER:
                raise RekeyError("key-check marker mismatch under the old key — refusing")

            rows = conn.execute(
                "SELECT tenant, name, version, nonce, value_ct FROM secrets").fetchall()
            digests: Dict[tuple, str] = {}
            tenants = set()
            for r in rows:
                aad = SecretStore._aad(r["tenant"], r["name"], r["version"])
                try:
                    value = AESGCM(old_key).decrypt(r["nonce"], r["value_ct"], aad)
                except Exception:
                    raise RekeyError(
                        f"row {r['tenant']}/{r['name']} does not decrypt under the old key "
                        "— refusing (no partial rotation)") from None
                digests[(r["tenant"], r["name"])] = hashlib.sha256(value).hexdigest()
                nonce = os.urandom(_NONCE_LEN)
                ct = AESGCM(new_key).encrypt(nonce, value, aad)
                conn.execute(
                    "UPDATE secrets SET nonce=?, value_ct=? WHERE tenant=? AND name=?",
                    (nonce, ct, r["tenant"], r["name"]))
                tenants.add(r["tenant"])
                del value
            m_nonce = os.urandom(_NONCE_LEN)
            conn.execute(
                "UPDATE store_key_check SET nonce=?, ct=? WHERE k=1",
                (m_nonce, AESGCM(new_key).encrypt(m_nonce, SecretStore._MARKER, _MARKER_AAD)))

            # VERIFY before commit: every row decrypts under the NEW key to the
            # same plaintext digest, and NOT under the old key any more.
            for r in conn.execute(
                    "SELECT tenant, name, version, nonce, value_ct FROM secrets").fetchall():
                aad = SecretStore._aad(r["tenant"], r["name"], r["version"])
                try:
                    value = AESGCM(new_key).decrypt(r["nonce"], r["value_ct"], aad)
                except Exception:
                    raise RekeyError("verification failed under the new key — rolled back") from None
                if hashlib.sha256(value).hexdigest() != digests.get((r["tenant"], r["name"])):
                    raise RekeyError("verification digest mismatch — rolled back")
                del value
            report = {
                "dry_run": dry_run,
                "rows_reencrypted": len(rows),
                "tenants": len(tenants),
                "marker_reencrypted": True,
                "verified_under_new_key": True,
                # a fingerprint of the SET of plaintexts so the operator can
                # compare a dry run with the real run. KEYED by the old master
                # key (HMAC): an unkeyed hash of one low-entropy secret would
                # be guessable offline from a pasted report (D-137 review).
                "plaintext_set_hmac": hmac.new(
                    old_key, "\n".join(sorted(digests.values())).encode(),
                    hashlib.sha256).hexdigest(),
            }
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if dry_run:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
            # purge old-key ciphertext from the WAL and free pages
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        return report
    finally:
        conn.close()


def verify_store_key(db_path: str, key_file: str) -> bool:
    """True iff ``key_file`` opens the store (marker + every row) — read-only."""
    key = _read_key(key_file, "candidate")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        m = conn.execute("SELECT nonce, ct FROM store_key_check WHERE k=1").fetchone()
        if m is None:
            return False
        try:
            if AESGCM(key).decrypt(m["nonce"], m["ct"], _MARKER_AAD) != SecretStore._MARKER:
                return False
            for r in conn.execute("SELECT tenant, name, version, nonce, value_ct FROM secrets"):
                AESGCM(key).decrypt(r["nonce"], r["value_ct"],
                                    SecretStore._aad(r["tenant"], r["name"], r["version"]))
        except Exception:
            return False
        return True
    finally:
        conn.close()


__all__ = ["RekeyError", "rekey_secret_store", "verify_store_key", "MasterKeyError"]
