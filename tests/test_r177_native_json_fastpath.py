"""R-177 (D-142): native-JSON fast path for the two routes the reduced
Codespaces run found saturated (log 389, F1/F2).

Profiling (log 391) showed the dominant cost of ``GET /api/agents`` (~88%)
and ``GET /api/admin/tools`` (~62%) is FastAPI's generic
``jsonable_encoder`` walking already-JSON-native dicts — not the route logic.
The fix renders such payloads directly (the same ``json.dumps`` settings
Starlette's ``JSONResponse`` uses) and falls back to the generic encoder for
anything that is not JSON-native.

The API contract must NOT change (owner rule): these tests prove the response
is byte-identical to the generic path (body, status, content-type), the
filter still works, the role gate still applies, and the fallback still
encodes non-native values exactly like FastAPI.
"""
from __future__ import annotations

import datetime
import enum
import hashlib
import importlib
import json
import secrets

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agent_civilization.core import tool_runtime


def _sha(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    keys = {"TENANT": "t-" + secrets.token_urlsafe(24),
            "ADM": "adm-" + secrets.token_urlsafe(24)}
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", str(tmp_path / "tools.sqlite3"))
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    monkeypatch.setenv("AGOS_TENANT_KEYS", json.dumps([
        {"tenant": "tenant_a", "key_sha256": _sha(keys["TENANT"]), "role": "user"},
        {"tenant": "ops", "key_sha256": _sha(keys["ADM"]), "role": "admin"},
    ]))
    monkeypatch.setenv("AGOS_RATE_LIMIT_REQUESTS", "100000")
    tool_runtime.reset_runtime_registry()
    import server
    srv = importlib.reload(server)
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield srv, c, keys
    for k in ("AGOS_TENANT_KEYS", "AGOS_RATE_LIMIT_REQUESTS"):
        monkeypatch.delenv(k, raising=False)
    tool_runtime.reset_runtime_registry()
    importlib.reload(server)


def _generic(content):
    """What FastAPI did before D-142 for a returned dict."""
    return JSONResponse(content=jsonable_encoder(content))


def _both(srv, c, monkeypatch, path, headers=None):
    fast = c.get(path, headers=headers or {})
    with pytest.MonkeyPatch.context() as mp:  # separate: keep the fixture's env
        mp.setattr(srv, "_native_json_response", _generic)
        slow = c.get(path, headers=headers or {})
    return fast, slow


def _assert_identical(fast, slow):
    assert fast.status_code == slow.status_code == 200, (fast.text[:200], slow.text[:200])
    assert fast.content == slow.content
    assert fast.headers["content-type"] == slow.headers["content-type"] == "application/json"
    assert fast.headers["content-length"] == slow.headers["content-length"]


def test_agents_list_is_byte_identical_to_the_generic_path(env, monkeypatch):
    srv, c, _ = env
    fast, slow = _both(srv, c, monkeypatch, "/api/agents")
    _assert_identical(fast, slow)
    assert fast.json()["count"] == len(fast.json()["agents"]) > 0


def test_agents_filter_is_byte_identical_and_still_filters(env, monkeypatch):
    srv, c, _ = env
    fast, slow = _both(srv, c, monkeypatch, "/api/agents?specialization=analyst")
    _assert_identical(fast, slow)
    body = fast.json()
    assert body["count"] > 0
    assert {a["specialization"] for a in body["agents"]} == {"analyst"}


def test_admin_tools_is_byte_identical_and_still_role_gated(env, monkeypatch):
    srv, c, keys = env
    adm = {"X-API-Key": keys["ADM"]}
    fast, slow = _both(srv, c, monkeypatch, "/api/admin/tools", adm)
    _assert_identical(fast, slow)
    assert fast.json()["count"] == 100
    assert c.get("/api/admin/tools", headers={"X-API-Key": keys["TENANT"]}).status_code == 403
    assert c.get("/api/admin/tools").status_code in (401, 403)


def test_the_routes_no_longer_walk_the_generic_encoder(env, monkeypatch):
    """The measured hot spot is gone: FastAPI's serializer is not invoked for
    these routes (it was called ~17,000 times per /api/agents request)."""
    srv, c, keys = env
    import fastapi.routing as fr
    calls = {"n": 0}
    real = fr.jsonable_encoder

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(fr, "jsonable_encoder", counting)
    assert c.get("/api/agents").status_code == 200
    assert c.get("/api/admin/tools", headers={"X-API-Key": keys["ADM"]}).status_code == 200
    assert calls["n"] == 0


class _Color(enum.Enum):
    RED = "red"


@pytest.mark.parametrize("content", [
    {"when": datetime.datetime(2026, 11, 9, 1, 2, 3)},
    {"set": {1}},
    {"enum": _Color.RED},
    {"nested": [{"d": datetime.date(2026, 1, 1)}]},
])
def test_non_native_values_fall_back_to_the_generic_encoder(content):
    import server
    fast = server._native_json_response(content)
    assert fast.body == _generic(content).body
    assert fast.media_type == "application/json"


@pytest.mark.parametrize("content", [
    {"a": 1, "b": [1.5, True, None, "x"], "c": {"d": "é"}},
    {"t": (1, 2)},
    {"unicode": "وكيل"},
    {},
])
def test_native_values_render_exactly_like_the_generic_encoder(content):
    import server
    assert server._native_json_response(content).body == _generic(content).body


def test_nan_is_still_rejected_like_before():
    """allow_nan=False on both paths: a NaN is a server error, not silent
    invalid JSON — behaviour unchanged."""
    import server
    with pytest.raises(ValueError):
        server._native_json_response({"x": float("nan")})
    with pytest.raises(ValueError):
        _generic({"x": float("nan")})
