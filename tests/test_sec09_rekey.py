"""SEC-09 (D-137): offline master-key rotation + master-key path consistency.

All data here is SYNTHETIC (tmp_path). Nothing touches a real store.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent_civilization.core.secret_store import SecretStore, MasterKeyError
from agent_civilization.core.secret_rekey import (
    RekeyError, rekey_secret_store, verify_store_key,
)

ROOT = Path(__file__).resolve().parents[1]
VALUES = {("t_a", "api_key"): "ka-" + "x" * 30, ("t_a", "github_token"): "gh-123",
          ("t_b", "api_key"): "kb-" + "y" * 30}


def _key(path: Path) -> Path:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, secrets.token_bytes(32))
    os.close(fd)
    return path


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "secrets.sqlite3"
    old = _key(tmp_path / "old.key")
    s = SecretStore(str(db), str(old))
    s.initialize()
    for (t, n), v in VALUES.items():
        s.put_secret(t, n, v, meta={"role": "admin"} if n == "api_key" else None)
    s.rotate_secret("t_b", "api_key", "kb-rotated-" + "z" * 20)  # version 2 (AAD binds it)
    return db, old, _key(tmp_path / "new.key")


def _expected():
    e = dict(VALUES)
    e[("t_b", "api_key")] = "kb-rotated-" + "z" * 20
    return e


def test_dry_run_changes_nothing(store):
    db, old, new = store
    before = db.read_bytes()
    rep = rekey_secret_store(str(db), str(old), str(new), dry_run=True)
    assert rep["rows_reencrypted"] == 3 and rep["tenants"] == 2 and rep["dry_run"]
    assert verify_store_key(str(db), str(old)) is True
    assert verify_store_key(str(db), str(new)) is False
    # the old store still serves every value
    s = SecretStore(str(db), str(old))
    s.initialize()
    for (t, n), v in _expected().items():
        assert s.get_secret(t, n) == v
    assert before  # file existed


def test_apply_rotates_and_preserves_values_versions_and_auth(store):
    db, old, new = store
    rep = rekey_secret_store(str(db), str(old), str(new), dry_run=False)
    assert rep["rows_reencrypted"] == 3 and rep["verified_under_new_key"]
    assert verify_store_key(str(db), str(new)) is True
    assert verify_store_key(str(db), str(old)) is False
    s = SecretStore(str(db), str(new))
    s.initialize()  # the key-check marker accepts the new key
    for (t, n), v in _expected().items():
        assert s.get_secret(t, n) == v
    assert {r["name"]: r["version"] for r in s.list_secrets("t_b")} == {"api_key": 2}
    assert s.authenticate_key("ka-" + "x" * 30) == {"tenant": "t_a", "role": "admin"}
    with pytest.raises(MasterKeyError):
        SecretStore(str(db), str(old)).initialize()  # old key now refused at startup


def test_dry_run_and_apply_report_same_plaintext_set(store):
    db, old, new = store
    d = rekey_secret_store(str(db), str(old), str(new), dry_run=True)
    a = rekey_secret_store(str(db), str(old), str(new), dry_run=False)
    assert d["plaintext_set_hmac"] == a["plaintext_set_hmac"]


def test_no_value_in_report_or_new_file(store):
    db, old, new = store
    rep = rekey_secret_store(str(db), str(old), str(new), dry_run=False)
    blob = json.dumps(rep) + db.read_bytes().decode("latin-1")
    for v in _expected().values():
        assert v not in blob


def test_wrong_old_key_refused_store_unchanged(store, tmp_path):
    db, old, new = store
    wrong = _key(tmp_path / "wrong.key")
    with pytest.raises(RekeyError, match="old key does not match"):
        rekey_secret_store(str(db), str(wrong), str(new), dry_run=False)
    assert verify_store_key(str(db), str(old)) is True


def test_same_key_and_bad_new_key_refused(store, tmp_path):
    db, old, new = store
    with pytest.raises(RekeyError, match="equals old key"):
        rekey_secret_store(str(db), str(old), str(old), dry_run=True)
    short = tmp_path / "short.key"
    short.write_bytes(b"x" * 16)
    os.chmod(short, 0o600)
    with pytest.raises(RekeyError, match="exactly 32"):
        rekey_secret_store(str(db), str(old), str(short), dry_run=True)
    os.chmod(new, 0o644)
    with pytest.raises(RekeyError, match="chmod 600"):
        rekey_secret_store(str(db), str(old), str(new), dry_run=True)


def test_corrupt_row_rolls_back_everything(store):
    db, old, new = store
    c = sqlite3.connect(db)
    c.execute("UPDATE secrets SET value_ct=? WHERE tenant='t_b'", (b"\x00" * 40,))
    c.commit()
    c.close()
    with pytest.raises(RekeyError, match="no partial rotation"):
        rekey_secret_store(str(db), str(old), str(new), dry_run=False)
    # the marker and the healthy rows are still under the OLD key
    s = SecretStore(str(db), str(old))
    s.initialize()
    assert s.get_secret("t_a", "api_key") == VALUES[("t_a", "api_key")]


def test_store_in_use_is_refused(store):
    db, old, new = store
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a live writer (a running server)
    try:
        with pytest.raises(RekeyError, match="stop the server"):
            rekey_secret_store(str(db), str(old), str(new), dry_run=False)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert verify_store_key(str(db), str(old)) is True


def test_cli_dry_run_then_apply_with_backup(store):
    db, old, new = store
    cli = [sys.executable, str(ROOT / "scripts" / "sec09_rekey_secret_store.py"),
           "--db", str(db), "--old-key", str(old), "--new-key", str(new)]
    r = subprocess.run(cli, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["dry_run"] is True
    r = subprocess.run(cli + ["--apply"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["new_key_opens_store"] is True and rep["old_key_opens_store"] is False
    bak = Path(rep["backup"])
    assert bak.exists() and (bak.stat().st_mode & 0o077) == 0
    # the backup is the rollback path: still opens with the OLD key
    assert verify_store_key(str(bak), str(old)) is True
    for v in _expected().values():
        assert v not in r.stdout


# ---------------------------------------------------------------------------
# master-key PATH consistency: the server reads AGOS_SECRETS_MASTER_KEY_FILE
# (default <tmp>/agos_secrets_master_key); connectors used to read ONLY a
# different variable, AGOS_MASTER_KEY_FILE — a rotation that updates one
# variable would silently desynchronize the two readers.
# ---------------------------------------------------------------------------
def _fresh_connectors(monkeypatch):
    import agent_civilization.connectors as conn
    monkeypatch.setattr(conn, "_secret_store_instance", None)
    monkeypatch.setattr(conn, "_secret_store_key", None)
    return conn


def test_connectors_honor_the_server_master_key_variable(store, monkeypatch):
    db, old, _new = store
    conn = _fresh_connectors(monkeypatch)
    monkeypatch.setenv("AGOS_SECRETS_DB", str(db))
    monkeypatch.delenv("AGOS_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("AGOS_SECRETS_MASTER_KEY_FILE", str(old))
    assert conn._secret_store().get_secret("t_a", "github_token") == "gh-123"


def test_connectors_refuse_conflicting_master_key_variables(store, monkeypatch):
    db, old, new = store
    conn = _fresh_connectors(monkeypatch)
    monkeypatch.setenv("AGOS_SECRETS_DB", str(db))
    monkeypatch.setenv("AGOS_SECRETS_MASTER_KEY_FILE", str(old))
    monkeypatch.setenv("AGOS_MASTER_KEY_FILE", str(new))
    with pytest.raises(conn.AuthInvalidated):
        conn._secret_store()


def test_connectors_legacy_variable_still_works(store, monkeypatch):
    db, old, _new = store
    conn = _fresh_connectors(monkeypatch)
    monkeypatch.setenv("AGOS_SECRETS_DB", str(db))
    monkeypatch.delenv("AGOS_SECRETS_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("AGOS_MASTER_KEY_FILE", str(old))
    assert conn._secret_store().get_secret("t_a", "github_token") == "gh-123"


def test_report_fingerprint_is_keyed_not_a_guessable_hash(tmp_path):
    """D-137: the comparison fingerprint must not be an UNKEYED hash of the
    plaintexts — for a store holding one low-entropy secret, sha256 of the
    value (or of its hex digest) would allow offline guessing from a report
    pasted into a ticket. It is an HMAC keyed by the OLD master key, which
    an attacker able to use it already holds (and could decrypt anyway)."""
    import hashlib
    db = tmp_path / "s.sqlite3"
    old = _key(tmp_path / "o.key")
    s = SecretStore(str(db), str(old))
    s.initialize()
    s.put_secret("t", "pin", "1234")
    rep = rekey_secret_store(str(db), str(old), str(_key(tmp_path / "n.key")), dry_run=True)
    fp = rep.get("plaintext_set_hmac")
    assert fp and "plaintext_set_digest" not in rep
    one = hashlib.sha256(b"1234").hexdigest()
    assert fp not in (one, hashlib.sha256(one.encode()).hexdigest())
