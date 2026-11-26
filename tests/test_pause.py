"""A spent model quota pauses the run; it does not fail it, waste calls retrying, or lose finished work."""
import asyncio
import json

import httpx
import pytest

from app import llm as L
from app.orchestrator import Orchestrator, Run
from tests.test_core import FakeLLM, T, plan_of

FREE_LIMIT = ('{"error":{"message":"HTTP 402 FREE_LIMIT: Free limit reached: 100 replies a day per IP. '
              'It resets at 00:00 UTC, in 22 hours"}}')
SPACING = '{"error":{"message":"Free limit reached. Retry after 31s, or get a key"}}'


@pytest.fixture(autouse=True)
def _no_holds():
    L._HOLDS.clear()
    yield
    L._HOLDS.clear()


# ---- error taxonomy -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,text,kind", [
    (402, FREE_LIMIT, "quota_exhausted"),
    (402, SPACING, "rate_limited"),  # the free provider's per-request spacing: wait, do not pause
    (402, "Payment Required", "quota_exhausted"),
    (429, "Too Many Requests", "rate_limited"),
    (429, "You exceeded your current quota", "quota_exhausted"),
    (401, "bad key", "authentication_error"),
    (403, "forbidden", "authentication_error"),
    (408, "", "timeout"),
    (504, "gateway timeout", "timeout"),
    (503, "unavailable", "provider_unavailable"),
    (418, "teapot", "unknown_provider_error"),
])
def test_classify(status, text, kind):
    assert L.classify(status, text) == kind


def test_quota_reset_time():
    assert L.quota_resume_after("It resets at 00:00 UTC, in 22 hours", now=1000.0) == 1000.0 + 22 * 3600
    assert L.quota_resume_after("no hint", now=86400 * 3 + 5) == 86400 * 4  # next 00:00 UTC


def _client(handler, base="https://q.invalid", key=None):
    c = L.LLM(L.LLMConfig("free", base, "m", key, concurrency=1))
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def test_daily_quota_stops_at_once_and_holds_later_calls(monkeypatch):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(402, text=FREE_LIMIT)

    async def no_sleep(s):
        raise AssertionError("a daily quota must not be waited on")
    monkeypatch.setattr(L.asyncio, "sleep", no_sleep)

    async def go():
        c = _client(handler)
        try:
            with pytest.raises(L.ModelPaused) as e1:
                await c.chat([{"role": "user", "content": "x"}])
            with pytest.raises(L.ModelPaused) as e2:  # known spent: refused locally, no request sent
                await c.chat([{"role": "user", "content": "y"}])
            return c.usage, e1.value, e2.value
        finally:
            await c.close()
    usage, e1, e2 = asyncio.run(go())
    assert len(sent) == 1  # old code retried this ~16 minutes (production run 84cca6feac24)
    assert e1.kind == e2.kind == "quota_exhausted" and e1.status == 402 and e1.resume_after
    assert usage.calls == 0 and usage.requests == 1 and usage.failed == 1 and usage.retried == 0 and usage.avoided == 1
    assert L.holds() and L.holds()[0]["kind"] == "quota_exhausted"


def test_quota_of_one_key_does_not_hold_other_keys():
    def handler(req):
        if req.headers.get("authorization") == "Bearer spent":
            return httpx.Response(402, text="insufficient credits")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def go():
        a, b = _client(handler, "https://or.invalid", "spent"), _client(handler, "https://or.invalid", "good")
        try:
            with pytest.raises(L.ModelPaused):
                await a.chat([{"role": "user", "content": "x"}])
            return await b.chat([{"role": "user", "content": "x"}])
        finally:
            await a.close(); await b.close()
    assert asyncio.run(go()) == "ok"
    assert all("spent" not in json.dumps(h) for h in L.holds())  # no secret in /health


def test_rate_limit_still_waits_and_succeeds(monkeypatch):
    replies = [httpx.Response(429, text="slow down"), httpx.Response(200, json={"choices": [{"message": {"content": "تم"}}]})]
    sent = []

    def handler(req):
        sent.append(req)
        return replies[len(sent) - 1]

    async def fake_sleep(s):
        pass
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)

    async def go():
        c = _client(handler, "https://rl.invalid")
        try:
            return await c.chat([{"role": "user", "content": "x"}]), c.usage
        finally:
            await c.close()
    text, usage = asyncio.run(go())
    assert text == "تم" and usage.calls == 1 and usage.failed == 1 and usage.retried == 1


def test_timeouts_end_as_pause_not_crash(monkeypatch):
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    async def fake_sleep(s):
        pass
    monkeypatch.setattr(L.asyncio, "sleep", fake_sleep)

    async def go():
        c = _client(handler, "https://to.invalid")
        try:
            with pytest.raises(L.ModelPaused) as e:
                await c.chat([{"role": "user", "content": "x"}])
            return e.value, c.usage
        finally:
            await c.close()
    e, usage = asyncio.run(go())
    assert e.kind == "timeout" and usage.requests == 5 and usage.retried == 4 and usage.calls == 0


def test_bad_request_is_not_resent(monkeypatch):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(400, text="invalid model")

    async def go():
        c = _client(handler, "https://br.invalid")
        try:
            with pytest.raises(L.LLMError) as e:
                await c.chat([{"role": "user", "content": "x"}])
            return e.value
        finally:
            await c.close()
    e = asyncio.run(go())
    assert e.kind == "invalid_response" and not isinstance(e, L.ModelPaused) and len(sent) == 1


def test_usage_by_phase():
    u = L.Usage()
    with L.phase("analysis"):
        u.count("calls")
        with L.phase("review"):
            u.count("calls"); u.count("failed")
        u.count("calls")
    u.avoid(3, "research")
    assert u.by_phase["analysis"]["calls"] == 2 and u.by_phase["review"] == {"calls": 1, "failed": 1, "retried": 0, "avoided": 0}
    assert u.avoided == 3 and u.by_phase["research"]["avoided"] == 3
    v = L.Usage(); v.load(u.as_dict())
    assert v.calls == 3 and v.carried == 3 and v.by_phase["analysis"]["calls"] == 2


# ---- the run pauses, keeps its work -------------------------------------------------------------------------

class QuotaAt(FakeLLM):
    """Answers normally until `after` replies, then the quota is spent."""

    def __init__(self, plan, after, **kw):
        super().__init__(plan, **kw)
        self.after = after

    async def chat(self, messages, temperature=0.3, max_tokens=2000):
        if self.usage.calls >= self.after:
            raise L.ModelPaused("HTTP 402 FREE_LIMIT", kind="quota_exhausted", status=402, resume_after=123.0)
        return await super().chat(messages, temperature, max_tokens)


async def _run(fake, goal="هدف"):
    run = Run(goal=goal, llm_public={"provider": "fake"}, max_agents=20)
    saved = []

    async def persist(final=False):
        saved.append(json.loads(json.dumps(run.full(), default=str)))
        return True
    await Orchestrator(run, fake, persist=persist).execute()
    return run, saved


def _types(run):
    return [e["type"] for e in run.events]


def test_quota_during_review_pauses_and_keeps_agent_output():
    # planner(1) + agent a(1) + review a(1) + agent b(1) -> review b refused
    fake = QuotaAt(plan_of(T("a"), T("b", ["a"])), after=4)
    run, saved = asyncio.run(_run(fake))
    assert run.status == "paused_quota"  # not failed, not done
    assert run.tasks["a"]["status"] == "done" and run.tasks["a"]["output"] == "ناتج الوكيل"
    b = run.tasks["b"]
    assert b["status"] == "pending" and b["unreviewed"]["output"] == "ناتج الوكيل"  # kept for review on resume
    assert run.pause["kind"] == "quota_exhausted" and run.pause["step"] == "review:b:1" and run.pause["resume_after"] == 123.0
    assert "محفوظ" in run.pause["message"] and run.pause["kept"]["tasks_done"] == ["a"]
    assert "run_paused" in _types(run) and "run_error" not in _types(run)
    assert not any(e["type"] == "verified" and e.get("task") == "b" for e in run.events)  # not counted as a rejection
    assert saved and saved[-1]["status"] == "paused_quota" and saved[-1]["tasks"]["a"]["output"]


def test_quota_during_agent_pauses_without_spending_attempts():
    fake = QuotaAt(plan_of(T("a"), T("b", ["a"])), after=3)  # agent b refused
    run, _ = asyncio.run(_run(fake))
    assert run.status == "paused_quota" and run.tasks["a"]["status"] == "done"
    assert run.tasks["b"]["status"] == "pending" and not run.tasks["b"]["attempts"]
    assert run.pause["step"].startswith("agent:b:")


def test_quota_during_final_report_pauses_and_keeps_all_tasks():
    plan = plan_of(T("a"), T("b"))
    fake = QuotaAt(plan, after=5)  # planner + 2x(agent+review) = 5 -> synthesis refused
    run, _ = asyncio.run(_run(fake))
    assert run.status == "paused_quota"
    assert all(t["status"] == "done" and t["output"] for t in run.tasks.values())
    assert run.pause["step"].startswith(("finalization", "final_review"))
    assert {x["id"] for x in run.report["sections"]} >= {"task:a", "task:b"}  # sections saved before the stop


def test_quota_during_planning_pauses():
    run, _ = asyncio.run(_run(QuotaAt(plan_of(T("a")), after=0)))
    assert run.status == "paused_quota" and run.pause["step"] == "plan:root"


def test_invalid_review_reply_is_not_a_pause():
    class BadReview(FakeLLM):
        async def chat(self, messages, temperature=0.3, max_tokens=2000):
            if messages[0]["content"].startswith("أنت مدقق"):
                self.usage.calls += 1
                raise L.LLMError("bad response", kind="invalid_response")
            return await super().chat(messages, temperature, max_tokens)
    run, _ = asyncio.run(_run(BadReview(plan_of(T("a")))))
    assert run.status != "paused_quota" and run.status != "paused_provider"


def test_paused_runs_are_resumable_and_labelled():
    from app import main
    assert {"paused_quota", "paused_provider"} <= main.RESUMABLE
    js = open("web/app.js", encoding="utf-8").read()
    assert "paused_quota" in js and "run_paused" in js
