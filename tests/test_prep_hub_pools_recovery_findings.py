"""PREP audit findings (PR #221) as executable reproductions — NO runtime change.

Each test asserts the DESIRED behaviour and is marked ``xfail(strict=True)``:
today it fails because of the documented defect (that failure IS the
reproduction). When a fix lands (R-176/R-179/R-181), the test XPASSes, strict
mode turns that into a suite failure, and the fixer must drop the marker — the
test then stays as the permanent anti-regression guard.

Findings: docs/audit/M8/prep/PREP_AUDIT_HUB_POOLS_RECOVERY.md (H1-H6, P1/P4, C1-C4).
H5 (broadcast cost), C1 and C3 (capacity arithmetic) are not correctness
assertions and are not encoded here; see the document for their load-test plan.
"""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_civilization.core import pools  # noqa: E402
from agent_civilization.core.agents.base_agent import BaseAgent, Message  # noqa: E402
from agent_civilization.core.agents.communication_hub import CommunicationHub  # noqa: E402

Agent = type("FindingAgent", (BaseAgent,), {})


def _run(coro):
    return asyncio.run(coro)


async def _hub_with(*specs):
    hub = CommunicationHub()
    agents = []
    for name, active in specs:
        a = Agent(name, hub)
        a.is_active = active
        await hub.register_agent(a)
        agents.append(a)
    return hub, agents


@pytest.mark.xfail(strict=True, reason="PREP H1: delivery to an inactive agent is reported as success while the message is dropped")
def test_h1_delivery_to_inactive_agent_is_not_reported_as_success():
    async def go():
        hub, (idle,) = await _hub_with(("idle", False))
        r = await hub.send_message(Message(sender="s", recipient="idle", content={"action": "note"}))
        return r, idle._inbox.qsize(), hub.get_stats()
    r, queued, stats = _run(go())
    assert queued == 0  # precondition: the message really was not queued
    assert r.success is False and stats["messages_delivered"] == 0


@pytest.mark.xfail(strict=True, reason="PREP H2: broadcast counts silently dropped messages as delivered")
def test_h2_broadcast_counts_only_real_deliveries():
    async def go():
        hub, agents = await _hub_with(("on", True), ("off", False))
        await hub.send_message(Message(sender="x", recipient="broadcast", content={"action": "note"}))
        return hub.get_stats()["messages_delivered"], sum(a._inbox.qsize() for a in agents)
    counted, queued = _run(go())
    assert counted == queued == 1


@pytest.mark.xfail(strict=True, reason="PREP H3: broadcast echoes the message back to its sender")
def test_h3_broadcast_does_not_echo_to_sender():
    async def go():
        hub, (sender, other) = await _hub_with(("sender", True), ("other", True))
        await hub.send_message(Message(sender="sender", recipient="broadcast", content={"action": "note"}))
        return sender._inbox.qsize(), other._inbox.qsize()
    own, other = _run(go())
    assert other == 1 and own == 0


@pytest.mark.xfail(strict=True, reason="PREP H4: subscribe() stores callbacks that nothing ever invokes")
def test_h4_subscribers_are_notified_on_registration():
    calls = []

    async def go():
        hub = CommunicationHub()
        await hub.subscribe("agent_registered", lambda *a, **k: calls.append(a))
        await hub.register_agent(Agent("late", hub))
    _run(go())
    assert calls, "a registered subscriber was never called"


@pytest.mark.xfail(strict=True, reason="PREP H6: broadcast shares ONE mutable Message object across all inboxes")
def test_h6_broadcast_recipients_do_not_share_mutable_state():
    async def go():
        hub, agents = await _hub_with(("r0", True), ("r1", True))
        await hub.send_message(Message(sender="z", recipient="broadcast", content={"action": "note"}))
        m0, m1 = (a._inbox.get_nowait() for a in agents)
        m0.content["mutated_by"] = "r0"
        return m1.content
    assert "mutated_by" not in _run(go())


@pytest.fixture
def one_thread_local_pool(monkeypatch):
    saved = dict(pools._pools)
    monkeypatch.setenv("AGOS_POOL_LOCAL", "1")
    pools._pools.pop("local", None)
    yield
    ex = pools._pools.pop("local", None)
    if ex is not None:
        ex.shutdown(wait=False)
    pools._pools.update({k: v for k, v in saved.items() if k not in pools._pools})


@pytest.mark.xfail(strict=True, reason="PREP P1: a caller timeout does not free the pool thread (it stays busy until fn returns)")
def test_p1_caller_timeout_frees_the_pool_thread(one_thread_local_pool):
    release = threading.Event()

    async def go():
        try:
            await asyncio.wait_for(pools.run("local", release.wait, 5), timeout=0.05)
        except asyncio.TimeoutError:
            pass
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(pools.run("local", lambda: "fast"), timeout=0.5)
            return time.perf_counter() - t0
        except asyncio.TimeoutError:
            return None
        finally:
            release.set()
    waited = _run(go())
    assert waited is not None and waited < 0.2, "next call could not get the thread the timed-out caller abandoned"


@pytest.mark.xfail(strict=True, reason="PREP P4: no pool shutdown exists and the server lifespan does not close the pools")
def test_p4_pools_are_closed_on_server_shutdown():
    assert any(n in dir(pools) for n in ("shutdown", "close", "shutdown_all"))
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    life = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_lifespan")
    assert "pools" in ast.unparse(life)


def _reaper_ast():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_queue_reaper")


def test_c2_precondition_reaper_renews_leases():
    """Not a finding: guards the C2 reproduction below against silently passing
    because the renewal call was moved or renamed."""
    assert "renew_task_lease" in ast.unparse(_reaper_ast())


@pytest.mark.xfail(strict=True, reason="PREP C2: the result of renew_task_lease is discarded (a lost lease does not stop the run)")
def test_c2_lease_renewal_result_is_checked():
    bare = [n for n in ast.walk(_reaper_ast())
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Await)
            and "renew_task_lease" in ast.unparse(n.value)]
    assert not bare, "renew_task_lease is awaited as a bare statement; its result is ignored"


@pytest.mark.xfail(strict=True, reason="PREP C4: REAP >= LEASE (every lease expires before renewal) is accepted without warning")
def test_c4_inconsistent_lease_and_reap_configuration_is_flagged():
    env = dict(os.environ, AGOS_TASK_LEASE_S="10", AGOS_TASK_REAP_S="30", PYTHONPATH=str(ROOT))
    code = ("import logging,sys; logging.basicConfig(level=logging.WARNING, stream=sys.stderr); "
            "import server")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]  # precondition: the server module imports
    out = (p.stdout + p.stderr).upper()
    assert "LEASE" in out and "REAP" in out, "no warning/error about AGOS_TASK_REAP_S >= AGOS_TASK_LEASE_S"
