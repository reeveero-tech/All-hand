"""SEC-03 (R-139): production refuses to start without valid credentials.

Before SEC-03 a production server with neither AGOS_TENANT_KEYS nor
AGOS_API_KEY started normally (tenant data then answered 401 — SEC-02), so a
deployment that forgot its keys looked healthy. Now:

* production is the default — ``AGOS_ENV`` unset means production;
* production without credentials, or with any malformed credential setting,
  fails at import of ``server`` (uvicorn exits; Render's health check fails);
* only an explicit ``AGOS_ENV=development`` / ``test`` starts without keys, and
  never on a deployed platform (``RENDER=true`` is set by Render itself);
* error messages and start-up logs carry no key or key hash.

The process-level tests start a real Python interpreter with a minimal,
explicit environment (no inherited AGOS_* / RENDER variables) and import
``server`` exactly as uvicorn does.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from agent_civilization.core import tenancy
from agent_civilization.core.tenancy import CredentialsError

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_VARS = ("AGOS_ENV", "AGOS_TENANT_KEYS", "AGOS_API_KEY", "RENDER")


def _sha(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def _key(prefix="k") -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _tenant_keys(*pairs) -> str:
    return json.dumps([{"tenant": t, "key_sha256": _sha(k), "role": r} for t, k, r in pairs])


# ------------------------------------------------------------------ helpers

def _start_process(env_extra: dict) -> subprocess.CompletedProcess:
    """`import server` in a fresh interpreter, like uvicorn server:app."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS and not k.startswith("AGOS_")}
    env.update(env_extra)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-c", "import server; print('STARTED', server.RUNTIME_ENV)"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


@pytest.fixture
def reload_server(monkeypatch):
    """Reload ``server`` under a given environment; restore the test env afterwards."""
    def _do(**env):
        for k in ENV_VARS:
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import server
        return importlib.reload(server)
    yield _do
    for k in ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AGOS_ENV", "test")
    import server
    importlib.reload(server)


# ------------------------------------------------------------------ real process start

def test_process_without_any_setting_is_production_and_refuses_to_start():
    r = _start_process({})
    assert r.returncode != 0 and "STARTED" not in r.stdout
    assert "production start refused: no credentials configured" in r.stderr


def test_process_production_with_tenant_keys_starts():
    r = _start_process({"AGOS_TENANT_KEYS": _tenant_keys(("acme", _key(), "user"))})
    assert r.returncode == 0, r.stderr[-2000:]
    assert "STARTED production" in r.stdout


def test_process_on_render_with_development_refuses_to_start():
    r = _start_process({"AGOS_ENV": "development", "RENDER": "true"})
    assert r.returncode != 0 and "not allowed on a deployed platform" in r.stderr


def test_process_explicit_development_starts_without_keys():
    r = _start_process({"AGOS_ENV": "development"})
    assert r.returncode == 0, r.stderr[-2000:]
    assert "STARTED development" in r.stdout
    assert "no credentials (AGOS_ENV=development)" in r.stderr


# ------------------------------------------------------------------ production start-up matrix

def test_production_without_credentials_fails(reload_server):
    with pytest.raises(CredentialsError, match="no credentials configured"):
        reload_server(AGOS_ENV="production")
    with pytest.raises(CredentialsError, match="no credentials configured"):
        reload_server()  # AGOS_ENV unset = production


@pytest.mark.parametrize("empty", ["", "   "])
def test_production_with_empty_tenant_keys_and_no_api_key_fails(reload_server, empty):
    with pytest.raises(CredentialsError, match="no credentials configured"):
        reload_server(AGOS_ENV="production", AGOS_TENANT_KEYS=empty)


def test_production_with_valid_tenant_keys_starts_and_enforces_auth(reload_server):
    ka, kb = _key("a"), _key("b")
    srv = reload_server(AGOS_ENV="production",
                        AGOS_TENANT_KEYS=_tenant_keys(("tenant_a", ka, "user"), ("tenant_b", kb, "user")))
    assert srv.RUNTIME_ENV == "production" and srv.ENABLE_AUTH
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        assert c.get("/api/tasks").status_code == 401
        assert c.get("/api/tasks", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/api/tasks", headers={"X-API-Key": ka}).status_code == 200


def test_production_with_valid_legacy_api_key_starts_as_operator_admin(reload_server):
    key = _key("op")
    srv = reload_server(AGOS_ENV="production", AGOS_API_KEY=key)
    assert srv.RUNTIME_ENV == "production"
    assert list(srv.CREDENTIALS.values()) == [tenancy.Principal("operator", "admin")]
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        assert c.get("/api/tasks").status_code == 401
        assert c.get("/api/tasks?all_tenants=true", headers={"X-API-Key": key}).status_code == 200


def test_production_key_rotation_two_keys_one_tenant(reload_server):
    old, new = _key("old"), _key("new")
    srv = reload_server(AGOS_ENV="production",
                        AGOS_TENANT_KEYS=_tenant_keys(("acme", old, "user"), ("acme", new, "user")))
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "rotation 1 2 3"},
                   headers={"X-API-Key": old})
        assert r.status_code == 202
        # the same tenant, through the new key, sees the task created with the old key
        tid = r.json()["task_id"]
        assert c.get(f"/api/tasks/{tid}", headers={"X-API-Key": new}).status_code == 200


BAD_TENANT_KEYS = {
    "broken_json": "[{not json",
    "not_a_list": '{"tenant": "a"}',
    "empty_list": "[]",
    "bad_role": json.dumps([{"tenant": "a", "key_sha256": "0" * 64, "role": "superuser"}]),
    "bad_hash_short": json.dumps([{"tenant": "a", "key_sha256": "abc123"}]),
    "bad_hash_upper": json.dumps([{"tenant": "a", "key_sha256": "A" * 64}]),
    "plain_key_instead_of_hash": json.dumps([{"tenant": "a", "key": "plain-secret-value-123"}]),
    "bad_tenant": json.dumps([{"tenant": "a b", "key_sha256": "0" * 64}]),
    "duplicate_key": json.dumps([{"tenant": "a", "key_sha256": "0" * 64}, {"tenant": "b", "key_sha256": "0" * 64}]),
}


@pytest.mark.parametrize("name", sorted(BAD_TENANT_KEYS))
def test_production_with_malformed_credentials_fails(reload_server, name):
    with pytest.raises(CredentialsError):
        reload_server(AGOS_ENV="production", AGOS_TENANT_KEYS=BAD_TENANT_KEYS[name])
    # a valid AGOS_API_KEY next to a broken AGOS_TENANT_KEYS does not rescue it
    with pytest.raises(CredentialsError):
        reload_server(AGOS_ENV="production", AGOS_TENANT_KEYS=BAD_TENANT_KEYS[name], AGOS_API_KEY=_key("op"))


@pytest.mark.parametrize("bad", ["short-key", " " * 20, _key("op") + "\n", " " + _key("op")])
def test_production_with_weak_or_padded_api_key_fails(reload_server, bad):
    with pytest.raises(CredentialsError):
        reload_server(AGOS_ENV="production", AGOS_API_KEY=bad)


@pytest.mark.parametrize("value", ["prod", "Production", "dev", "staging", "local", "true"])
def test_unknown_agos_env_value_fails_instead_of_being_guessed(reload_server, value):
    with pytest.raises(CredentialsError, match="AGOS_ENV must be one of"):
        reload_server(AGOS_ENV=value, AGOS_TENANT_KEYS=_tenant_keys(("a", _key(), "user")))


@pytest.mark.parametrize("typo", ["AGOS_TENANT_KEY", "AGOS_TENANTS_KEYS", "AGOS_APIKEY", "AGOS_API_KEYS",
                                  "AGOS_KEY", "TENANT_KEYS"])
def test_misspelled_credential_variable_leaves_production_closed(reload_server, monkeypatch, typo):
    monkeypatch.setenv(typo, _tenant_keys(("a", _key(), "user")) if "TENANT" in typo else _key("op"))
    try:
        with pytest.raises(CredentialsError, match="no credentials configured"):
            reload_server(AGOS_ENV="production")
    finally:
        monkeypatch.delenv(typo)


@pytest.mark.parametrize("typo", ["AGOS_ENVIRONMENT", "AGOS_MODE", "ENV", "APP_ENV"])
def test_misspelled_environment_variable_stays_production(reload_server, monkeypatch, typo):
    monkeypatch.setenv(typo, "development")
    try:
        with pytest.raises(CredentialsError, match="no credentials configured"):
            reload_server()
    finally:
        monkeypatch.delenv(typo)


# ------------------------------------------------------------------ non-production, platform guard

@pytest.mark.parametrize("name", ["development", "test"])
def test_explicit_non_production_starts_without_keys_but_serves_no_tenant_data(reload_server, name):
    srv = reload_server(AGOS_ENV=name)
    assert srv.RUNTIME_ENV == name and srv.CREDENTIALS == {}
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        for path in ("/api/tasks", "/api/memory", "/api/events"):
            r = c.get(path, headers={"X-API-Key": "anything", "X-AGOS-Env": "development"})
            assert r.status_code == 401 and r.json()["detail"].startswith("authentication is not configured")
        assert c.post("/api/tasks", json={"specialization": "analyst", "prompt": "1 2"}).status_code == 401


@pytest.mark.parametrize("name", ["development", "test"])
@pytest.mark.parametrize("marker", ["true", "TRUE", " true "])
def test_non_production_is_refused_on_render(reload_server, name, marker):
    with pytest.raises(CredentialsError, match="not allowed on a deployed platform"):
        reload_server(AGOS_ENV=name, RENDER=marker)
    # even with valid credentials: a deployment is always production
    with pytest.raises(CredentialsError, match="not allowed on a deployed platform"):
        reload_server(AGOS_ENV=name, RENDER=marker, AGOS_TENANT_KEYS=_tenant_keys(("a", _key(), "user")))


def test_render_production_with_keys_starts_and_without_keys_fails(reload_server):
    srv = reload_server(RENDER="true", AGOS_TENANT_KEYS=_tenant_keys(("a", _key(), "user")))
    assert srv.RUNTIME_ENV == "production"
    with pytest.raises(CredentialsError, match="no credentials configured"):
        reload_server(RENDER="true")


def test_startup_decision_reads_only_the_process_environment():
    # pure function of the given environment: no request, header or file is consulted
    assert tenancy.runtime_environment({}) == "production"
    assert tenancy.enforce_startup({}, {"AGOS_ENV": "test"}) == "test"
    with pytest.raises(CredentialsError):
        tenancy.enforce_startup({}, {})


# ------------------------------------------------------------------ no secret in messages or logs

def test_errors_and_logs_never_contain_keys_or_hashes(reload_server, caplog):
    short = "s3cr3t-short"
    with pytest.raises(CredentialsError) as e:
        reload_server(AGOS_ENV="production", AGOS_API_KEY=short)
    assert short not in str(e.value)
    bad_hash = "f" * 63
    with pytest.raises(CredentialsError) as e:
        reload_server(AGOS_ENV="production", AGOS_TENANT_KEYS=json.dumps([{"tenant": "a", "key_sha256": bad_hash}]))
    assert bad_hash not in str(e.value)
    good = _key("good")
    caplog.set_level("DEBUG")
    reload_server(AGOS_ENV="production", AGOS_API_KEY=good,
                  AGOS_TENANT_KEYS=_tenant_keys(("acme", good + "x", "user")))
    assert "startup: AGOS_ENV=production, 2 credential(s) for 2 tenant(s)" in caplog.text
    assert good not in caplog.text and _sha(good) not in caplog.text and _sha(good + "x") not in caplog.text


def test_process_failure_output_contains_no_key():
    secret = "never-print-me-" + secrets.token_hex(8)
    bad = json.dumps([{"tenant": "a", "key_sha256": _sha(secret), "role": "root"}])
    r = _start_process({"AGOS_TENANT_KEYS": bad})
    assert r.returncode != 0 and "role must be one of" in r.stderr
    assert secret not in r.stderr and _sha(secret) not in r.stderr
    weak = "tiny-key-zq"
    r = _start_process({"AGOS_API_KEY": weak})
    assert r.returncode != 0 and "shorter than 16" in r.stderr and weak not in r.stderr


# ------------------------------------------------------------------ SEC-02 regression in production mode

def test_tenant_isolation_still_holds_in_production(reload_server):
    ka, kb = _key("a"), _key("b")
    srv = reload_server(AGOS_ENV="production",
                        AGOS_TENANT_KEYS=_tenant_keys(("tenant_a", ka, "user"), ("tenant_b", kb, "user")))
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        tid = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "secret-prod-a 1 2"},
                     headers={"X-API-Key": ka}).json()["task_id"]
        assert c.get(f"/api/tasks/{tid}", headers={"X-API-Key": kb}).status_code == 404
        assert "secret-prod-a" not in c.get("/api/tasks", headers={"X-API-Key": kb}).text
        assert c.get("/api/tasks?all_tenants=true", headers={"X-API-Key": kb}).status_code == 403
        assert c.get(f"/api/tasks/{tid}", headers={"X-API-Key": ka}).status_code == 200


def test_external_agent_registration_needs_documented_authority_in_production(reload_server):
    """Report §6 item 3 (SEC-03): no public external-agent registration — the
    admin role from SEC-02 is required; anonymous 401, tenant user 403."""
    user, admin = _key("u"), _key("adm")
    srv = reload_server(AGOS_ENV="production",
                        AGOS_TENANT_KEYS=_tenant_keys(("tenant_a", user, "user"), ("ops", admin, "admin")))
    body = {"name": "ext-sec03", "url": "http://agent.example.org:9/x", "model": "", "api_key": ""}
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        assert c.post("/api/agents/external", json=body).status_code == 401
        assert c.post("/api/agents/external", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.post("/api/agents/external", json=body, headers={"X-API-Key": user}).status_code == 403
        assert c.delete("/api/agents/external/ext-sec03", headers={"X-API-Key": user}).status_code == 403
        assert c.post("/api/agents/external", json=body, headers={"X-API-Key": admin}).status_code == 201
        assert c.delete("/api/agents/external/ext-sec03", headers={"X-API-Key": admin}).status_code == 200


# ------------------------------------------------------------------ deployment configuration

def _render_env():
    spec = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    (svc,) = [s for s in spec["services"] if s.get("type") == "web"]
    return {e["key"]: e for e in svc["envVars"]}


def _source_text():
    files = [ROOT / "server.py", *(ROOT / "agent_civilization").rglob("*.py"), *(ROOT / "agos_kernel").rglob("*.py")]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_render_blueprint_is_production_and_declares_both_credential_inputs():
    env = _render_env()
    assert env["AGOS_ENV"].get("value") == "production" and "sync" not in env["AGOS_ENV"]
    for name in ("AGOS_TENANT_KEYS", "AGOS_API_KEY"):
        assert env[name].get("sync") is False and "value" not in env[name], name  # secrets: dashboard only


def test_render_blueprint_has_no_misspelled_agos_variable():
    """Every AGOS_* name in render.yaml is one the code actually reads — a typo
    (e.g. AGOS_TENANT_KEY) would otherwise be silently ignored."""
    src = _source_text()
    unknown = [k for k in _render_env() if k.startswith("AGOS_") and f'"{k}"' not in src]
    assert unknown == []


def test_render_blueprint_sets_no_non_production_override():
    env = _render_env()
    for k, e in env.items():
        assert str(e.get("value", "")).strip() not in ("development", "test"), k


def test_dockerfile_does_not_select_a_non_production_environment():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(ENV|ARG)\b.*AGOS_ENV\s*=\s*(development|test)", text, re.M)
    assert re.search(r"^ENV AGOS_ENV=production$", text, re.M)
