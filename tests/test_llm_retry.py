"""Rate limits: wait as long as the provider asks (header or 'Retry after Ns' hint), capped."""
import asyncio

import httpx

from app import llm as L


def test_retry_after_parsing():
    mk = lambda h=None, t="": httpx.Response(402, headers=h or {}, text=t)
    assert L.retry_after(mk(t="Free limit reached ... Retry after 31s, or get"), 6) == 32
    assert L.retry_after(mk({"Retry-After": "10"}), 6) == 11
    assert L.retry_after(mk(t="no hint"), 12) == 12
    assert L.retry_after(mk({"Retry-After": "9999"}), 6) == L.RETRY_CAP


def test_chat_waits_for_free_limit_then_succeeds(monkeypatch):
    calls, waits = [], []
    replies = [httpx.Response(402, text='{"error":{"message":"Free limit reached. Retry after 31s"}}'),
               httpx.Response(200, json={"choices": [{"message": {"content": "تم"}}]})]

    def handler(req):
        calls.append(req)
        return replies[len(calls) - 1]

    async def fake_sleep(s):
        waits.append(s)
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)

    async def go():
        c = L.LLM(L.LLMConfig("free", "https://example.invalid", "m", None, concurrency=1))
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await c.chat([{"role": "user", "content": "x"}])
        finally:
            await c.close()
    assert asyncio.run(go()) == "تم"
    assert waits == [32] and len(calls) == 2  # waited what the provider asked, no extra blind backoff


def _client(handler, interval):
    c = L.LLM(L.LLMConfig("free", f"https://pace-{interval}.invalid", "m", None, concurrency=1, min_interval=interval))
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def test_pacer_spaces_parallel_calls():
    """Several agents asking at once must not hit a per-IP limit back to back."""
    times = []

    def handler(req):
        times.append(asyncio.get_event_loop().time())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def go():
        a, b = _client(handler, 0.2), _client(handler, 0.2)  # two runs share one pacer
        try:
            await asyncio.gather(*(c.chat([{"role": "user", "content": "x"}]) for c in (a, b, a)))
        finally:
            await a.close(); await b.close()
    asyncio.run(go())
    gaps = [y - x for x, y in zip(times, times[1:])]
    assert len(times) == 3 and all(g >= 0.18 for g in gaps)


def test_rate_limits_do_not_spend_ordinary_attempts(monkeypatch):
    monkeypatch.setattr(L, "RETRY_CAP", 0.01)
    n = []

    def handler(req):
        n.append(1)
        if len(n) <= 6:  # more rejections than the 5 ordinary attempts
            return httpx.Response(402, text="Free limit reached. Retry after 40s")
        return httpx.Response(200, json={"choices": [{"message": {"content": "وصل"}}]})

    async def go():
        c = _client(handler, 0.001)
        try:
            return await c.chat([{"role": "user", "content": "x"}])
        finally:
            await c.close()
    assert asyncio.run(go()) == "وصل" and len(n) == 7


def test_output_token_limit_retries_with_a_larger_budget(monkeypatch):
    """Production (978e636a8163, t4): the reasoning model spent all 2500 tokens thinking, HTTP 400 twice."""
    import json as _json
    budgets = []

    def handler(req):
        budgets.append(_json.loads(req.content)["max_tokens"])
        if len(budgets) < 3:
            return httpx.Response(400, text='{"error":{"message":"The model exhausted its output token limit","code":"output_token_limit_exceeded"}}')
        return httpx.Response(200, json={"choices": [{"message": {"content": "تم"}}]})

    async def fake_sleep(s):
        pass
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)

    async def go():
        c = L.LLM(L.LLMConfig("free", "https://tokens.invalid", "m", None, concurrency=1))
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await c.chat([{"role": "user", "content": "x"}], max_tokens=2500)
        finally:
            await c.close()
    assert asyncio.run(go()) == "تم"
    assert budgets == [2500, 5000, 10000]
