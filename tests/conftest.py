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
    monkeypatch.setattr(tools, "_real_tavily", tools._tavily, raising=False)
    monkeypatch.setattr(tools, "_tavily", no_news)
    monkeypatch.setitem(tools._tavily_gate, "cool_until", 0.0)
    for p in tools.registry.REGISTRY.values():  # circuit breakers and counters are process-wide too
        p.fails, p.open_until, p.last_error = 0, 0.0, ""
        p.stats = {k: 0 for k in p.stats}
    yield
    tools._cache.clear()


@pytest.fixture(autouse=True)
def _research_manager_off(monkeypatch):
    """Older tests drive agents with scripted models; the research manager is enabled only by tests that test it."""
    from app.research import manager
    monkeypatch.setattr(manager, "ENABLED", False)
    yield
