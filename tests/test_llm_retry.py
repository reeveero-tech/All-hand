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
