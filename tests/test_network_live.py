"""R-072: the three internet-facing agents against the REAL internet.

Marked ``network`` → deselected by default; run with ``pytest -m network``
(CI job ``network``, not a required check — an outage of DuckDuckGo or DNS must
not block merges, but it stays visible). Coverage of the agents' real network
behaviour is therefore kept, just separated from the deterministic suite.
"""
import asyncio

import pytest

from agent_civilization.core.agents import real_dispatcher as rd

pytestmark = pytest.mark.network


def test_researcher_live_duckduckgo():
    out = asyncio.run(rd._researcher({"prompt": "Python programming language"}))
    assert out["ok"] is True, out
    assert out["tool_output"]["abstract"] or out["tool_output"]["related"], out


def test_api_integrator_live_get():
    out = asyncio.run(rd._api_integrator({"prompt": "GET https://en.wikipedia.org/api/rest_v1/page/summary/Python_(programming_language)"}))
    assert out["ok"] is True, out
    assert out["tool_output"]["status"] == 200
    assert isinstance(out["tool_output"]["sample"], dict) and out["tool_output"]["sample"].get("title")


def test_network_client_live_dns_and_head():
    dns = asyncio.run(rd._network_client({"prompt": "resolve example.com"}))
    assert dns["ok"] is True, dns
    assert not dns["tool_output"]["ip"].startswith("192.0.2."), "guard leaked into a network test"
    head = asyncio.run(rd._network_client({"prompt": "HEAD https://example.com/"}))
    assert head["ok"] is True, head
    assert head["tool_output"]["status"] == 200
