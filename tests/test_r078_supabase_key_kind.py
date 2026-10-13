"""R-078 (RISK-018): the server detects a PUBLIC Supabase key at startup.

With RLS on and no anon policies (docs/sql/001_init.sql, D-039) a public key
makes every write fail while the server keeps serving from memory. Before
R-078 /health reported storage "healthy" in that case (docs/audit/M6/logs/49).
"""
import importlib.util
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_civilization.storage import supabase_store as st

_spec = importlib.util.spec_from_file_location(
    "make_jwt", Path(__file__).resolve().parents[1] / "scripts" / "r042" / "make_jwt.py")
make_jwt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_jwt)

SECRET = "r078-test-secret-not-a-real-key-0000000"
ANON_JWT = make_jwt.make(SECRET, "anon")
SERVICE_JWT = make_jwt.make(SECRET, "service_role")


@pytest.mark.parametrize("key,source,kind", [
    ("sb_secret_abc123", "SUPABASE_SERVICE_KEY", "service"),
    ("sb_publishable_abc123", "SUPABASE_SERVICE_KEY", "public"),   # wrong key in the service var
    (SERVICE_JWT, "SUPABASE_SERVICE_KEY", "service"),
    (ANON_JWT, "SUPABASE_SERVICE_KEY", "public"),                  # anon JWT in the service var
    (ANON_JWT, "SUPABASE_ANON_KEY", "public"),
    (SERVICE_JWT, "SUPABASE_ANON_KEY", "service"),                 # format wins over the var name
    ("opaque-value", "SUPABASE_ANON_KEY", "public"),
    ("opaque-value", "VITE_SUPABASE_ANON_KEY", "public"),
    ("opaque-value", "SUPABASE_SERVICE_KEY", "unknown"),
    ("a.b.c", "SUPABASE_SERVICE_KEY", "unknown"),                  # 3 parts but not a JWT
    ("", "", "none"),
])
def test_classify_key(key, source, kind):
    assert st.classify_key(key, source) == kind


def _configure(monkeypatch, key, source):
    monkeypatch.setattr(st, "SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(st, "SUPABASE_KEY", key)
    monkeypatch.setattr(st, "SUPABASE_KEY_SOURCE", source)


def test_key_status_never_contains_the_value(monkeypatch):
    _configure(monkeypatch, ANON_JWT, "SUPABASE_ANON_KEY")
    status = st.key_status()
    assert status == {"kind": "public", "source": "SUPABASE_ANON_KEY"}
    assert ANON_JWT not in repr(status)


def test_startup_warns_on_public_key_without_leaking_it(monkeypatch, caplog):
    _configure(monkeypatch, ANON_JWT, "SUPABASE_SERVICE_KEY")
    with caplog.at_level(logging.INFO, logger="storage"):
        st.log_key_status()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "PUBLIC key" in text and "SUPABASE_SERVICE_KEY" in text
    assert ANON_JWT not in caplog.text and SECRET not in caplog.text


@pytest.mark.parametrize("key", ["sb_secret_abc123", SERVICE_JWT])
def test_startup_no_warning_on_service_key(monkeypatch, caplog, key):
    _configure(monkeypatch, key, "SUPABASE_SERVICE_KEY")
    with caplog.at_level(logging.INFO, logger="storage"):
        assert st.log_key_status()["kind"] == "service"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert key not in caplog.text


def test_unconfigured_storage_logs_nothing(monkeypatch, caplog):
    _configure(monkeypatch, "", "")
    monkeypatch.setattr(st, "SUPABASE_URL", "")
    with caplog.at_level(logging.INFO, logger="storage"):
        assert st.log_key_status()["kind"] == "none"
    assert caplog.records == []


def _client():
    import server
    return TestClient(server.app)


def test_health_degraded_and_status_field_with_public_key(monkeypatch):
    _configure(monkeypatch, "sb_publishable_abc123", "SUPABASE_SERVICE_KEY")
    with _client() as c:
        h = c.get("/health")
        s = c.get("/api/system/status").json()
    assert h.status_code == 200  # degraded still serves traffic (R-004)
    storage = h.json()["checks"]["storage"]
    assert storage["status"] == "degraded"
    assert "public key" in storage["message"] and storage["details"]["supabase_key"] == "public"
    assert s["supabase_key"] == {"kind": "public", "source": "SUPABASE_SERVICE_KEY"}
    assert "sb_publishable_abc123" not in h.text + repr(s)


def test_health_healthy_with_service_key(monkeypatch):
    _configure(monkeypatch, SERVICE_JWT, "SUPABASE_SERVICE_KEY")
    with _client() as c:
        storage = c.get("/health").json()["checks"]["storage"]
        s = c.get("/api/system/status").json()
    assert storage["status"] == "healthy" and storage["details"]["supabase_key"] == "service"
    assert s["supabase_key"]["kind"] == "service"
