"""R-160 (G-159-1): no raw exception text in logs or in the public /health body.

R-159 closed raw-exception logging in the eight telemetry components and listed
the remaining sites as G-159-1 (specialist templates, BaseAgent, the
communication hub, agent_factory, data_worker, redis_queue, server). The R-160
review also found: the Redis URL (may carry a password) logged in full on every
``/health`` (RedisQueue.__init__), and four ``str(e)`` in the health checks'
*response* messages (public route).

Each test raises an exception whose text is a synthetic secret and asserts the
secret appears in no log record and no response, while the exception TYPE (the
diagnosis) is still there. Evidence class: unit / local (mocks and in-process
TestClient) — not a live integration.
"""
import asyncio
import importlib
import json
import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scan_raw_exception_logs as scanner  # noqa: E402

SECRET = "SYNTHETIC-SECRET-r160-7f3a9c"


class Boom(RuntimeError):
    pass


def _no_secret(caplog, *extra):
    text = "\n".join(r.getMessage() for r in caplog.records)
    text += "\n" + "\n".join(str(r.args) for r in caplog.records)
    for x in extra:
        text += "\n" + str(x)
    assert SECRET not in text
    return text


# --------------------------------------------------------------------- static

def test_scanner_finds_no_unreviewed_raw_exception_log_in_runtime_code():
    found, unreviewed, stale = scanner.scan()
    assert unreviewed == [], unreviewed
    assert stale == [], stale          # every reviewed entry still exists and still matches


def test_scanner_detects_every_raw_form(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(
        "import logging\nlogger = logging.getLogger()\n"
        "try:\n    pass\nexcept Exception as e:\n"
        "    logger.error(f'x {e}')\n"            # f-string
        "    logger.error('x %s', e)\n"            # %-arg
        "    logger.warning('x ' + str(e))\n"      # str()
        "    logger.info('x', exc_info=True)\n"    # traceback
        "    logger.exception('x')\n"              # traceback
        "    print(repr(e))\n"                     # print
        "    logger.error('ok %s', type(e).__name__)\n"   # safe
        "    logger.error('ok %s', e.__class__.__name__)\n",  # safe
        encoding="utf-8")
    hits = scanner.scan_file(src, "m.py")
    assert [h[1] for h in hits] == [6, 7, 8, 9, 10, 11]


def test_scanner_detects_the_pre_fix_form_of_a_real_site(tmp_path):
    """The exact pre-R-160 line of the specialist templates is detected (not inert)."""
    tree_line = 'logger.error(f"Agent {self.name} error: {e}")'
    src = "def f(self):\n    try:\n        pass\n    except Exception as e:\n        " + tree_line + "\n"
    p = tmp_path / "probe.py"
    p.write_text("import logging\nlogger = logging.getLogger()\n" + src, encoding="utf-8")
    assert len(scanner.scan_file(p, "probe.py")) == 1


# ------------------------------------------------------------- behavioural

SPECS = ["analyst", "api_integrator", "architect", "builder", "code_generator", "communicator",
         "data_processor", "db_manager", "designer", "educator", "modifier", "monitor",
         "network_client", "researcher", "reviewer", "self_developer", "strategist", "surgeon",
         "test_runner", "validator"]


@pytest.mark.parametrize("spec", SPECS)
def test_specialist_template_logs_type_only(spec, caplog):
    from agent_civilization.core.agents.communication_hub import CommunicationHub
    pkg = importlib.import_module(f"agent_civilization.agents.specialists.{spec}")
    cls = getattr(pkg, pkg.__all__[0])
    agent = cls("probe", CommunicationHub(), agent_id=1)

    async def boom(work):
        raise Boom(SECRET)
    agent._process_task = boom
    caplog.set_level(logging.DEBUG)
    asyncio.run(agent._execute_work({"task_id": "t1"}))
    text = _no_secret(caplog)
    assert "Boom" in text and "probe" in text
    assert agent.stats["errors"] == 1


def _base_agent():
    from agent_civilization.core.agents.base_agent import BaseAgent
    from agent_civilization.core.agents.communication_hub import CommunicationHub

    class A(BaseAgent):
        async def _process_task(self, work):
            raise Boom(SECRET)
    return A("probe-base", CommunicationHub())


def test_base_agent_work_error_logs_type_only(caplog):
    agent = _base_agent()

    async def boom(work):
        raise Boom(SECRET)
    agent.execute = boom
    caplog.set_level(logging.DEBUG)
    asyncio.run(agent._execute_work({"task_id": "t"}))
    assert "Boom" in _no_secret(caplog)


def test_base_agent_loop_error_logs_and_exposes_type_only(caplog):
    agent = _base_agent()

    async def boom(msg):
        raise Boom(SECRET)
    agent._handle_message = boom
    caplog.set_level(logging.DEBUG)

    async def run():
        agent.start()
        agent._inbox.put_nowait(object())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if agent.performance_metrics["errors"]:
                break
        await agent.shutdown_async()
    asyncio.run(run())
    status = agent.get_status()
    assert status["performance"]["errors"] == ["Boom"]      # exposed metrics: type only
    assert "Boom" in _no_secret(caplog, status)


def test_communication_hub_broadcast_error_logs_type_only(caplog):
    from agent_civilization.core.agents.base_agent import Message
    from agent_civilization.core.agents.communication_hub import CommunicationHub

    class Bad:
        def deliver_message(self, m):
            raise Boom(SECRET)
    hub = CommunicationHub()
    hub.agents["bad"] = Bad()
    caplog.set_level(logging.DEBUG)
    asyncio.run(hub._broadcast_internal(Message(sender="s", recipient="broadcast", content={})))
    text = _no_secret(caplog)
    assert "Boom" in text and "bad" in text


def test_agent_factory_learning_and_memory_errors_log_type_only(caplog):
    from agos_kernel.agent_production.agent_factory import AgentConfig, DynamicAgent

    class BadRuntime:
        def record_task(self, **kw):
            raise Boom(SECRET)

        def get_best_approach(self, t):
            raise Boom(SECRET)

    class BadMemory:
        async def recall(self, **kw):
            raise Boom(SECRET)
    a = DynamicAgent(AgentConfig(name="probe-dyn"), learning_runtime=BadRuntime(), memory_system=BadMemory())
    caplog.set_level(logging.DEBUG)
    a._current_task = {"type": "x"}
    a._record_experience({"type": "x"}, {"ok": True}, success=True)
    assert a._select_approach({"type": "x"}) is None
    assert asyncio.run(a._recall_context({"prompt": "p"})) == {}
    text = _no_secret(caplog)
    assert text.count("Boom") >= 3


def test_data_worker_error_logs_type_only(caplog):
    from agent_civilization.agents.executors.data_worker import DataWorkerAgent
    from agent_civilization.core.agents.communication_hub import CommunicationHub
    w = DataWorkerAgent("probe-worker", CommunicationHub())

    async def boom(data):
        raise Boom(SECRET)
    w._process_data = boom
    caplog.set_level(logging.DEBUG)
    asyncio.run(w._execute_work({"task_id": "t", "data": {}}))
    assert "Boom" in _no_secret(caplog)
    assert w.performance_metrics["tasks_failed"] == 1


@pytest.mark.parametrize("raw", [
    "{not json " + SECRET,                                     # decode error
    json.dumps([{"url": "http://x", "api_key": SECRET}]),      # entry refused by the name contract (R-188 review round 3)
    json.dumps({"name": SECRET}),                              # not a list
])
def test_external_agents_env_error_logs_position_only(raw, monkeypatch, caplog):
    from agent_civilization.agents import external_adapter
    monkeypatch.setenv("EXTERNAL_AGENTS", raw)
    caplog.set_level(logging.DEBUG)
    assert external_adapter.load_external_agents_from_env() == []
    text = _no_secret(caplog)
    # R-160: the log never echoes the offending VALUE — only the type and
    # position. R-188 review round 3: an entry with an unacceptable name is
    # now refused PER-ENTRY (skipped by position) instead of failing the
    # whole environment — the redaction contract itself is unchanged.
    assert ("Invalid EXTERNAL_AGENTS JSON" in text
            or "EXTERNAL_AGENTS entry 0 skipped" in text)


class _FakeRedis:
    """Mock Redis client: every call raises an error carrying the secret."""

    def __getattr__(self, name):
        async def call(*a, **k):
            raise Boom(SECRET)
        return call


def test_redis_queue_does_not_log_url_credentials(monkeypatch, caplog):
    from agos_kernel.infrastructure import redis_queue
    caplog.set_level(logging.DEBUG)
    redis_queue.RedisQueue(redis_url=f"redis://user:{SECRET}@cache.internal:6380/0")
    text = _no_secret(caplog)
    assert "redis://cache.internal:6380" in text


def test_redis_queue_target_helper():
    from agos_kernel.infrastructure.redis_queue import _safe_redis_target
    assert _safe_redis_target(f"rediss://:{SECRET}@h:1/2?x={SECRET}") == "rediss://h:1"
    assert _safe_redis_target("") == "?://?"
    assert SECRET not in _safe_redis_target(f"redis://[{SECRET}")


def test_redis_queue_errors_log_and_return_type_only(caplog):
    from agos_kernel.infrastructure import redis_queue
    q = redis_queue.RedisQueue(redis_url="redis://localhost:6379")
    q._use_fallback = False
    q._redis = _FakeRedis()
    caplog.set_level(logging.DEBUG)

    async def run():
        await q.enqueue("spec", "prompt", task_id="t-1")   # hexists + hset fail -> memory
        q._fallback_tasks.clear()
        assert await q.dequeue(timeout=0) is None
        await q.complete("t-x", {})
        assert await q.fail("t-x", "e") is False
        assert await q.get_task("t-x") is None
        assert await q.get_queue_size() == 0
        return await q.get_stats()
    stats = asyncio.run(run())
    assert stats == {"error": "Boom"}
    text = _no_secret(caplog, stats)
    assert text.count("Boom") >= 7


def test_health_check_wrapper_message_is_type_only():
    from agos_kernel.infrastructure.health import HealthCheck

    async def boom():
        raise Boom(SECRET)
    res = asyncio.run(HealthCheck("probe", boom).run())
    assert res.message == "Error: Boom" and res.details == {"error_type": "Boom"}
    assert SECRET not in json.dumps(res.to_dict())


@pytest.mark.parametrize("check,target", [
    ("check_redis", "agos_kernel.infrastructure.redis_queue.get_redis_queue"),
    ("check_storage", "agent_civilization.storage.storage_stats"),
    ("check_civilization", "agent_civilization.agents.real.create_civilization"),
])
def test_health_component_checks_message_is_type_only(check, target, monkeypatch):
    from agos_kernel.infrastructure import health
    mod, attr = target.rsplit(".", 1)

    def boom(*a, **k):
        raise Boom(SECRET)
    monkeypatch.setattr(importlib.import_module(mod), attr, boom)
    # the standalone path (no provider registered by server.py): importing the
    # server in an earlier test registers one, so pin it off for this test
    monkeypatch.setattr(health, "_civilization_provider", None)
    out = asyncio.run(getattr(health, check)())
    assert SECRET not in json.dumps(out)
    assert "Boom" in out["message"]


def test_public_health_route_body_has_no_exception_text(monkeypatch):
    """In-process TestClient on the real app: a failing component check (here
    get_redis_queue raising) reaches /health without its text."""
    from fastapi.testclient import TestClient

    import server
    from agos_kernel.infrastructure import redis_queue

    def boom(*a, **k):
        raise Boom(SECRET)
    monkeypatch.setattr(redis_queue, "get_redis_queue", boom)
    with TestClient(server.app, raise_server_exceptions=False) as c:
        r = c.get("/health")
    assert SECRET not in r.text
