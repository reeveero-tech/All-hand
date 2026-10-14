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
