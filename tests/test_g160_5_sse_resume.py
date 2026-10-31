"""G-160-5 (D-109): SSE resume with ``Last-Event-ID`` — no loss and no wrong
duplication across a reconnect, honest signals when resume is impossible.

Before: frames had no ``id:`` and a reconnect was a NEW subscription — every
event published while the client was away (or still queued in the closed
connection) was silently gone; only a REST re-read could recover state.

Contract pinned here (bus level, then the real route):
* each bus event has an id ``<epoch>-<generation>-<seq>``, seq contiguous per
  tenant log; synthetic frames (heartbeat/stream_gap/stream_reset) have none;
* reconnect with the last seen id -> exactly the later events, in order, then
  live events — nothing missing, nothing twice;
* events evicted from the bounded ring -> ONE ``stream_gap`` with the EXACT
  count, then what is still held;
* an id this process cannot place (other process/restart, malformed, too
  long, future, another tenant's) -> ONE ``stream_reset``; never another
  tenant's events.

Honest limits: per-process memory only (a restart or another worker =
stream_reset); ring bounded (``AGOS_SSE_REPLAY_SIZE`` x MAX_REPLAY_TENANTS);
in-stream drops of a slow listener keep their stream_gap semantics.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

import server
from agent_civilization.core import sse
from agent_civilization.core.sse import EventBus, SSELimits

ID_RE = re.compile(r"^[0-9a-f]{8}-\d+-\d+$")


def _bus(**limits):
    return EventBus(SSELimits(**limits), clock=lambda: 1000.0)


def _drain(sub):
    async def go():
        out = []
        while (ev := await sub.next(timeout=0.05)) is not None:
            out.append(ev)
        return out
    return asyncio.run(go())


def _ev(i):
    return {"type": "task_created", "task_id": f"t{i}", "specialization": "analyst"}


def test_ids_are_unique_contiguous_and_outside_the_payload():
    b = _bus()
    sub = b.open("A", "c")
    for i in range(1, 6):
        b.publish("A", _ev(i))
    got = _drain(sub)
    assert [dict(e) for e in got] == [_ev(i) for i in range(1, 6)]   # payload unchanged
    ids = [e.sse_id for e in got]
    assert all(ID_RE.match(i) for i in ids)
    assert [int(i.rsplit("-", 1)[1]) for i in ids] == [1, 2, 3, 4, 5]
    assert len({i.rsplit("-", 1)[0] for i in ids}) == 1


def test_reconnect_replays_exactly_the_missed_events_then_live():
    b = _bus()
    s1 = b.open("A", "c")
    for i in range(1, 6):
        b.publish("A", _ev(i))
    first = asyncio.run(s1.next(0.05)), asyncio.run(s1.next(0.05)), asyncio.run(s1.next(0.05))
    last_id = first[-1].sse_id                       # client saw t1..t3; t4,t5 still queued
    b.close(s1)                                      # connection drops: t4,t5 lost before G-160-5
    for i in range(6, 9):
        b.publish("A", _ev(i))                       # nobody listening
    s2 = b.open("A", "c", last_event_id=last_id)
    b.publish("A", _ev(9))                           # live, after the reconnect
    got = _drain(s2)
    assert [e["task_id"] for e in got] == ["t4", "t5", "t6", "t7", "t8", "t9"]
    assert len({e.sse_id for e in got}) == 6
    assert b.stats["replayed"] == 5 and b.stats["resume_gap"] == 0


def test_reconnect_with_the_latest_id_replays_nothing():
    b = _bus()
    s1 = b.open("A", "c")
    b.publish("A", _ev(1))
    last = asyncio.run(s1.next(0.05)).sse_id
    b.close(s1)
    s2 = b.open("A", "c", last_event_id=last)
    assert _drain(s2) == []


def test_no_last_event_id_keeps_the_old_live_only_behavior():
    b = _bus()
    b.publish("A", _ev(1))
    s = b.open("A", "c")
    b.publish("A", _ev(2))
    assert [e["task_id"] for e in _drain(s)] == ["t2"]


def test_evicted_events_give_an_exact_gap_then_what_is_held():
    b = _bus(replay_size=3)
    s1 = b.open("A", "c")
    b.publish("A", _ev(1))
    b.publish("A", _ev(2))
    asyncio.run(s1.next(0.05))
    last = asyncio.run(s1.next(0.05)).sse_id        # saw t1,t2
    b.close(s1)
    for i in range(3, 11):
        b.publish("A", _ev(i))                       # ring keeps t8,t9,t10
    got = _drain(b.open("A", "c", last_event_id=last))
    assert dict(got[0]) == {"type": "stream_gap", "missed": 5, "recover": sse.GAP_RECOVER}
    assert getattr(got[0], "sse_id", None) is None
    assert [e["task_id"] for e in got[1:]] == ["t8", "t9", "t10"]


def test_replay_disabled_still_counts_the_gap_exactly():
    b = _bus(replay_size=0)
    s1 = b.open("A", "c")
    b.publish("A", _ev(1))
    last = asyncio.run(s1.next(0.05)).sse_id
    b.close(s1)
    for i in range(2, 5):
        b.publish("A", _ev(i))
    got = _drain(b.open("A", "c", last_event_id=last))
    assert [dict(e) for e in got] == [{"type": "stream_gap", "missed": 3,
                                       "recover": sse.GAP_RECOVER}]


def _reset():
    return {"type": "stream_reset", "reason": "unknown_last_event_id",
            "recover": sse.GAP_RECOVER}


@pytest.mark.parametrize("bad", ["", "garbage", "zzzzzzzz-1-1", "1-1", "x" * 65,
                                 "deadbeef-1-1\ndata: {}", "deadbeef-01-1"])
def test_unplaceable_ids_give_one_stream_reset(bad):
    b = _bus()
    b.publish("A", _ev(1))
    got = _drain(b.open("A", "c", last_event_id=bad))
    assert [dict(e) for e in got] == [_reset()]


def test_other_process_future_and_other_tenant_ids_reset_without_leaking():
    other = _bus()
    other.publish("A", _ev(1))
    foreign = f"{other.epoch}-1-1"
    b = _bus()
    sa = b.open("A", "c")
    b.publish("A", _ev(1))
    a_id = asyncio.run(sa.next(0.05)).sse_id
    b.publish("B", {"type": "task_created", "task_id": "b-secret", "specialization": "x"})
    assert [dict(e) for e in _drain(b.open("A", "c", last_event_id=foreign))] == [_reset()]
    epoch, gen, seq = a_id.split("-")
    future = f"{epoch}-{gen}-{int(seq) + 5}"
    assert [dict(e) for e in _drain(b.open("A", "c", last_event_id=future))] == [_reset()]
    # tenant B presenting tenant A's id: reset — never A's events, never B's replay by A's id
    got = _drain(b.open("B", "c", last_event_id=a_id))
    assert [dict(e) for e in got] == [_reset()]
    assert b.stats["resume_reset"] == 3


def test_evicted_tenant_log_never_reuses_ids(monkeypatch):
    monkeypatch.setattr(sse, "MAX_REPLAY_TENANTS", 2)
    b = _bus()
    sa = b.open("A", "c")
    b.publish("A", _ev(1))
    old = asyncio.run(sa.next(0.05)).sse_id
    b.publish("B", _ev(1))
    b.publish("C", _ev(1))                           # A's log evicted (LRU, bound 2)
    b.publish("A", _ev(2))                           # new generation for A
    new = asyncio.run(sa.next(0.05)).sse_id
    assert old != new and old.split("-")[1] != new.split("-")[1]
    b.close(sa)
    assert [dict(e) for e in _drain(b.open("A", "c", last_event_id=old))] == [_reset()]
    assert len(b._logs) == 2


def test_replay_is_bounded_per_tenant():
    b = _bus(replay_size=4)
    for i in range(100):
        b.publish("A", _ev(i))
    assert len(b._logs["A"][2]) == 4


def test_limits_reject_an_invalid_replay_size():
    with pytest.raises(ValueError):
        SSELimits.from_env({"AGOS_SSE_REPLAY_SIZE": "-1"})
    assert SSELimits.from_env({"AGOS_SSE_REPLAY_SIZE": "0"}).replay_size == 0
    assert SSELimits.from_env({}).replay_size == 100


# -- the real route ---------------------------------------------------------

def _request(last_event_id=None):
    from starlette.requests import Request
    headers = [] if last_event_id is None else [(b"last-event-id", last_event_id.encode())]
    return Request({"type": "http", "method": "GET", "path": "/api/events/stream",
                    "headers": headers, "query_string": b"", "client": ("127.0.0.1", 1)})


def _frame(chunk):
    m = re.fullmatch(r"(?:id: (?P<id>[^\n]+)\n)?data: (?P<data>[^\n]+)\n\n", chunk)
    assert m, chunk
    return m.group("id"), json.loads(m.group("data"))


def test_route_sends_ids_and_resumes_from_the_last_event_id_header(tenant_auth, monkeypatch):
    b = _bus()
    monkeypatch.setattr(server, "EVENT_BUS", b)

    async def go():
        r1 = await server.event_stream(_request(), api_key=tenant_auth.key)
        for i in (1, 2, 3):
            await server._publish(tenant_auth.tenant, _ev(i))
        it = r1.body_iterator
        seen = [_frame(await asyncio.wait_for(it.__anext__(), 2)) for _ in range(2)]
        await it.aclose()                            # drop after t2; t3 was queued
        await server._publish(tenant_auth.tenant, _ev(4))
        r2 = await server.event_stream(_request(seen[-1][0]), api_key=tenant_auth.key)
        it2 = r2.body_iterator
        resumed = [_frame(await asyncio.wait_for(it2.__anext__(), 2)) for _ in range(2)]
        await it2.aclose()
        r3 = await server.event_stream(_request("not-an-id"), api_key=tenant_auth.key)
        it3 = r3.body_iterator
        reset = _frame(await asyncio.wait_for(it3.__anext__(), 2))
        await it3.aclose()
        return seen, resumed, reset

    seen, resumed, reset = asyncio.run(go())
    assert [d["task_id"] for _i, d in seen] == ["t1", "t2"]
    assert all(i and ID_RE.match(i) for i, _d in seen + resumed)
    assert [d["task_id"] for _i, d in resumed] == ["t3", "t4"]
    assert reset == (None, _reset())
    from agent_civilization.core import contracts as C
    C.SSEFrame.model_validate({"event": reset[1]})
    for _i, d in seen + resumed:
        C.SSEFrame.model_validate({"event": d})


def test_cors_preflight_allows_last_event_id():
    """Cross-origin UI: without this the browser refuses the reconnect."""
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        r = c.options("/api/events/stream", headers={
            "Origin": "https://ui.example.org",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key,last-event-id",
        })
    assert r.status_code == 200, r.text
    assert "last-event-id" in r.headers.get("access-control-allow-headers", "").lower()
