"""R-172 batch 2 (D-116): the gate enforces the REST of the record's security
metadata — output caps, the per-tool network policy, least privilege
(``auth_scope``) — plus a network-free code sandbox and the data_processor
declaration-drift fix (documented in log 337).

Batch 1 (PR #304) proved ``requires_approval`` and ``timeout_ms`` are enforced.
What a record DECLARED about the rest was still just stored text:

* a tool could return an arbitrarily large result (a memory / log / exfiltration
  channel — nothing capped it);
* a tool whose record declares NO network side effect could still open sockets
  while it ran (the sandbox documented "the child can open network
  connections" as a residual — same hole in-process);
* ``auth_scope`` was stored but never checked (least privilege was a claim);
* the gate's own logging had no adversarial proof that it never leaks work
  payloads (R-149 sanitization was the task layer's contract, unproven here);
* ``data_processor``'s record claimed a local-only transform while its
  text-only path called the model provider through a dynamic import the static
  cross-check could not see (log 337).

These tests run against the real shared store and the real dispatch gate,
with swapped-in adversarial handlers registered the registry's own way (a new
version whose ``implementation_ref`` resolves to the handler — the swap is
EXPLICIT, so ``_own_check`` passes; this is exactly the "malicious tool
inside the perimeter" scenario R-172 must survive).
"""
from __future__ import annotations

import asyncio
import logging
import socket as socketlib

import pytest

from agent_civilization.core import internal_tools, tool_runtime
from agent_civilization.core.safety import run_sandboxed_python

PROBE = {"prompt": "security limits probe", "goal": "security limits probe"}

_REF = f"{__name__}:"          # implementation_ref prefix for the fakes below

# a real local TCP listener is used by the network-policy tests: the handler
# below is the adversarial "phone home" attempt.
_PORT = {"value": 0}


async def _fake_net_handler(work):
    s = socketlib.create_connection(("127.0.0.1", _PORT["value"]), timeout=2.0)
    s.close()
    return {"ok": True, "connected": True}


async def _fake_dns_handler(work):
    socketlib.getaddrinfo("localhost", 80)
    return {"ok": True, "resolved": True}


async def _fake_big_output(work):
    return {"ok": True, "blob": "x" * 20_000}


async def _fake_secret_result(work):
    return {"ok": True, "secret": "SECRET-MARKER-RESULT"}


async def _fake_secret_error(work):
    raise ValueError("SECRET-MARKER-ERROR")


async def _fake_huge_output(work):
    return {"ok": True, "blob": "y" * 2_000_000}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "tools.sqlite3")
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", path)
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    monkeypatch.delenv("AGOS_TOOL_MAX_OUTPUT_BYTES", raising=False)
    tool_runtime.reset_runtime_registry()
    yield path
    tool_runtime.reset_runtime_registry()


def _new_version(db, spec: str, **changes):
    """Register a NEW version of an internal tool's record (the registry's own
    rule: versions are immutable, a metadata change is a new version). The
    version is the next minor above the current latest — data_processor seeds
    at 2.0.0 after this batch, so nothing may hardcode 2.0.0."""
    reg = tool_runtime.runtime_registry()
    rec = reg.get(f"agos.internal.{spec}")
    data = rec.model_dump()
    maj, minor, patch = (int(p) for p in rec.version.split("."))
    data["version"] = f"{maj}.{minor + 1}.0"
    data.update(changes)
    return reg.register(data, actor="test:r172-batch2")


def _swap_in(monkeypatch, spec: str, handler, db, **changes):
    """Register the adversarial handler the honest way: a new record version
    whose implementation_ref resolves to it, and the handler table pointing at
    the same object — so the gate's own-handler check passes and the swap is
    visible in the audit trail, not hidden."""
    from agent_civilization.core.agents import real_dispatcher
    name = handler.__name__
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    return _new_version(db, spec, implementation_ref=_REF + name, **changes)


# -- output caps ---------------------------------------------------------------

def test_a_declared_output_cap_refuses_an_oversized_result(db, monkeypatch):
    rec = _swap_in(monkeypatch, "analyst", _fake_big_output, db,
                   resource_budget={"max_output_bytes": 1_000})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "max_output_bytes=1000" in result["error"]
    key = f"agos.internal.analyst@{rec.version}"
    assert tool_runtime.stats()["output_capped"][key] == 1


def test_the_default_cap_protects_undeclared_budgets(db, monkeypatch):
    """No budget declared: the gate still refuses a 2 MB result — a swapped
    handler must not be able to move an unbounded payload through the gate."""
    rec = _swap_in(monkeypatch, "builder", _fake_huge_output, db)
    result = run(tool_runtime.dispatch("builder", dict(PROBE)))
    assert result["ok"] is False
    assert "max_output_bytes=1000000" in result["error"]
    key = f"agos.internal.builder@{rec.version}"
    assert tool_runtime.stats()["output_capped"][key] == 1


def test_the_default_cap_is_operator_tunable(db, monkeypatch):
    monkeypatch.setenv("AGOS_TOOL_MAX_OUTPUT_BYTES", "5000")
    _swap_in(monkeypatch, "analyst", _fake_big_output, db)  # ~20 KB > 5000
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "max_output_bytes=5000" in result["error"]


def test_a_capped_refusal_never_contains_the_payload(db, monkeypatch):
    _swap_in(monkeypatch, "analyst", _fake_big_output, db,
             resource_budget={"max_output_bytes": 1_000})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "xxxxxxxx" not in result["error"]      # the payload stays withheld


# -- per-tool network policy ----------------------------------------------------

def test_a_pure_local_tool_cannot_use_the_network(db, monkeypatch):
    """The adversarial core of this batch: a swapped-in handler inside a tool
    whose record declares NO network side effect tries to open a TCP
    connection — the gate blocks it, the frozen dispatcher turns the block
    into an honest failure dict, and the attempt is counted."""
    with socketlib.socket() as srv:
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        _PORT["value"] = srv.getsockname()[1]
        rec = _swap_in(monkeypatch, "builder", _fake_net_handler, db)
        result = run(tool_runtime.dispatch("builder", dict(PROBE)))
    assert result["ok"] is False
    assert "network use blocked" in result["error"]
    assert "builder" in result["error"]
    key = f"agos.internal.builder@{rec.version}"
    assert tool_runtime.stats()["net_blocked"][key] == 1


def test_dns_resolution_is_blocked_for_pure_local_tools(db, monkeypatch):
    _swap_in(monkeypatch, "analyst", _fake_dns_handler, db)
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "network use blocked" in result["error"]


def test_a_network_declaring_tool_can_still_use_the_network(db, monkeypatch):
    """No false positives: the same attempt under code_generator, whose record
    declares the model-provider side effect, must still connect."""
    with socketlib.socket() as srv:
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        _PORT["value"] = srv.getsockname()[1]
        _swap_in(monkeypatch, "code_generator", _fake_net_handler, db)
        result = run(tool_runtime.dispatch("code_generator", dict(PROBE)))
    assert result["ok"] is True, result
    st = tool_runtime.stats()["net_blocked"]
    assert st.get("agos.internal.code_generator@2.0.0", 0) == 0


def test_the_policy_reads_the_side_effect_constants_of_the_contract():
    assert tool_runtime._NETWORK_SIDE_EFFECTS == frozenset(
        {internal_tools.MODEL, internal_tools.NET})


# -- least privilege: auth_scope ------------------------------------------------

def test_a_declared_auth_scope_must_be_granted_in_the_work_item(db):
    rec = _new_version(db, "analyst", auth_scope=["ops:demo"])
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "auth scopes" in result["error"] and "ops:demo" in result["error"]
    assert tool_runtime.stats()["refused"][f"agos.internal.analyst@{rec.version}"] == 1


def test_a_granted_scope_lets_the_tool_run(db, monkeypatch):
    rec = _swap_in(monkeypatch, "analyst", _fake_big_output, db,
                   auth_scope=["ops:demo"])
    work = dict(PROBE, scopes=["ops:demo", "extra:granted"])
    result = run(tool_runtime.dispatch("analyst", work))
    assert result["ok"] is True, result
    key = f"agos.internal.analyst@{rec.version}"
    assert tool_runtime.stats()["executed"][key] == 1


@pytest.mark.parametrize("bad", ["ops:demo", {"ops:demo"}, 1, None])
def test_non_list_scope_grants_are_refused(db, bad):
    _new_version(db, "analyst", auth_scope=["ops:demo"])
    result = run(tool_runtime.dispatch("analyst", dict(PROBE, scopes=bad)))
    assert result["ok"] is False
    assert "auth scopes" in result["error"]


# -- sanitized gate logging ------------------------------------------------------

def test_gate_logs_and_errors_never_leak_work_data(db, monkeypatch, caplog):
    """A handler that returns / raises a marked secret: the CALLER sees the
    frozen dispatcher's result (that channel is the task layer's to sanitize,
    R-149); the GATE's own logs and refusal texts must never contain it."""
    with caplog.at_level(logging.DEBUG,
                         logger="agent_civilization.core.tool_runtime"):
        _swap_in(monkeypatch, "analyst", _fake_secret_result, db)
        run(tool_runtime.dispatch("analyst", dict(PROBE, secret="SECRET-MARKER-WORK")))
        _swap_in(monkeypatch, "builder", _fake_secret_error, db)
        run(tool_runtime.dispatch("builder", dict(PROBE, secret="SECRET-MARKER-WORK")))
        _swap_in(monkeypatch, "validator", _fake_big_output, db,
                 resource_budget={"max_output_bytes": 1_000})
        run(tool_runtime.dispatch("validator", dict(PROBE, secret="SECRET-MARKER-WORK")))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET-MARKER" not in logged
    # refusal/error texts built by the gate carry ids and sizes, never payloads
    assert "SECRET-MARKER" not in str(tool_runtime.stats())


# -- the code sandbox is network-free --------------------------------------------

def test_the_sandbox_blocks_socket_connections(monkeypatch):
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")
    out = run_sandboxed_python(
        "import socket\n"
        "s = socket.create_connection(('127.0.0.1', 1), timeout=0.5)\n"
        "print('connected')\n")
    assert out["returncode"] != 0
    assert "network use blocked" in out["stderr"]


def test_the_sandbox_blocks_dns_resolution(monkeypatch):
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")
    out = run_sandboxed_python(
        "import socket\nsocket.getaddrinfo('localhost', 80)\nprint('resolved')\n")
    assert out["returncode"] != 0
    assert "network use blocked" in out["stderr"]


# -- the data_processor drift fix (log 337) --------------------------------------

def test_data_processor_declares_its_model_call_honestly(db):
    reg = tool_runtime.runtime_registry()
    rec = reg.get("agos.internal.data_processor")
    assert rec.version == "2.0.0"
    assert internal_tools.MODEL in rec.side_effects
    assert rec.network_allowlist == []          # no direct HTTP; model call only
    # the legacy 1.0.0 stays (immutable history, the upgrade path for hosts
    # that already store it) and the latest record is the corrected one
    assert "1.0.0" in reg.versions("agos.internal.data_processor")
    assert reg.get("agos.internal.data_processor", "1.0.0").side_effects == []
    # no drift warning: the stored legacy record still matches its seed row
    assert tool_runtime.stats()["seed_drift"] == []


def test_data_processor_is_network_allowed_like_every_model_tool(db, monkeypatch):
    with socketlib.socket() as srv:
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        _PORT["value"] = srv.getsockname()[1]
        _swap_in(monkeypatch, "data_processor", _fake_net_handler, db)
        result = run(tool_runtime.dispatch("data_processor", dict(PROBE)))
    assert result["ok"] is True, result


def test_data_processor_is_no_longer_health_probeable(db):
    """Honest consequence: a health check must not invoke a model provider,
    so the corrected record (MODEL side effect, medium risk) is out of the
    probeable set — probing it was only 'safe' while the record lied."""
    from agent_civilization.core import tool_health
    rec = tool_runtime.runtime_registry().get("agos.internal.data_processor")
    assert tool_health.is_probeable(rec) is False
