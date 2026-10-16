"""R-125 (D-053) — self_developer runs a real self-development cycle.

Before: it returned a static count of its handlers ("20 specializations backed
by real handlers") whatever happened. Now it probes its own handlers, re-runs
remembered failures through the real router + pipeline, retires the failure
memories of goals that now succeed, and attributes the ones that still fail.
"""
import asyncio

import pytest

from agent_civilization.core import experience
from agent_civilization.core.agents import real_dispatcher as rd


def run(coro):
    return asyncio.run(coro)


async def _remember_failure(spec, goal, error):
    async def call():
        return {"ok": False, "error": error}
    await experience.run_with_experience(spec, f"{spec}_t", {"prompt": goal, "goal": goal}, call)


@pytest.fixture(autouse=True)
def fresh_memory(monkeypatch, tenant_context):
    # SEC-02: the cycle runs as one tenant; it only retests that tenant's failures
    monkeypatch.setenv("AGOS_MEMORY", "1")
    monkeypatch.delenv("AGOS_MEMORY_FILE", raising=False)
    experience.reset()
    yield
    experience.reset()


def test_probes_check_the_answers_not_only_ok():
    r = run(rd.dispatch("self_developer", {"prompt": "self improve"}))
    assert r["ok"] is True
    assert r["answer"].startswith(f"self-development cycle: probes {len(rd._SELF_PROBES)}/{len(rd._SELF_PROBES)} healthy")
    assert "no remembered failures to retest" in r["answer"]
    assert set(r["tool_output"]["probes"]) == set(rd._SELF_PROBES)


def test_a_broken_handler_is_reported_and_the_cycle_fails(monkeypatch):
    async def broken(work):
        return {"ok": True, "answer": "n=3 mean=999"}  # wrong answer, ok=True
    monkeypatch.setitem(rd._HANDLERS, "analyst", broken)
    r = run(rd.dispatch("self_developer", {"prompt": "self improve"}))
    assert r["ok"] is False
    assert "failing: analyst" in r["answer"]
    assert r["tool_output"]["probes"]["analyst"]["ok"] is False


def test_a_fixed_failure_is_retested_and_retired():
    goal = "sum these numbers: [1, 2, 3, 4, 5]"  # works now (offline)
    run(_remember_failure("analyst", goal, "simulated earlier failure"))
    before = [e for e in experience.memory()._memories.values() if e.metadata.get("goal") == goal and not e.metadata["ok"]]
    assert len(before) == 1
    r = run(rd.dispatch("self_developer", {"prompt": "self improve"}))
    assert "retested 1 remembered failure(s): 1 fixed and retired (1 memories), 0 still failing" in r["answer"], r["answer"]
    after = [e for e in experience.memory()._memories.values() if e.metadata.get("goal") == goal and not e.metadata.get("ok")]
    assert after == []  # the change: the stale failure is gone
    assert r["tool_output"]["retests"][0]["fixed"] is True


def test_a_failure_that_persists_is_attributed_and_kept(monkeypatch):
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "0")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    goal = "draft a message to the team about the outage"  # needs an LLM -> still fails
    run(_remember_failure("communicator", goal, "earlier failure"))
    r = run(rd.dispatch("self_developer", {"prompt": "self improve"}))
    item = r["tool_output"]["retests"][0]
    assert item["fixed"] is False and item["specialization"] == "communicator"
    assert item["cause"] == "external" and "LLM" in item["reason"]
    assert "0 fixed and retired" in r["answer"] and "1 still failing" in r["answer"]
    kept = [e for e in experience.memory()._memories.values() if e.metadata.get("goal") == goal and not e.metadata["ok"]]
    assert len(kept) >= 1  # not retired


def test_retest_limit_and_no_self_recursion(monkeypatch):
    monkeypatch.setenv("AGOS_SELF_RETEST_MAX", "1")
    run(_remember_failure("analyst", "sum these numbers: [1, 2, 3, 4, 5]", "x"))
    run(_remember_failure("analyst", "self improve: inspect your own specializations and evolve", "x"))
    r = run(rd.dispatch("self_developer", {"prompt": "self improve"}))
    assert len(r["tool_output"]["retests"]) <= 1
    assert all("self improve" not in t["goal"] for t in r["tool_output"]["retests"])


def test_experience_stores_the_goal():
    run(_remember_failure("analyst", "compute the average of [1, 2]", "x"))
    e = next(iter(experience.memory()._memories.values()))
    assert e.metadata["goal"] == "compute the average of [1, 2]"
