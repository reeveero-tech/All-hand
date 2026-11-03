"""R-175 batch 3: the remaining adversarial list — the red-first tests.

The spec (10K_TOOLS_SPEC_AR.md) demands testing «tool name collisions،
malformed output، slow/hung tool، rate limiting، provider compromise».
Batch 2 covered poisoning/misleading descriptions/schema drift; this batch
covers the rest. The RED is the malformed-output enforcement (a new gate
check — D-127): on main the malformed result flows through UNCHECKED, so
these tests fail there. The slow-tool/collision/rate-limit/compromise tests
pin the existing fail-closed defenses so they cannot be silently weakened.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import tool_runtime
from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.tool_certification import certify, state_counts

DISPATCHER_URI = "agent_civilization/core/agents/real_dispatcher"
REF = f"{__name__}:"
CAP = "adversarial"


def run(coro):
    return asyncio.run(coro)


# -- module-level handlers (the implementation_ref must resolve to them) ------

async def h_returns_string(work):
    return "I am not an object"


async def h_returns_object(work):
    return {"ok": True, "answer": 42}


async def h_returns_bool(work):
    return True


async def h_slow(work):
    await asyncio.sleep(5.0)
    return {"ok": True}


async def h_leaks(work):
    return {"ok": False, "error": "upstream failed: password=s3cr3t-PW-x"}


async def h_huge(work):
    return {"ok": True, "blob": "x" * 2_000_000}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", str(tmp_path / "tools.sqlite3"))
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield str(tmp_path / "tools.sqlite3")
    tool_runtime.reset_runtime_registry()


@pytest.fixture()
def keys():
    import secrets

    def _sha(key: str) -> str:
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()

    return {"TENANT": "t-" + secrets.token_urlsafe(24),
            "ADM": "adm-" + secrets.token_urlsafe(24)}


@pytest.fixture()
def client(monkeypatch, keys, db):
    import importlib
    from fastapi.testclient import TestClient

    def _sha(key: str) -> str:
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()

    # a LOW window so the rate-limit test can trip it in seconds:
    monkeypatch.setenv("AGOS_RATE_LIMIT_REQUESTS", "5")
    monkeypatch.setenv("AGOS_TENANT_KEYS", __import__("json").dumps([
        {"tenant": "tenant_a", "key_sha256": _sha(keys["TENANT"]), "role": "user"},
        {"tenant": "ops", "key_sha256": _sha(keys["ADM"]), "role": "admin"},
    ]))
    import server
    srv = importlib.reload(server)
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c
    monkeypatch.delenv("AGOS_RATE_LIMIT_REQUESTS", raising=False)
    monkeypatch.delenv("AGOS_TENANT_KEYS", raising=False)
    importlib.reload(server)


def _reg():
    return tool_runtime.runtime_registry()


def _record(tool_id, spec, **overrides):
    base = {"tool_id": tool_id, "version": "1.0.0", "name": f"{spec} tool",
            "description": f"{spec} adversarial test tool",
            "source": {"kind": "internal", "uri": DISPATCHER_URI},
            "capabilities": [CAP], "input_schema": {"type": "object"},
            "output_schema": {"type": "object"}, "auth_scope": [],
            "tenant_scope": "request_tenant", "risk_class": "low",
            "side_effects": [], "read_only": True, "idempotent": True,
            "requires_approval": False, "timeout_ms": 30_000,
            "network_allowlist": [], "health_status": "healthy",
            "test_status": "not_tested",
            "implementation_ref": REF + "missing",
            "lifecycle_state": "registered"}
    base.update(overrides)
    return base


def _tool(monkeypatch, spec, handler, **fields):
    rec = _reg().register(_record(f"agos.internal.{spec}", spec,
                                 implementation_ref=REF + handler.__name__,
                                 **fields), actor="test:r175b3")
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    return rec


_EVIDENCE = "smoke pass; contract v1.0 verified; security: allowlist+timeout"


# -- MALFORMED OUTPUT (the new enforcement — the red) ------------------------------

def test_malformed_output_is_refused_counted_and_withheld(db, monkeypatch):
    rec = _tool(monkeypatch, "adv.malformed", h_returns_string)
    result = run(tool_runtime.dispatch("adv.malformed", {"prompt": "x"}))
    assert result["ok"] is False
    assert "output_schema type" in result["error"]
    key = f"{rec.tool_id}@{rec.version}"
    stats = tool_runtime.stats()
    assert stats["output_malformed"][key] == 1
    assert stats["executed"][key] == 1


def test_conforming_output_passes_unchanged(db, monkeypatch):
    rec = _tool(monkeypatch, "adv.conform", h_returns_object)
    result = run(tool_runtime.dispatch("adv.conform", {"prompt": "x"}))
    assert result == {"ok": True, "answer": 42}
    key = f"{rec.tool_id}@{rec.version}"
    assert tool_runtime.stats()["output_malformed"].get(key, 0) == 0


def test_undeclared_type_is_never_checked(db, monkeypatch):
    # A schema that declares a union (or no type at all) is nothing the
    # mechanical check can pin down: the gate checks only the declared
    # top-level type. The registry refuses a typeless schema outright
    # (structural check), and a declared union conforms for any member:
    with pytest.raises(Exception):
        _tool(monkeypatch, "adv.typeless", h_returns_string,
              output_schema={"items": {"type": "string"}})
    rec = _tool(monkeypatch, "adv.union", h_returns_string,
                output_schema={"type": ["object", "string"]})
    result = run(tool_runtime.dispatch("adv.union", {"prompt": "x"}))
    assert result == "I am not an object"
    key = f"{rec.tool_id}@{rec.version}"
    assert tool_runtime.stats()["output_malformed"].get(key, 0) == 0


def test_boolean_is_not_a_number(db, monkeypatch):
    rec = _tool(monkeypatch, "adv.boolnum", h_returns_bool,
                output_schema={"type": "number"})
    result = run(tool_runtime.dispatch("adv.boolnum", {"prompt": "x"}))
    assert result["ok"] is False
    key = f"{rec.tool_id}@{rec.version}"
    assert tool_runtime.stats()["output_malformed"][key] == 1


def test_malformed_output_lowers_the_quality_score(db, monkeypatch):
    from agent_civilization.core.tool_quality import runtime_quality
    rec = _tool(monkeypatch, "adv.qual", h_returns_string)
    run(tool_runtime.dispatch("adv.qual", {"prompt": "x"}))   # malformed
    run(tool_runtime.dispatch("adv.qual", {"prompt": "x"}))   # malformed
    quality = runtime_quality()(rec)
    assert quality == 0.0            # 2 executed, 2 malformed -> 0.0


# -- SLOW / HUNG TOOL ---------------------------------------------------------------

def test_slow_tool_is_killed_at_its_registered_budget(db, monkeypatch):
    rec = _tool(monkeypatch, "adv.slow", h_slow, timeout_ms=150)
    result = run(tool_runtime.dispatch("adv.slow", {"prompt": "x"}))
    assert result["ok"] is False
    assert "timeout_ms=150" in result["error"]
    key = f"{rec.tool_id}@{rec.version}"
    assert tool_runtime.stats()["timed_out"][key] == 1


# -- NAME COLLISIONS -----------------------------------------------------------------

def test_display_name_collision_cannot_hijack_identity(db, monkeypatch):
    # two tools MAY share a display name (it is not identity); the anchors
    # are tool_id + implementation_ref — and both hijack paths are refused:
    _tool(monkeypatch, "adv.first", h_returns_object)
    with pytest.raises(Exception):
        # same tool_id + version: not a second tool, a duplicate key:
        _reg().register(_record("agos.internal.adv.first", "adv.first"),
                        actor="test:r175b3")
    with pytest.raises(Exception):
        # same implementation_ref under a different tool_id: alias refused:
        _reg().register(_record("agos.internal.adv.hijack", "adv.hijack",
                                name="adv.first tool",
                                implementation_ref=REF + "h_returns_object"),
                        actor="test:r175b3")
    # the collision itself (same NAME, different id/ref) is allowed and both
    # are counted as SEPARATE tools:
    _reg().register(_record("agos.internal.adv.second", "adv.second",
                            name="adv.first tool"), actor="test:r175b3")
    counts = state_counts(_reg())
    assert counts["unique_tools"] >= 2


def test_collision_does_not_confuse_certification(db, monkeypatch):
    _tool(monkeypatch, "adv.c1", h_returns_object)
    _reg().register(_record("agos.internal.adv.c2", "adv.c2",
                            name="adv.c1 tool"), actor="test:r175b3")
    r1 = certify(_reg(), "agos.internal.adv.c1", "1.0.0", actor="test:r175b3",
                 evidence=_EVIDENCE)
    assert r1.lifecycle_state == "certified"
    # the sibling (same display name) is a SEPARATE tool, still uncertified:
    r2 = _reg().get("agos.internal.adv.c2", "1.0.0")
    assert r2.lifecycle_state == "registered"
    assert state_counts(_reg())["lifecycle"]["certified"] == 1


# -- RATE LIMITING --------------------------------------------------------------------

def test_hammering_client_is_rate_limited(client, keys):
    # the dispatch surface is behind the per-client rate limiter: a client
    # that exceeds the window is refused fast (429) — the flood never
    # reaches the tools:
    import os
    limit = int(os.environ["AGOS_RATE_LIMIT_REQUESTS"])
    codes = []
    for _ in range(limit + 3):
        r = client.get("/api/tasks", headers={"X-API-Key": keys["TENANT"]})
        codes.append(r.status_code)
    assert 429 in codes
    assert codes[-1] == 429


# -- PROVIDER COMPROMISE ---------------------------------------------------------------

def test_hostile_provider_error_text_is_redacted(db, monkeypatch):
    monkeypatch.setenv("AGOS_API_KEY", "s3cr3t-PW-x")
    _tool(monkeypatch, "adv.leak", h_leaks)
    result = run(tool_runtime.dispatch("adv.leak", {"prompt": "x"}))
    assert result["ok"] is False
    assert "s3cr3t-PW-x" not in result["error"]
    assert "<redacted>" in result["error"]


def test_hostile_provider_oversized_output_is_capped_and_withheld(db, monkeypatch):
    rec = _tool(monkeypatch, "adv.huge", h_huge)
    result = run(tool_runtime.dispatch("adv.huge", {"prompt": "x"}))
    assert result["ok"] is False
    assert "output cap" in result["error"]
    key = f"{rec.tool_id}@{rec.version}"
    assert tool_runtime.stats()["output_capped"][key] == 1
