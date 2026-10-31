"""R-171 batch 2 (D-112): the tool registry governs the REAL execution path.

Before this batch the registry (batch 1, D-108) was a catalogue only: every
caller imported ``real_dispatcher.dispatch`` and ran the handler whatever the
registry said. These tests prove that every handler run now passes
``tool_runtime.dispatch``, which refuses (fail-closed, the handler never runs):

* a tool whose lifecycle is not executable (discovered / failed / disabled) or
  whose health is ``disabled``;
* a handler with NO registry record (unregistered = not runnable);
* a handler that is not the object the record's ``implementation_ref`` names;
* ``self_developer`` when any tool its self-test reaches is not executable
  (its probes call the handlers directly inside the frozen dispatcher);

and that ``AGOS_DISABLED_TOOLS`` disables tools at seeding (audited), while an
executable tool returns exactly what the frozen dispatcher returns. The
frozen dispatcher is read, never modified (R-169).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from agent_civilization.core import tool_runtime
from agent_civilization.core.agents import real_dispatcher

ROOT = Path(__file__).resolve().parents[1]
FROZEN = "agent_civilization/core/agents/real_dispatcher.py"
GATE = "agent_civilization/core/tool_runtime.py"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield
    tool_runtime.reset_runtime_registry()


class _Spy:
    def __init__(self):
        self.calls = []
        self.real = real_dispatcher.dispatch

    async def __call__(self, spec, work):
        self.calls.append(spec)
        return await self.real(spec, work)


# -- no bypass -----------------------------------------------------------------

def _dispatch_importers():
    hits = []
    files = [ROOT / "server.py"] + sorted((ROOT / "agent_civilization").rglob("*.py"))
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel in (FROZEN, GATE):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("real_dispatcher"):
                for a in node.names:
                    if a.name in ("dispatch", "_HANDLERS", "*") or (
                            a.name.startswith("_") and a.name[1:] in real_dispatcher._HANDLERS):
                        hits.append(f"{rel}:{node.lineno}:{a.name}")
            if isinstance(node, ast.Attribute) and node.attr in ("dispatch", "_HANDLERS") \
                    and isinstance(node.value, ast.Name) and node.value.id == "real_dispatcher":
                hits.append(f"{rel}:{node.lineno}:real_dispatcher.{node.attr}")
    return hits


def test_no_module_reaches_a_handler_except_through_the_gate():
    assert _dispatch_importers() == []


def test_the_gate_calls_the_frozen_dispatch_by_module_attribute():
    src = (ROOT / GATE).read_text(encoding="utf-8")
    assert "real_dispatcher.dispatch(" in src


# -- seeded runtime registry ------------------------------------------------------

def test_runtime_registry_is_seeded_with_every_handler_and_all_are_executable():
    reg = tool_runtime.runtime_registry()
    assert len(reg) == len(real_dispatcher._HANDLERS) == 20
    for spec in real_dispatcher._HANDLERS:
        ok, reason = tool_runtime.check(spec)
        assert ok, (spec, reason)


def test_executable_tool_returns_exactly_the_frozen_result(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    work = {"prompt": "numbers 1 2 3 4"}
    via_gate = run(tool_runtime.dispatch("analyst", dict(work)))
    direct = run(spy.real("analyst", dict(work)))
    assert spy.calls == ["analyst"]
    assert via_gate == direct
    assert tool_runtime.stats()["executed"]["agos.internal.analyst@1.0.0"] == 1


def test_unknown_specialization_keeps_the_none_contract(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    assert run(tool_runtime.dispatch("no_such_spec", {})) is None
    assert spy.calls == []


# -- refusals (the handler never runs) ---------------------------------------------

@pytest.mark.parametrize("state", ["disabled", "failed"])
def test_non_executable_lifecycle_is_refused(state, monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    tool_runtime.runtime_registry().update_status(
        "agos.internal.analyst", "1.0.0", actor="test", lifecycle_state=state)
    out = run(tool_runtime.dispatch("analyst", {"prompt": "1 2 3"}))
    assert spy.calls == []
    assert out["ok"] is False
    assert "agos.internal.analyst@1.0.0" in out["error"] and state in out["error"]
    assert tool_runtime.stats()["refused"]["agos.internal.analyst@1.0.0"] == 1


def test_disabled_health_is_refused(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    tool_runtime.runtime_registry().update_status(
        "agos.internal.monitor", "1.0.0", actor="test", health_status="disabled")
    out = run(tool_runtime.dispatch("monitor", {}))
    assert spy.calls == [] and out["ok"] is False and "health_status=disabled" in out["error"]


def test_unregistered_handler_is_refused_fail_closed(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    tool_runtime.runtime_registry().remove("agos.internal.designer", "1.0.0", actor="test")
    out = run(tool_runtime.dispatch("designer", {"prompt": "blue"}))
    assert spy.calls == [] and out["ok"] is False and "not registered" in out["error"]


def test_implementation_mismatch_is_refused(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)

    async def impostor(work):
        return {"ok": True, "answer": "not the registered implementation"}
    monkeypatch.setitem(real_dispatcher._HANDLERS, "analyst", impostor)
    out = run(tool_runtime.dispatch("analyst", {"prompt": "1 2"}))
    assert spy.calls == [] and out["ok"] is False and "implementation" in out["error"]


def test_self_developer_is_refused_when_a_reached_tool_is_not_executable(monkeypatch):
    reached = sorted(real_dispatcher._SELF_PROBES)
    assert reached, "self_developer probes nothing?"
    tool_runtime.runtime_registry().update_status(
        f"agos.internal.{reached[0]}", "1.0.0", actor="test", lifecycle_state="disabled")
    ok, reason = tool_runtime.check("self_developer")
    assert not ok and f"agos.internal.{reached[0]}" in reason


# -- operational control -------------------------------------------------------------

def test_env_disables_tools_at_seeding_and_is_audited(monkeypatch):
    monkeypatch.setenv("AGOS_DISABLED_TOOLS", "analyst, agos.internal.monitor ,nope")
    tool_runtime.reset_runtime_registry()
    reg = tool_runtime.runtime_registry()
    assert reg.get("agos.internal.analyst").lifecycle_state == "disabled"
    assert reg.get("agos.internal.monitor").lifecycle_state == "disabled"
    assert reg.get("agos.internal.designer").lifecycle_state == "registered"
    actors = {e["actor"] for e in reg.audit() if e["op"] == "update"}
    assert actors == {"env:AGOS_DISABLED_TOOLS"}
    assert tool_runtime.stats()["unknown_disabled"] == ["nope"]
    assert not tool_runtime.check("analyst")[0] and tool_runtime.check("designer")[0]


# -- both production call paths -------------------------------------------------------

def test_server_agent_path_is_refused_when_disabled(monkeypatch):
    import server
    monkeypatch.setenv("AGOS_DISABLED_TOOLS", "analyst")
    tool_runtime.reset_runtime_registry()
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    agent = server._pick_agent("analyst")
    out = run(server._execute_bounded(agent, {"prompt": "numbers 1 2 3"}))
    assert spy.calls == []
    assert out["ok"] is False
    assert "agos.internal.analyst@1.0.0" in str(out)


def test_base_agent_wrapper_path_is_gated(monkeypatch):
    from agent_civilization.core.agents.base_agent import _wrap_process_task
    monkeypatch.setenv("AGOS_DISABLED_TOOLS", "analyst")
    tool_runtime.reset_runtime_registry()
    spy = _Spy()
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)

    async def legacy(self, work):
        return {"ok": True, "legacy": True}
    wrapped = _wrap_process_task(legacy)

    class Dummy:
        specialization = "analyst"
    out = run(wrapped(Dummy(), {"prompt": "1 2"}))
    assert spy.calls == [] and out["ok"] is False and "disabled" in out["error"]
