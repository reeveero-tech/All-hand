"""R-158 (D-070): regression tests for gaps reviewed by the baseline.

G-157-1  /api/system/status with a broken civilization object: the gap (a
         generic 500 instead of a structured error) stays OPEN; this test
         proves the boundary that matters today — the 500 carries no internal
         text (no exception message, no traceback) and the readiness probe
         still answers with its fixed reason.
G-157-5  the R-149 crash-injection tests now patch both runtime bindings; this
         test proves the helper is effective on the specialists runtime, i.e.
         an injected crash really reaches the specialist template.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import server
from agent_civilization.agents.real import create_civilization

SECRET = "R158-internal-detail-/etc/shadow"


class _Broken(dict):
    def values(self):
        raise RuntimeError(SECRET)


def test_g157_1_system_status_500_has_no_internal_text(monkeypatch):
    monkeypatch.setattr(server, "CIVILIZATION", _Broken(a=[1]))
    with TestClient(server.app, raise_server_exceptions=False) as c:
        r = c.get("/api/system/status")
        ready = c.get("/health/ready")
    assert r.status_code == 500
    assert SECRET not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    assert ready.status_code == 503 and ready.json() == {"status": "not_ready", "reason": "readiness check failed"}


def test_g157_5_injection_helper_reaches_the_specialists_runtime(monkeypatch):
    import tests.test_r149_security_qa_gaps as r149

    calls = []

    def crashing(spec, work):
        calls.append(spec)
        raise RuntimeError(SECRET)
    r149._patch_handler_dispatch(monkeypatch, crashing)
    agent = create_civilization(1, impl="specialists")["test_runner"][0]
    out = asyncio.run(agent.execute({"prompt": "run unit tests"}))
    assert calls == ["test_runner"]                       # the injection is not inert
    assert out["ok"] is False and out["result"] == {"ok": False, "error": "RuntimeError"}
    assert SECRET not in str(out)
    real = create_civilization(1, impl="real")["test_runner"][0]
    out = asyncio.run(real.execute({"prompt": "run unit tests"}))
    assert calls == ["test_runner", "test_runner"] and out["result"] == {"ok": False, "error": "RuntimeError"}
