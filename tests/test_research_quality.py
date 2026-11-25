"""Researchers are told to use what search returned; pages actually read are kept as sources."""
import asyncio
import json

from app import agents
from app.agents import ROLES, run_agent, system_prompt
from app.llm import extract_json


def test_research_rules_only_for_search_roles():
    assert "لا تكتب «لم أجد معلومات»" in system_prompt(ROLES["researcher"], False)
    assert "fetch_url أهم رابط" in system_prompt(ROLES["researcher"], False)
    assert "لم أجد معلومات" not in system_prompt(ROLES["writer"], False)


def test_pages_read_become_sources(monkeypatch):
    async def fake_tool(name, args, allowed):
        if args.get("url") == "https://ok.example/p":
            return "HTTP 200 — https://ok.example/p\n\nالسعر 9 ريال"
        return "خطأ في الجلب: ConnectError"
    monkeypatch.setattr(agents, "call_tool", fake_tool)
    script = iter([
        {"action": "tool", "tool": "fetch_url", "input": {"url": "https://ok.example/p"}},
        {"action": "tool", "tool": "fetch_url", "input": {"url": "https://bad.example"}},
        {"action": "final", "output": "السعر 9 ريال", "sources": ["https://claimed.example", "https://ok.example/p"]},
    ])

    class LLM:
        async def chat_json(self, messages, *a, **k):
            return next(script)

    async def emit(*a):
        pass
    res = asyncio.run(run_agent(LLM(), ROLES["researcher"], "ابحث", "", emit, False))
    assert res["sources"] == ["https://ok.example/p", "https://claimed.example"]  # read first, no duplicates, failed fetch absent
