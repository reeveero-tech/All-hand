"""R-042: payload shape sent by supabase_store (offline — no network).

The live round-trip is covered by tests/test_r042_live_storage.py and
scripts/run_r042_storage_verify.py. These tests pin the regression found live:
jsonb fields were sent as JSON *strings* (double-encoded) instead of objects.
"""
import asyncio
import logging

from agent_civilization.storage import supabase_store as ss


class _Resp:
    def __init__(self, status, text=""):
        self.status, self._text = status, text

    async def text(self):
        return self._text

    async def json(self):
        return []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self, status=201):
        self.calls, self.status, self.closed = [], status, False

    def _req(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return _Resp(self.status, '{"message":"denied"}')

    def post(self, url, **kw):
        return self._req("POST", url, **kw)

    def patch(self, url, **kw):
        return self._req("PATCH", url, **kw)

    def get(self, url, **kw):
        return self._req("GET", url, **kw)


def _store(monkeypatch, status=201):
    monkeypatch.setattr(ss, "SUPABASE_URL", "http://x")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "k")
    st = ss.SupabaseStorage()
    sess = _Session(status)

    async def _get():
        return sess
    st._get_session = _get
    return st, sess


def test_update_task_sends_result_as_object(monkeypatch):
    st, sess = _store(monkeypatch, 204)
    result = {"ok": True, "nested": {"n": [1, 2]}}
    assert asyncio.run(st.update_task("t1", "completed", agent_name="a", result=result, elapsed_s=0.1))
    payload = sess.calls[0][2]["json"]
    assert payload["result"] == result and isinstance(payload["result"], dict)
    assert payload["finished_at"] == "now()"


def test_insert_event_sends_event_data_as_object(monkeypatch):
    st, sess = _store(monkeypatch, 201)
    assert asyncio.run(st.insert_event("e", {"task_id": "t1", "ok": True}))
    assert sess.calls[0][2]["json"]["event_data"] == {"task_id": "t1", "ok": True}


def test_jsonable_coerces_non_json_types():
    class Odd:
        def __str__(self):
            return "odd"
    assert ss._jsonable({"a": Odd(), "b": (1, 2)}) == {"a": "odd", "b": [1, 2]}


def test_rejected_write_returns_false_and_logs(monkeypatch, caplog):
    st, _ = _store(monkeypatch, 401)
    with caplog.at_level(logging.WARNING, logger="storage"):
        assert asyncio.run(st.insert_task("t1", "analysis", "p")) is False
    assert "insert_task rejected: HTTP 401" in caplog.text


def test_rejected_read_returns_empty_and_logs(monkeypatch, caplog):
    st, _ = _store(monkeypatch, 500)
    with caplog.at_level(logging.WARNING, logger="storage"):
        assert asyncio.run(st.get_events(scope="some_tenant")) == []  # SEC-02: reads are always scoped
    assert "get_events rejected: HTTP 500" in caplog.text


def test_service_key_preferred_over_anon_key(monkeypatch):
    """R-042b: server-side secret/service key wins (RLS has no anon policies)."""
    import importlib
    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service")
    try:
        assert importlib.reload(ss).SUPABASE_KEY == "service"
        monkeypatch.delenv("SUPABASE_SERVICE_KEY")
        assert importlib.reload(ss).SUPABASE_KEY == "anon"  # backward-compatible fallback
    finally:
        monkeypatch.delenv("SUPABASE_URL")
        monkeypatch.delenv("SUPABASE_ANON_KEY")
        importlib.reload(ss)
