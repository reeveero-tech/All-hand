"""R-161f: the llm probe of scripts/r161_live_integration.py must not impose
its own tiny completion budget.

Live runs 39102435807 (model openrouter/free) and 39102564675 (model
liquid/lfm-2.5-2.6b:free) answered the llm probe with an EMPTY completion,
while the external probe — same host, same router model, no max_tokens —
PASSED in 39102435807. The llm probe sent max_tokens=16; hybrid reasoning
models spend such a budget on reasoning and return ``content: null``. The
probe now calls ``LLMClient.complete`` with the client's own default budget,
i.e. exactly the production call shape.

Evidence class of THESE tests: mock (aiohttp fake on 127.0.0.1).
"""
import inspect
import json

from aiohttp import web

from tests.test_r161_live_integration import R, SECRET, _r, _serve

from agent_civilization.core.llm import LLMClient

DEFAULT_BUDGET = inspect.signature(LLMClient.complete).parameters["max_tokens"].default


class HybridReasoningCompletion:
    """Like a hybrid reasoning model behind OpenRouter: with a small
    max_tokens the whole budget goes to reasoning and content is null."""

    def __init__(self, threshold=64):
        self.threshold, self.budgets = threshold, []

    async def handle(self, request):
        body = json.loads(await request.read())
        mt = body.get("max_tokens")
        self.budgets.append(mt)
        if mt is not None and mt < self.threshold:
            return web.json_response({"choices": [{"message": {"content": None,
                                                               "reasoning": "The user wants..."}}]})
        return web.json_response({"choices": [{"message": {"content": "PONG"}}], "model": "fake-hybrid"})


async def _run_llm(fake):
    runner, base = await _serve(fake.handle, "/v1/chat/completions")
    try:
        env = {"R161_LLM_URL": base + "/v1/chat/completions", "R161_LLM_API_KEY": SECRET,
               "R161_LLM_MODEL": "fake/hybrid:free", "R161_TIMEOUT_S": "5"}
        return await R.run(["llm"], env, simulated=True)
    finally:
        await runner.cleanup()


async def test_hybrid_reasoning_model_passes_with_the_client_budget():
    fake = HybridReasoningCompletion()
    rep = await _run_llm(fake)
    r = _r(rep, "llm")
    assert r["outcome"] == "PASS" and r["connected"] is True, r
    assert fake.budgets and all(b == DEFAULT_BUDGET for b in fake.budgets), fake.budgets


async def test_probe_budget_is_the_production_default():
    """The probe sends the LLMClient default (the production call shape),
    not a probe-specific cap."""
    fake = HybridReasoningCompletion(threshold=0)
    await _run_llm(fake)
    assert fake.budgets == [DEFAULT_BUDGET], fake.budgets
    assert DEFAULT_BUDGET >= 256


async def test_a_genuinely_empty_completion_still_fails():
    """No weakening: a provider that answers null content at any budget is FAIL."""
    fake = HybridReasoningCompletion(threshold=10**9)
    rep = await _run_llm(fake)
    r = _r(rep, "llm")
    assert r["outcome"] == "FAIL" and "empty completion" in r["reason"], r
    assert r["connected"] is True
    assert SECRET not in json.dumps(rep)
