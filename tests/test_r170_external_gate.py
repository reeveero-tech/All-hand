"""R-170 (batch 17): the completion gate also covers the EXTERNAL-agent path.

Gap found during the 2026-10-29 takeover audit: ``ExternalAgentAdapter._execute``
returned ``ok=True`` for ANY non-empty completion from the remote service and
counted it as ``tasks_completed`` — it never called
``apply_output_verification``. Every other path that declares success
(RealAgent / specialist runtime -> server ``_run_task``, DAG, hub) goes through
the gate, so an external agent answering "Task completed successfully." with no
machine evidence was the one path where forged success still became a
``completed`` task while the gate was ON (default since PR #280).

These are LOCAL INTEGRATION tests: a real aiohttp server on 127.0.0.1 speaks the
OpenAI-compatible protocol, and the adapter performs a real HTTP request
(``allow_private=True``). No mock of the adapter or the gate.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time

import pytest
from aiohttp import web

from agent_civilization.agents.external_adapter import ExternalAgentAdapter

FORGED = "Task completed successfully."
HONEST = "The capital of France is Paris."


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve(content: str):
    box = {"calls": 0}

    async def handler(request):
        await request.read()
        box["calls"] += 1
        body = json.dumps({"choices": [{"message": {"content": content}}], "model": "m"})
        return web.Response(status=200, text=body, content_type="application/json")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, port, box


async def _run(content: str, task: dict):
    runner, port, box = await _serve(content)
    try:
        a = ExternalAgentAdapter("ext-test", f"http://127.0.0.1:{port}/v1/chat",
                                 allow_private=True)
        out = await a.execute(task)
        return a, out, box
    finally:
        await runner.cleanup()


@pytest.fixture
def gate_default(monkeypatch):
    """The production default: variable unset -> gate ON (PR #280)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)


async def test_forged_success_from_external_agent_is_rejected(gate_default):
    a, out, box = await _run(FORGED, {"prompt": "do the thing"})
    assert box["calls"] == 1                       # one provider call, no retry
    assert out["ok"] is False
    res = out["result"]
    assert res.get("error") == "output_verification_failed"
    assert res["verification"]["ok"] is False
    assert a.stats["tasks_completed"] == 0         # success NOT counted
    assert a.stats["tasks_failed"] == 1
    assert a.status == "idle"


async def test_honest_external_answer_is_accepted(gate_default):
    a, out, _ = await _run(HONEST, {"prompt": "capital of France?"})
    assert out["ok"] is True
    assert out["result"]["answer"] == HONEST
    assert out["result"]["external_agent"] == "ext-test"
    assert a.stats["tasks_completed"] == 1
    assert a.stats["tasks_failed"] == 0


async def test_arabic_forged_success_is_rejected(gate_default):
    a, out, _ = await _run("تم بنجاح.", {"prompt": "نفذ المهمة"})
    assert out["ok"] is False
    assert a.stats["tasks_completed"] == 0


async def test_declared_requirement_is_enforced(gate_default):
    """An internal caller's declared requirement applies to external output too."""
    a, out, _ = await _run(HONEST, {"prompt": "q", "verification": {"citations": True}})
    assert out["ok"] is False                      # no citations in the answer
    assert a.stats["tasks_completed"] == 0


async def test_requirements_captured_before_the_call(gate_default):
    """Mutating the task during the call cannot relax the captured requirement."""
    runner, port, _ = await _serve(HONEST)
    try:
        a = ExternalAgentAdapter("ext-test", f"http://127.0.0.1:{port}/v1/chat",
                                 allow_private=True)
        task = {"prompt": "q", "verification": {"citations": True}}
        fut = asyncio.ensure_future(a.execute(task))
        await asyncio.sleep(0)
        task["verification"] = {}                  # attempted relaxation mid-flight
        out = await fut
    finally:
        await runner.cleanup()
    assert out["ok"] is False


async def test_rollback_flag_restores_previous_behaviour(monkeypatch):
    """Documented rollback (OPERATIONS.md): AGOS_OUTPUT_VALIDATORS=0."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    a, out, _ = await _run(FORGED, {"prompt": "x"})
    assert out["ok"] is True
    assert "verification" not in out["result"]
    assert a.stats["tasks_completed"] == 1


async def test_transport_failure_untouched_by_gate(gate_default):
    """An honest failure stays a failure with its fixed reason (gate does not touch it)."""
    a = ExternalAgentAdapter("ext-test", f"http://127.0.0.1:{_free_port()}/v1/chat",
                             allow_private=True)
    out = await a.execute({"prompt": "x"})
    assert out["ok"] is False
    assert out["result"]["error"] != "output_verification_failed"
    assert "verification" not in out["result"]
    assert a.stats["tasks_failed"] == 1


def test_server_run_task_marks_forged_external_task_failed(tenant_auth, monkeypatch):
    """End to end through server._run_task: the task record ends ``failed``."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    import server

    async def scenario():
        runner, port, _ = await _serve(FORGED)
        try:
            adapter = ExternalAgentAdapter("ext-e2e", f"http://127.0.0.1:{port}/v1/chat",
                                           allow_private=True)
            monkeypatch.setattr(server, "EXTERNAL_AGENTS", [adapter])
            monkeypatch.setattr(server, "TASKS", {})
            tid = "r170extforged1"
            server.TASKS[tid] = {"id": tid, "specialization": "external", "prompt": "p",
                                 "status": "queued", "created_at": time.time(),
                                 "result": None, "tenant_id": tenant_auth.tenant}
            await server._run_task(tid, "external", "p", None)
            return dict(server.TASKS[tid])
        finally:
            await runner.cleanup()

    rec = asyncio.run(scenario())
    assert rec["status"] == "failed", rec
    assert rec["result"]["ok"] is False
    assert rec["result"]["result"]["error"] == "output_verification_failed"
