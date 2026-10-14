"""R-072: the offline guard in tests/conftest.py really isolates deterministic tests.

These are the regression tests for RISK-019: if someone removes or weakens the
guard, a deterministic test could again depend on DuckDuckGo / DNS / any host.
"""
import asyncio
import socket
import urllib.request

import pytest

from agent_civilization.core.agents import real_dispatcher as rd


def test_external_socket_connect_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match="external network disabled"):
            s.connect(("93.184.216.34", 443))
    finally:
        s.close()


def test_external_dns_is_not_queried():
    assert socket.gethostbyname("api.duckduckgo.com").startswith("192.0.2.")  # RFC 5737 TEST-NET-1
    # R-100: getaddrinfo is answered by the same fake (the URL policy resolves
    # every host before connecting); the answer is TEST-NET, never real DNS.
    infos = socket.getaddrinfo("example.com", 443)
    assert {i[4][0] for i in infos} == {socket.gethostbyname("example.com")}
    assert infos[0][4][1] == 443


def test_loopback_is_untouched():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        c.connect(srv.getsockname())  # must not raise
    finally:
        c.close()
        srv.close()


def test_researcher_runs_real_logic_on_fake_transport(_offline_external_network):
    out = asyncio.run(rd._researcher({"prompt": "what is python"}))
    assert out["ok"] is True
    assert "what is python" in out["answer"]
    assert len(out["tool_output"]["related"]) == 3
    method, url = _offline_external_network[-1]
    assert method == "GET" and url.startswith("https://api.duckduckgo.com/?q=what+is+python")


def test_api_integrator_and_network_client_on_fake_transport(_offline_external_network):
    got = asyncio.run(rd._api_integrator({"prompt": "GET https://api.example.org/v1/items"}))
    assert got["ok"] and got["tool_output"]["status"] == 200
    assert got["tool_output"]["sample"]["path"] == "/v1/items"
    head = asyncio.run(rd._network_client({"prompt": "probe https://example.org/"}))
    assert head["ok"] and head["tool_output"]["status"] == 200
    dns = asyncio.run(rd._network_client({"prompt": "resolve example.org"}))
    assert dns["ok"] and dns["tool_output"]["ip"].startswith("192.0.2.")
    assert [m for m, _ in _offline_external_network] == ["GET", "HEAD"]


def test_urlopen_is_the_fake_here():
    assert urllib.request.urlopen.__name__ == "fake_urlopen"
