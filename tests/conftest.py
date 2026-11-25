import pytest

from app import tools


@pytest.fixture(autouse=True)
def _fresh_search_state(monkeypatch):
    """Search results are cached and DuckDuckGo is gated process-wide; tests must not see each other's."""
    tools._cache.clear()
    monkeypatch.setitem(tools._ddg_gate, "cool_until", 0.0)
    monkeypatch.setitem(tools._ddg_gate, "next", 0.0)
    monkeypatch.setitem(tools._ddg_gate, "lock", None)
    monkeypatch.setattr(tools, "AUTO_READ", 0)

    async def no_news(query):  # no network in tests; a test that needs news results patches this itself
        return []
    monkeypatch.setattr(tools, "_gnews", no_news)
    yield
    tools._cache.clear()
