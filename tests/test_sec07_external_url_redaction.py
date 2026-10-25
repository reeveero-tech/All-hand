"""SEC-07 (batch 293): an external agent's URL may carry credentials — userinfo
(``https://user:pass@host``), a query token (``?api_key=…``), a fragment, or a
secret path segment (``/bot<token>/…``). Before this fix
``ExternalAgentAdapter.snapshot()`` returned the full URL, and the snapshot is
served WITHOUT authentication by ``GET /api/agents`` (catalog route, public by
design) — any anonymous client could read the admin's credential.

Acceptance (fixed before the change): no response of any public or tenant
route, nor the admin registration response, contains the userinfo, query,
fragment or path of a registered external agent URL; the snapshot exposes only
``scheme://host[:port]`` (the same disclosure as the R-159 log line). The
adapter still calls the full URL (behaviour unchanged).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_civilization.agents.external_adapter import ExternalAgentAdapter, public_url

SECRET_URLS = [
    ("https://alice:s3cr3t-PW@agent.example.com/v1/chat", "https://agent.example.com"),
    ("https://agent.example.com:8443/v1/chat?api_key=TOK-123&x=1", "https://agent.example.com:8443"),
    ("http://10.0.0.5:11434/bot987654:ABCDEF/hook#frag-TOK", "http://10.0.0.5:11434"),
    ("https://[::1]:9000/p?token=T", "https://[::1]:9000"),
    ("https://AGENT.Example.com/", "https://agent.example.com"),
]
SECRETS = ("s3cr3t-PW", "alice", "TOK-123", "api_key", "987654", "ABCDEF", "frag-TOK", "token=T", "/v1/chat")


@pytest.mark.parametrize("url,expected", SECRET_URLS)
def test_public_url_keeps_only_scheme_host_port(url, expected):
    assert public_url(url) == expected


@pytest.mark.parametrize("url", ["", "not a url", "https://", "https://u:p@:99999/x"])
def test_public_url_never_echoes_unparseable_input(url):
    out = public_url(url)
    assert "u:p" not in out and "/x" not in out


@pytest.mark.parametrize("url,expected", SECRET_URLS)
def test_snapshot_redacts_but_adapter_keeps_full_url(url, expected):
    a = ExternalAgentAdapter("ext", url, api_key="K-SECRET")
    snap = a.snapshot()
    assert snap["url"] == expected
    assert "K-SECRET" not in repr(snap)
    assert a.url == url  # outbound calls still use the configured URL


def test_public_routes_do_not_leak_external_url_credentials(monkeypatch, tenant_auth):
    import server
    url = SECRET_URLS[0][0] + "?api_key=TOK-123#frag-TOK"
    monkeypatch.setattr(server, "EXTERNAL_AGENTS",
                        [ExternalAgentAdapter("ext-leak", url, api_key="K-SECRET")])
    with TestClient(server.app, raise_server_exceptions=False) as c:
        bodies = []
        for path in ("/api/agents", "/api/agents?specialization=external", "/api/system/status",
                     "/metrics", "/api", "/health"):
            r = c.get(path)
            bodies.append((path, r.text))
        for path, body in bodies:
            for s in SECRETS + ("K-SECRET",):
                assert s not in body, f"{path} leaks {s!r}"
        agents = c.get("/api/agents").json()["agents"]
        ext = [a for a in agents if a["name"] == "ext-leak"]
        assert ext and ext[0]["url"] == "https://agent.example.com"


def test_admin_registration_response_does_not_echo_credentials(monkeypatch, tenant_auth):
    import server
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [])
    url = "http://bob:PW-XYZ@127.0.0.1:9/secret-path?key=TOK-REG#f"
    with TestClient(server.app, raise_server_exceptions=False) as c:
        r = c.post("/api/agents/external", headers=tenant_auth.admin_headers,
                   json={"name": "ext-reg", "url": url, "api_key": "K-REG"})
        assert r.status_code == 201, r.text
        for s in ("bob", "PW-XYZ", "secret-path", "TOK-REG", "K-REG"):
            assert s not in r.text
        assert r.json()["registered"]["url"] == "http://127.0.0.1:9"
        assert server.EXTERNAL_AGENTS[0].url == url
