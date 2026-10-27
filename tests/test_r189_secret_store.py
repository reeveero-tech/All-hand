"""R-189: encrypted per-tenant secret storage with a full lifecycle.

What this file PROVES (each test is a contract, not a formality):

- **Lifecycle** — create (put_secret, versioned, metadata-only return), get
  (value to the authorized caller or an explicit SecretNotFound — never None,
  never a default), rotate (new value handed to the trusted caller, the old
  value stops resolving IMMEDIATELY — single row, new version), revoke
  (explicit; revoking an absent secret is an error, not a silent no-op).
- **Fail-closed initialization** — initialize() is EXPLICIT (creates a 0600
  master-key file and the schema); an existing DB whose key file is missing
  is REFUSED (the store never silently regenerates a key and invalidates
  every stored secret); a wrong-length key is refused; a configured-but-
  broken store stops the server loudly.
- **Authenticated encryption at rest** — AES-256-GCM with the AAD binding
  (tenant, name, version): a ciphertext moved to another tenant or name
  fails to decrypt; a corrupted ciphertext fails; plaintext never appears
  in the DB file, its WAL, list output, exceptions or logs (marker-based).
- **Cross-process immediacy** — a second process (a real subprocess, and a
  real uvicorn server) sharing the file observes rotation and revocation
  immediately (no plaintext cache anywhere — the design price is a
  per-access round-trip, documented in the module).
- **The real consumer** — tenant-key authentication through the REAL server:
  a store-backed key authenticates HTTP requests (200), a wrong key is 401,
  and after rotate/revoke (performed OUTSIDE the server process) the old
  key is 401 and the new one is 200. The tenant identity comes only from
  the server-side resolution (SEC-02).

Honest limits (documented in the module, pinned here as non-claims): store
revocation does NOT revoke the secret at any external provider; previously
fetched plaintext cannot be recalled; the master-key file protects at-rest
bytes within one host's trust boundary (not multi-host distributed access).
Admin HTTP lifecycle endpoints are NOT part of this batch.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_civilization.core.secret_store import (  # noqa: E402
    API_KEY_SECRET_NAME, MasterKeyError, SecretNotFound, SecretStore,
    SecretStoreError)

MARKER = "R189-PLAINTEXT-MARKER-VALUE"  # a value that must NEVER appear at rest
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _mk_store(tmp_path, name="secrets.db"):
    db = str(tmp_path / name)
    key = str(tmp_path / f"{name}.masterkey")
    store = SecretStore(db, key)
    store.initialize()
    return store, db, key


# ---------------------------------------------------------------- lifecycle
def test_r189_lifecycle_create_get_rotate_revoke(tmp_path):
    store, db, key = _mk_store(tmp_path)
    info = store.put_secret("acme", "api_key", "sk-first", {"role": "admin"})
    assert info == {"tenant": "acme", "name": "api_key", "version": 1}
    assert store.get_secret("acme", "api_key") == "sk-first"
    # replace bumps the version and invalidates the old value immediately
    store.put_secret("acme", "api_key", "sk-second")
    assert store.get_secret("acme", "api_key") == "sk-second"
    listing = store.list_secrets("acme")
    assert listing[0]["version"] == 2 and listing[0]["meta"] == {}
    # rotate: a NEW value is generated when none is given; the old is dead
    new_val = store.rotate_secret("acme", "api_key")
    assert new_val != "sk-second"
    assert store.get_secret("acme", "api_key") == new_val
    # revoke: the secret stops resolving; revoking again is an explicit error
    store.revoke_secret("acme", "api_key")
    with pytest.raises(SecretNotFound):
        store.get_secret("acme", "api_key")
    with pytest.raises(SecretNotFound):
        store.revoke_secret("acme", "api_key")


def test_r189_refuse_when_absent_never_none_never_default(tmp_path):
    store, _, _ = _mk_store(tmp_path)
    with pytest.raises(SecretNotFound):
        store.get_secret("ghost", "api_key")
    with pytest.raises(SecretNotFound):
        store.rotate_secret("ghost", "api_key")
    assert store.authenticate_key("anything-at-all") is None


def test_r189_input_validation(tmp_path):
    store, _, _ = _mk_store(tmp_path)
    for tenant in ("", "bad tenant!", "x" * 65):
        with pytest.raises(SecretStoreError):
            store.put_secret(tenant, "api_key", "v")
    for name in ("", "Bad-Name", "x" * 65):
        with pytest.raises(SecretStoreError):
            store.put_secret("acme", name, "v")
    with pytest.raises(SecretStoreError):
        store.put_secret("acme", "api_key", "")       # empty value refused
    with pytest.raises(SecretStoreError):
        store.put_secret("acme", "api_key", "x" * 4097)  # over the cap


# --------------------------------------------------- initialization / keys
def test_r189_initialize_is_explicit_and_never_regenerates(tmp_path):
    db = str(tmp_path / "secrets.db")
    key = str(tmp_path / "master.key")
    store = SecretStore(db, key)
    store.initialize()
    store.put_secret("acme", "api_key", "sk-keepme")
    os.unlink(key)  # the key file is lost
    store2 = SecretStore(db, key)
    with pytest.raises(MasterKeyError):
        store2.get_secret("acme", "api_key")
    with pytest.raises(MasterKeyError):
        store2.initialize()  # an existing DB never gets a regenerated key


def test_r189_master_key_file_is_0600_and_wrong_length_refused(tmp_path):
    store, db, key = _mk_store(tmp_path)
    assert os.stat(key).st_mode & 0o777 == 0o600
    with open(key, "rb") as fh:
        assert len(fh.read()) == 32
    short = str(tmp_path / "short.key")
    with open(short, "wb") as fh:
        fh.write(b"tooshort")
    with pytest.raises(MasterKeyError):
        SecretStore(db, short).get_secret("acme", "api_key")


def test_r189_wrong_key_cannot_decrypt_existing_data(tmp_path):
    store, db, _ = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", "sk-value")
    other_key = str(tmp_path / "other.masterkey")
    with open(other_key, "wb") as fh:
        fh.write(os.urandom(32))
    # a different valid-length key on the same DB must NOT decrypt anything
    with pytest.raises(SecretStoreError):
        SecretStore(db, other_key).get_secret("acme", "api_key")


# ---------------------------------------------- authenticated encryption
def test_r189_aad_binding_blocks_ciphertext_substitution(tmp_path):
    store, db, _ = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", "sk-acme")
    store.put_secret("other", "api_key", "sk-other")
    store.put_secret("acme", "db_password", "sk-db")
    # lift acme/api_key's ciphertext and plant it under other/api_key
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT value_ct, nonce FROM secrets"
                       " WHERE tenant='acme' AND name='api_key'").fetchone()
    conn.execute("UPDATE secrets SET value_ct=?, nonce=?"
                 " WHERE tenant='other' AND name='api_key'", row)
    conn.commit()
    with pytest.raises(SecretStoreError):
        store.get_secret("other", "api_key")
    # ... and under acme/db_password (a different name)
    conn.execute("UPDATE secrets SET value_ct=?, nonce=?"
                 " WHERE tenant='acme' AND name='db_password'", row)
    conn.commit()
    with pytest.raises(SecretStoreError):
        store.get_secret("acme", "db_password")
    # the legitimate row still resolves
    assert store.get_secret("acme", "api_key") == "sk-acme"


def test_r189_corrupt_ciphertext_fails_closed(tmp_path):
    store, db, _ = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", "sk-value")
    conn = sqlite3.connect(db)
    ct = conn.execute("SELECT value_ct FROM secrets WHERE name='api_key'").fetchone()[0]
    conn.execute("UPDATE secrets SET value_ct=? WHERE name='api_key'",
                 (bytes([ct[0] ^ 0xFF]) + ct[1:],))
    conn.commit()
    with pytest.raises(SecretStoreError):
        store.get_secret("acme", "api_key")


# ----------------------------------------------------------- no-leak tests
def _all_bytes(db):
    out = open(db, "rb").read()
    for suffix in ("-wal", "-journal"):
        if os.path.exists(db + suffix):
            out += open(db + suffix, "rb").read()
    return out


def test_r189_no_plaintext_at_rest_in_db_or_wal(tmp_path):
    store, db, _ = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", MARKER)
    # the at-rest check comes FIRST — a leak must be detected on the raw
    # bytes even if later operations would also fail (red-first honesty)
    assert MARKER.encode() not in _all_bytes(db)
    # force WAL content to exist too: several rotations, each a new ciphertext
    for _ in range(3):
        store.rotate_secret("acme", "api_key")
    assert MARKER.encode() not in _all_bytes(db)
    store.get_secret("acme", "api_key")  # a read must not leak either
    assert MARKER.encode() not in _all_bytes(db)
    # sanity: the ciphertext bytes really are in the file (the DB is real)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 1


def test_r189_no_leak_in_exceptions_list_or_logs(tmp_path):
    store, db, _ = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", MARKER)
    # corrupt the row: the failure path must name the secret, never the value
    conn = sqlite3.connect(db)
    ct = conn.execute("SELECT value_ct FROM secrets WHERE name='api_key'").fetchone()[0]
    conn.execute("UPDATE secrets SET value_ct=? WHERE name='api_key'",
                 (bytes([ct[0] ^ 0xFF]) + ct[1:],))
    conn.commit()
    try:
        store.get_secret("acme", "api_key")
        assert False, "must raise"
    except SecretStoreError as exc:
        assert MARKER not in str(exc)
    # list output: metadata only — the JSON of the listing carries no value
    listing = json.dumps(store.list_secrets("acme"))
    assert MARKER not in listing and "value" not in listing
    # logs: capture EVERYTHING emitted while the module operates
    records = []

    class _Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Cap()
    logging.getLogger().addHandler(handler)
    try:
        store.rotate_secret("acme", "api_key")
        store.authenticate_key("wrong-key")
        store.revoke_secret("acme", "api_key")
    finally:
        logging.getLogger().removeHandler(handler)
    assert all(MARKER not in m for m in records), "a log line leaked the value"


# --------------------------------------------------- cross-process reality
def test_r189_second_process_sees_rotation_and_revocation(tmp_path):
    store, db, key = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", "sk-proc")
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from agent_civilization.core.secret_store import SecretStore; "
        "import os; "
        "os.environ['AGOS_SECRETS_MASTER_KEY_FILE'] = %r; "
        "s = SecretStore(%r, %r); "
        "print(s.get_secret('acme', 'api_key'))" % (ROOT, key, db, key)
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, timeout=60)
    assert out.stdout.strip() == "sk-proc", out.stderr  # visible cross-process
    # rotate from THIS process: the subprocess (a fresh process) sees the new
    new_val = store.rotate_secret("acme", "api_key")
    code2 = code.replace("s.get_secret('acme', 'api_key')",
                         "s.authenticate_key('sk-proc')")
    out2 = subprocess.run([sys.executable, "-c", code2], capture_output=True,
                          text=True, timeout=60)
    assert out2.stdout.strip() == "None", out2.stderr  # old key dead everywhere
    store.revoke_secret("acme", "api_key")
    out3 = subprocess.run([sys.executable, "-c", code.replace(
        "print(s.get_secret('acme', 'api_key'))",
        "print(s.authenticate_key(%r))" % new_val)], capture_output=True,
        text=True, timeout=60)
    assert out3.stdout.strip() == "None", out3.stderr  # revocation is global


# ------------------------------------------- the real consumer: HTTP auth
class _Server:
    """A real uvicorn subprocess with the store enabled (the R-189 opt-in).

    No env tenant keys — the ONLY credentials are the store-backed ones, so
    the test proves the store is the real authentication source, not a
    decoration beside env credentials.
    """

    def __init__(self, db, key):
        self.port = _free_port()
        self.db, self.key = db, key
        self.proc = None
        self._log = tempfile.NamedTemporaryFile(prefix="r189-server-",
                                                 suffix=".log", delete=False)

    def start(self, extra_env=None):
        env = dict(os.environ)
        env.pop("AGOS_TENANT_KEYS", None)
        env.update({
            "AGOS_ENV": "development",
            "AGOS_SECRETS_DB": self.db,
            "AGOS_SECRETS_MASTER_KEY_FILE": self.key,
        })
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--port",
             str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("server died at boot:\n" + self._tail())
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("server not healthy in 60s:\n" + self._tail())

    def get(self, path, api_key):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"X-API-Key": api_key} if api_key else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _tail(self, n=3000):
        self._log.flush()
        with open(self._log.name) as f:
            return f.read()[-n:]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_r189_auth_through_real_server_lifecycle(tmp_path):
    store, db, key = _mk_store(tmp_path)
    key_val = secrets.token_urlsafe(24)
    store.put_secret("acme", API_KEY_SECRET_NAME, key_val, {"role": "user"})
    srv = _Server(db, key)
    srv.start()
    try:
        # the store-backed key authenticates a REAL request (the tenant
        # identity comes only from the server-side resolution — SEC-02)
        assert srv.get("/api/tasks", key_val) == 200
        # a wrong key is 401; no key is 401
        assert srv.get("/api/tasks", "wrong-" + key_val) == 401
        assert srv.get("/api/tasks", None) == 401
        # ROTATE from OUTSIDE the server process: the old key dies, the new
        # one lives — immediately, no restart, no cache
        new_val = store.rotate_secret("acme", API_KEY_SECRET_NAME)
        assert srv.get("/api/tasks", key_val) == 401
        assert srv.get("/api/tasks", new_val) == 200
        # REVOKE from outside: the key is dead for every request
        store.revoke_secret("acme", API_KEY_SECRET_NAME)
        assert srv.get("/api/tasks", new_val) == 401
    finally:
        srv.stop()


def test_r189_configured_but_broken_store_stops_the_server_loudly(tmp_path):
    store, db, key = _mk_store(tmp_path)
    store.put_secret("acme", "api_key", "sk-value")
    os.unlink(key)  # the store is configured but its key is gone
    srv = _Server(db, key)
    with pytest.raises(RuntimeError, match="died at boot"):
        srv.start()
    assert srv.proc.poll() is not None  # it really died — no silent downgrade
    assert "secret store" in srv._tail()


def test_r189_store_disabled_by_default_no_behavior_change(tmp_path):
    # no AGOS_SECRETS_DB -> no store at all (the base env-credential auth is
    # untouched); import a fresh subprocess to prove the default is off
    code = ("import os; os.environ.pop('AGOS_SECRETS_DB', None); "
            "os.environ['AGOS_ENV']='development'; os.environ.pop"
            "('AGOS_TENANT_KEYS', None); import server; "
            "print('store=' + ('on' if server._SECRET_STORE else 'off'), "
            "'auth=' + str(server.ENABLE_AUTH))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, cwd=ROOT)
    assert "store=off" in out.stdout, out.stderr
