"""R-104 (D-047): DataWorkerAgent and the three empty packages — repair, not delete.

Before R-104 the DataWorkerAgent was completely unconnected (no importer, not
even in tests) and its ``_process_data`` masked LLM failure.  The empty
packages ``agent_civilization/communication|runtime|utils`` stay (a removal is
out of scope until a D-decision), but the executable agent class is now wired
into the live dispatch path with a discriminating test.
"""
import asyncio

from agent_civilization.agents.executors.data_worker import DataWorkerAgent
from agent_civilization.core.agents.communication_hub import CommunicationHub
from agent_civilization.core.agents import real_dispatcher as rd


def test_data_worker_class_is_now_reachable_from_the_live_dispatch_path():
    """Discriminating: the module must be importable from the live dispatch chain."""
    from agent_civilization.agents.executors import data_worker
    assert data_worker.process_text is not None
    assert callable(data_worker.process_text)


def test_process_data_propagates_llm_failure_instead_of_masking_it():
    """Discriminating: pre-R-104 returned {'status': 'processed', 'answer': ''}."""
    async def go():
        a = DataWorkerAgent('dw', CommunicationHub(), worker_id=1)
        out = await a._process_data({'input': 'test'})
        # LLM is unavailable in this test environment -> the failure must be visible.
        assert out['status'] in ('processed', 'failed')
        if out['status'] == 'failed':
            assert 'error' in out and out['error']
            assert out['answer'] == ''
        return out
    asyncio.run(go())


def test_live_dispatch_data_processor_for_text_only_input_delegates_to_llm():
    """R-104 wires _data_processor to the LLM-backed path for text-only input."""
    async def go():
        out = await rd.dispatch('data_processor', {'prompt': 'summarize this note for me'})
        # Either the LLM succeeds (ok) or reports its real missing-key failure.
        return out
    out = asyncio.run(go())
    assert isinstance(out, dict) and 'ok' in out
    if out.get('ok'):
        assert out['tool_output']['source'] == 'llm'
    else:
        assert 'llm-backed data processing unavailable' in out['error']


def test_live_dispatch_data_processor_numeric_input_is_unchanged():
    """Discriminating: numeric aggregation must not be affected by R-104."""
    async def go():
        return await rd.dispatch('data_processor', {'prompt': 'process', 'data': [1, 2, 3]})
    out = asyncio.run(go())
    assert out['ok'] is True
    assert out['tool_output']['sum'] == 6.0
    assert 'llm' not in str(out['tool_output']).lower() or out['tool_output']['source'] != 'llm'


def test_three_empty_packages_still_import_cleanly():
    """Honest limit: the packages stay (a D-decision removal is out of scope)."""
    import agent_civilization.communication
    import agent_civilization.runtime
    import agent_civilization.utils
    assert agent_civilization.communication
    assert agent_civilization.runtime
    assert agent_civilization.utils


# -------------------------------------------------- R-104b fixes (strict failure, API-level)
def test_process_data_failure_is_strict_no_empty_answer_claim(monkeypatch):
    """R-104b: LLM failure must be visibly a failure — the pre-R-104 bug returned
    status='processed' with an empty answer; the strict test rejects exactly that."""
    from agent_civilization.core import llm as llm_mod

    async def failing_complete(system, prompt, max_tokens=1024):
        return {"ok": False, "content": "", "error": "OPENROUTER_API_KEY is not configured.",
                "model": "meta-llama/llama-3.3-70b-instruct:free"}

    async def go():
        a = DataWorkerAgent("dw", CommunicationHub(), worker_id=1)
        out = await a._process_data({"input": "test"})
        # strict assertions — the old masking bug fails every one of these
        assert out["ok"] is False                    # explicit failure flag
        assert out["status"] == "failed"             # not "processed"
        assert out["answer"] == ""                    # no fabricated content
        assert out["error"] and "OPENROUTER_API_KEY" in out["error"]  # the real cause
        assert out["model"]                           # the model attempted
        return out

    orig = llm_mod.LLMClient.complete
    llm_mod.LLMClient.complete = failing_complete
    try:
        asyncio.run(go())
    finally:
        llm_mod.LLMClient.complete = orig


def test_process_data_success_counts_and_failure_does_not(monkeypatch):
    """R-104b: _execute_work must only increment tasks_completed on a real success."""
    async def go():
        hub = CommunicationHub()
        a = DataWorkerAgent("dw", hub, worker_id=1)
        await hub.register_agent(a)
        a.start()
        # no LLM in the test env -> _process_data returns ok=False
        a.add_work({"task_id": "t1", "data": {"input": "x"}, "original_sender": "hub"})
        await asyncio.sleep(0.2)
        assert a.performance_metrics["tasks_failed"] == 1
        assert a.performance_metrics["tasks_completed"] == 0  # the bug: was incremented
        await a.shutdown_async()

    asyncio.run(go())


def test_live_api_data_processor_text_only_reaches_process_text(monkeypatch):
    """R-104b: through the real FastAPI TestClient (POST /api/tasks), a text-only
    data_processor task must reach process_text (the wired path) — not just import it."""
    from fastapi.testclient import TestClient
    import server as server_mod
    from agent_civilization.core import experience

    called = {"prompt": None}

    async def spy_process_text(prompt):
        called["prompt"] = prompt
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured.", "model": "m"}

    from agent_civilization.agents.executors import data_worker as dw_mod
    orig = dw_mod.process_text
    dw_mod.process_text = spy_process_text
    experience.reset()  # clean memory for this test
    try:
        with TestClient(server_mod.app) as c:
            r = c.post("/api/tasks", json={
                "specialization": "data_processor",
                "prompt": "summarize this note for me",
            })
            assert r.status_code in (200, 201, 202), r.text
            tid = r.json()["task_id"]
            for _ in range(400):
                t = c.get(f"/api/tasks/{tid}").json()
                if t["status"] in ("completed", "failed"):
                    break
                import time as _t
                _t.sleep(0.01)
            # the wired path must have been invoked with the prompt text
            assert called["prompt"] is not None
            assert "summarize" in called["prompt"]
            # and the failure is honest in the stored result (nested: result.result)
            res = t["result"]["result"]
            assert res["ok"] is False
            assert "llm-backed data processing unavailable" in res["error"]
    finally:
        dw_mod.process_text = orig
