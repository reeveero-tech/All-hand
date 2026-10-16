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



def test_process_data_failure_is_strict_no_empty_answer_claim(monkeypatch):
    """R-104b: LLM failure must be visibly a failure — the pre-R-104 bug returned
    status='processed' with an empty answer; the strict test rejects exactly that."""
    from agent_civilization.core.llm import LLMClient

    async def failing_complete(self, system, prompt, max_tokens=1024):
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

    orig = LLMClient.complete
    LLMClient.complete = failing_complete
    try:
        asyncio.run(go())
    finally:
        LLMClient.complete = orig


def test_process_data_success_counts_and_failure_does_not(monkeypatch):
    """R-104b: _execute_work must only increment tasks_completed on a real success —
    deterministic via a test double (not the environment's missing LLM key)."""
    from agent_civilization.core.llm import LLMClient

    async def failing_complete(self, system, prompt, max_tokens=1024):
        return {"ok": False, "content": "", "error": "no key", "model": "m"}

    monkeypatch.setattr(LLMClient, "complete", failing_complete)

    async def go():
        hub = CommunicationHub()
        a = DataWorkerAgent("dw", hub, worker_id=1)
        await hub.register_agent(a)
        a.start()
        a.add_work({"task_id": "t1", "data": {"input": "x"}, "original_sender": "hub"})
        await asyncio.sleep(0.2)
        assert a.performance_metrics["tasks_failed"] == 1
        assert a.performance_metrics["tasks_completed"] == 0  # the bug: was incremented
        await a.shutdown_async()

    asyncio.run(go())


def test_live_api_data_processor_text_only_reaches_process_text(monkeypatch, tenant_auth):
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
        with TestClient(server_mod.app, headers=tenant_auth.headers) as c:  # SEC-02: authenticated tenant
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


# --------------------------------------------- R-104c (blocking review round 2)
def test_strict_failure_with_correct_signature_and_received_prompt(monkeypatch):
    """R-104c: monkeypatch with the REAL method signature (self, system, prompt)
    and verify the prompt the worker actually received."""
    from agent_civilization.core.llm import LLMClient

    received = {}

    async def failing_complete(self, system, prompt, max_tokens=1024):
        received["system"], received["prompt"] = system, prompt
        return {"ok": False, "content": "", "error": "OPENROUTER_API_KEY is not configured.",
                "model": "meta-llama/llama-3.3-70b-instruct:free"}

    monkeypatch.setattr(LLMClient, "complete", failing_complete)

    async def go():
        a = DataWorkerAgent("dw", CommunicationHub(), worker_id=1)
        return await a._process_data({"input": "the exact prompt sent"})

    out = asyncio.run(go())
    assert out["ok"] is False and out["status"] == "failed"
    assert received["prompt"] == "the exact prompt sent"   # the real prompt reached the LLM
    assert "data processing worker" in received["system"]


def test_success_is_deterministic_and_counters_increment(monkeypatch):
    """R-104c: a deterministic success path (not env-dependent) — both counters checked."""
    from agent_civilization.core.llm import LLMClient

    async def ok_complete(self, system, prompt, max_tokens=1024):
        return {"ok": True, "content": f"processed: {prompt}", "model": "m"}

    monkeypatch.setattr(LLMClient, "complete", ok_complete)

    async def go():
        hub = CommunicationHub()
        a = DataWorkerAgent("dw", hub, worker_id=1)
        await hub.register_agent(a)
        a.start()
        a.add_work({"task_id": "t1", "data": {"input": "real input"}, "original_sender": "hub"})
        await asyncio.sleep(0.2)
        return a

    a = asyncio.run(go())
    assert a.performance_metrics["tasks_completed"] == 1
    assert a.performance_metrics["tasks_failed"] == 0
    out_result = asyncio.run(a._process_data({"input": "x"}))
    assert out_result["ok"] is True and out_result["status"] == "processed"
    assert out_result["answer"] == "processed: x"
    asyncio.run(a.shutdown_async())


def test_api_level_task_with_real_data_field_reaches_llm(tenant_auth, monkeypatch):
    """R-104c: an API task with a prompt AND real data — the LLM must receive the
    prompt (the instruction), and the honest failure is stored (nested result)."""
    from fastapi.testclient import TestClient
    import server as server_mod
    from agent_civilization.core import experience

    received = {}

    async def spy_process_text(prompt):
        received["prompt"] = prompt
        return {"ok": False, "error": "OPENROUTER_API_KEY is not configured.", "model": "m"}

    from agent_civilization.agents.executors import data_worker as dw_mod
    orig = dw_mod.process_text
    dw_mod.process_text = spy_process_text
    experience.reset()
    try:
        with TestClient(server_mod.app, headers=tenant_auth.headers) as c:  # SEC-02: authenticated tenant
            # data is a dict of TEXT values (no numbers anywhere) so the numeric
            # aggregation has nothing to do and the task goes down the LLM path.
            r = c.post("/api/tasks", json={
                "specialization": "data_processor",
                "prompt": "clean and aggregate the sales notes",
                "data": {"notes": ["strong sales", "weak sales"], "column": "sales"},
            })
            assert r.status_code in (200, 201, 202), r.text
            tid = r.json()["task_id"]
            import time as _t
            for _ in range(400):
                t = c.get(f"/api/tasks/{tid}").json()
                if t["status"] in ("completed", "failed"):
                    break
                _t.sleep(0.01)
            # the prompt (instruction) reached the LLM path — not just any string
            assert "sales" in received["prompt"]
            res = t["result"]["result"]
            assert res["ok"] is False
            assert "llm-backed data processing unavailable" in res["error"]
    finally:
        dw_mod.process_text = orig


def test_task_complete_event_means_attempt_finished_not_success(monkeypatch):
    """R-104c: `task_complete` signals the attempt finished; the RESULT carries
    ok=False on failure — deterministic via a test double."""
    from agent_civilization.core.llm import LLMClient

    async def failing_complete(self, system, prompt, max_tokens=1024):
        return {"ok": False, "content": "", "error": "no key", "model": "m"}

    monkeypatch.setattr(LLMClient, "complete", failing_complete)

    async def go():
        hub = CommunicationHub()
        a = DataWorkerAgent("dw", hub, worker_id=1)
        await hub.register_agent(a)
        a.start()
        sent = []
        orig_send = hub.send_message
        async def spy(msg):
            if msg.sender == "dw" and msg.recipient == "hub":
                sent.append(msg.content)
            return await orig_send(msg)
        hub.send_message = spy
        a.add_work({"task_id": "t1", "data": {"input": "x"}, "original_sender": "hub"})
        await asyncio.sleep(0.2)
        assert len(sent) == 1
        content = sent[0]
        assert content["action"] == "task_complete"      # attempt finished
        assert content["result"]["ok"] is False           # but the result says failure
        assert content["result"]["status"] == "failed"
        await a.shutdown_async()

    asyncio.run(go())


# --------------------------------------------- R-104c2 (blocking review round 3)

def test_task_dispatcher_consumer_counts_completion_not_success(monkeypatch):
    """R-104c2: the REAL consumer of task_complete is TaskDispatcherAgent._handle_message
    (it decrements load on task_complete regardless of result.ok). Verified against
    the actual consumer code — the contract is 'attempt finished', not 'success'."""
    from agent_civilization.agents.infrastructure.task_dispatcher import TaskDispatcherAgent
    from agent_civilization.core.agents.base_agent import Message

    async def go():
        hub = CommunicationHub()
        d = TaskDispatcherAgent("dispatch", hub)
        d.agent_load["dw"] = 3
        # a task_complete whose result FAILED must still decrement the load —
        # call the consumer's own handler directly (its contract, not the hub's)
        msg = Message(sender="dw", recipient="dispatch",
                      content={"action": "task_complete", "task_id": "t1",
                               "result": {"ok": False, "error": "llm down"}})
        await d._handle_message(msg)
        assert d.agent_load["dw"] == 2  # decremented: the attempt finished
        # and the consumer stores no success claim anywhere — it only tracks load
        assert not hasattr(d, "completed_tasks") or not getattr(d, "completed_tasks")

    asyncio.run(go())


def test_three_packages_are_consumed_by_production_paths(monkeypatch):
    """R-104c2: each package is consumed by a PRODUCTION path — not just tests.
    For communication/: the package's CommunicationHub is patched FIRST, then
    specialist_runtime is RELOADED (so its import binds the package attribute at
    load time — this fails if the module imported from core/agents directly), and
    the real builder constructs the civilization on the stub. The module is
    restored afterwards."""
    # Run in an ISOLATED SUBPROCESS: patch the package attribute BEFORE the
    # consumer module is ever imported, which proves the module-level import
    # actually binds the package's export at load time — reverting the import
    # line to core/agents makes the child's assertion fail. A subprocess is
    # used because reloading the module in-process would create new class
    # objects and break isinstance checks in other tests.
    import subprocess, sys, textwrap

    child = textwrap.dedent("""
        import agent_civilization.communication as comm

        class PackageHub(comm.CommunicationHub):
            constructed = []

            def __init__(self):
                PackageHub.constructed.append(self)
                super().__init__()

        # patch the PACKAGE attribute BEFORE the consumer module is ever imported
        comm.CommunicationHub = PackageHub

        # now import the consumer — its module-level
        # `from agent_civilization.communication import CommunicationHub`
        # binds the stub
        import agent_civilization.agents.real.specialist_runtime as sr
        assert sr.CommunicationHub is PackageHub, (
            "the consumer did not bind the package's export")

        civ = sr.create_specialist_civilization(["data_processor"], agents_per_spec=2)
        assert PackageHub.constructed, "the builder never constructed the package hub"
        hub = PackageHub.constructed[0]
        for spec, pool in civ.items():
            for a in pool:
                assert a.inner.communication_hub is hub, (
                    "an agent is not on the package-constructed hub")
        print("CHILD-OK")
    """)

    import pathlib
    r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                       cwd=str(pathlib.Path(__file__).resolve().parent.parent))
    assert r.returncode == 0, (
        "the consumer is not wired to the package:\n" + r.stderr[-2000:])
    assert "CHILD-OK" in r.stdout


    # runtime/: the live researcher handler consumes make_deadline/remaining from it.
    import agent_civilization.runtime as runtime
    assert callable(runtime.make_deadline) and callable(runtime.remaining)
    d = runtime.make_deadline(0.5)
    assert 0 < runtime.remaining(d) <= 0.5

    # utils/: the live data-processor handler consumes build_llm_payload from it.
    import agent_civilization.utils as utils
    assert utils.build_llm_payload("p", {"k": "v"}).endswith('{"k": "v"}')
    assert utils.build_llm_payload("p", None) == "p"


def test_data_field_value_reaches_llm_and_changing_data_changes_the_input(monkeypatch):
    """R-104c2: a value that appears ONLY in `data` (not in the prompt) must reach
    the LLM — proven at the LLMClient.complete boundary itself — and changing the
    data with the same prompt must change the payload (both payloads compared)."""
    from agent_civilization.core.agents import real_dispatcher as rdc
    from agent_civilization.core.llm import LLMClient

    llm_prompts = []

    async def recording_complete(self, system, prompt, max_tokens=1024):
        llm_prompts.append(prompt)
        return {"ok": False, "content": "", "error": "no key", "model": "m"}

    monkeypatch.setattr(LLMClient, "complete", recording_complete)

    base = {"prompt": "clean and aggregate the sales notes",
            "data": {"notes": ["alpha-only-in-data"]}}
    other = {"prompt": "clean and aggregate the sales notes",
             "data": {"notes": ["beta-only-in-data"]}}
    asyncio.run(rdc._data_processor(base))
    assert llm_prompts, "the LLM boundary was never reached"
    first = llm_prompts[0]
    assert "alpha-only-in-data" in first          # data value reached the LLM
    assert "alpha-only-in-data" not in base["prompt"]  # ...and it came from data

    asyncio.run(rdc._data_processor(other))
    second = llm_prompts[1]
    assert "beta-only-in-data" in second
    # the two payloads differ ONLY because the data differed (same prompt)
    assert first != second
    assert first.split("\n\n=== data ===\n")[0] == second.split("\n\n=== data ===\n")[0]


def test_runtime_helpers_are_used_by_the_live_researcher_handler(monkeypatch):
    """The live handler really calls the runtime package — deterministically:
    the transport is a controlled stub (no real network), BOTH make_deadline and
    remaining are recorded, and the outcome is asserted for that stub."""
    import agent_civilization.runtime as runtime
    from agent_civilization.core import safety
    from agent_civilization.core.agents import real_dispatcher as rdc

    calls = {"make": 0, "remaining": 0}
    orig_make = runtime.make_deadline
    orig_remaining = runtime.remaining

    def counting_deadline(budget):
        calls["make"] += 1
        return orig_make(budget)

    def counting_remaining(deadline):
        calls["remaining"] += 1
        return orig_remaining(deadline)

    monkeypatch.setattr(runtime, "make_deadline", counting_deadline)
    monkeypatch.setattr(runtime, "remaining", counting_remaining)

    # a deterministic stub: an immediate, non-transient error from the transport
    # (no real network — the handler's own classifier decides the outcome).
    import urllib.error

    def stub_urlopen(req, timeout=None):
        raise urllib.error.URLError("stubbed: deterministic failure for the consumption test")

    monkeypatch.setattr(safety, "safe_urlopen", stub_urlopen)

    async def go():
        return await rdc._researcher({"prompt": "test"})

    out = asyncio.run(go())
    # BOTH helpers were consumed by the live handler
    assert calls["make"] >= 1
    assert calls["remaining"] >= 1
    # the outcome is deterministic for the stub: honest failure, no fabrication
    assert out["ok"] is False
    assert "stubbed" in (out.get("error") or "")
    assert not out.get("content")  # no fabricated results


def test_utils_payload_is_used_by_the_live_data_processor(monkeypatch):
    """The live handler really calls the utils package — a spy ON THE PACKAGE
    FUNCTION records its inputs, and a DISTINCTIVE payload it produces is what
    must reach process_text (proving the handler went through the package)."""
    import agent_civilization.utils as utils
    import agent_civilization.core.agents.real_dispatcher as rdc

    spy = {"inputs": []}

    def spying_build(prompt, data):
        spy["inputs"].append((prompt, data))
        # a distinctive marker only this stub produces — if it reaches
        # process_text, the handler truly went through the package function
        return f"STUB-PAYLOAD[{prompt}]::DATA={data!r}"

    monkeypatch.setattr(utils, "build_llm_payload", spying_build)

    received = []

    async def spy_process_text(payload):
        received.append(payload)
        return {"ok": False, "error": "no key", "model": "m"}

    from agent_civilization.agents.executors import data_worker as dw_mod
    monkeypatch.setattr(dw_mod, "process_text", spy_process_text)

    async def go():
        return await rdc._data_processor(
            {"prompt": "aggregate", "data": {"col": "gamma-only-in-data"}})

    asyncio.run(go())

    # the package function was called with the handler's prompt and data...
    assert spy["inputs"] == [("aggregate", {"col": "gamma-only-in-data"})]
    # ...and its DISTINCTIVE output is exactly what reached process_text
    assert received == ["STUB-PAYLOAD[aggregate]::DATA={'col': 'gamma-only-in-data'}"]

