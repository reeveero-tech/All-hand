"""When DuckDuckGo lite is blocked the html endpoint is tried; an empty search says why."""
import asyncio

from app import tools


def test_falls_back_to_ddg_html(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def lite(q):
        return []

    async def html_(q):
        return [{"title": "نسكافيه لاتيه", "url": "https://example.sa/p", "snippet": "31 ريال"}]
    monkeypatch.setattr(tools, "_ddg", lite)
    monkeypatch.setattr(tools, "_ddg_html", html_)
    out = asyncio.run(tools.web_search("قهوة"))
    assert out.startswith("المصدر: DuckDuckGo") and "https://example.sa/p" in out


def test_empty_search_explains(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def none(*a):
        return []
    for f in ("_ddg", "_ddg_html", "_wikipedia"):
        monkeypatch.setattr(tools, f, none)
    assert "يُحجب" in asyncio.run(tools.web_search("قهوة"))
