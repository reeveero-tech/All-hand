"""Runs are private to the browser that created them (X-Client-Key), live and from history."""
import time

import pytest
from fastapi.testclient import TestClient

from app import main, store
from app.orchestrator import Run
from app.tools import _is_public_host, fetch_url

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}


@pytest.fixture
def client(monkeypatch):
    main.RUNS.clear()
    run = Run(goal="سر المستخدم أ", llm_public={}, owner=main._owner(A["X-Client-Key"]))
    run.status = "done"
    main.RUNS[run.id] = run
    yield TestClient(main.app), run
    main.RUNS.clear()


def test_key_required(client):
    c, run = client
    assert c.get("/api/runs").status_code == 400
    assert c.get(f"/api/runs/{run.id}").status_code == 400
    assert c.get("/api/runs", headers={"X-Client-Key": "short"}).status_code == 400
    assert c.post("/api/runs", json={"goal": "x"}).status_code == 400


def test_list_shows_only_own_runs(client):
    c, run = client
    assert [r["id"] for r in c.get("/api/runs", headers=A).json()] == [run.id]
    assert c.get("/api/runs", headers=B).json() == []


def test_other_client_cannot_read_cancel_or_stream(client):
    c, run = client
    got = c.get(f"/api/runs/{run.id}", headers=A).json()
    assert got["goal"] == "سر المستخدم أ" and "owner" not in got
    for r in (c.get(f"/api/runs/{run.id}", headers=B), c.post(f"/api/runs/{run.id}/cancel", headers=B),
              c.get(f"/api/runs/{run.id}/events", headers=B)):
        assert r.status_code == 404
    assert c.get("/api/runs/doesnotexist", headers=B).json() == c.get(f"/api/runs/{run.id}", headers=B).json()


def test_history_rows_checked_for_owner(client, monkeypatch):
    c, _ = client
    rows = {"aaaaaaaaaaaa": {"id": "aaaaaaaaaaaa", "goal": "g", "owner": main._owner(A["X-Client-Key"])},
            "bbbbbbbbbbbb": {"id": "bbbbbbbbbbbb", "goal": "قديم بلا مالك"}}

    async def load(run_id):
        return dict(rows[run_id]) if run_id in rows else None
    monkeypatch.setattr(store, "load", load)
    assert c.get("/api/runs/aaaaaaaaaaaa", headers=A).json()["goal"] == "g"
    assert "owner" not in c.get("/api/runs/aaaaaaaaaaaa", headers=A).json()
    assert c.get("/api/runs/aaaaaaaaaaaa", headers=B).status_code == 404
    assert c.get("/api/runs/bbbbbbbbbbbb", headers=A).status_code == 404  # pre-ownership rows stay private


def test_created_run_is_owned_and_saved_with_owner(monkeypatch):
    main.RUNS.clear()
    saved = []

    async def save(data):
        saved.append(data)
    monkeypatch.setattr(store, "save", save)

    async def fake_execute(self):
        self.run.status = "done"
    monkeypatch.setattr(main.Orchestrator, "execute", fake_execute)
    with TestClient(main.app) as c:
        rid = c.post("/api/runs", json={"goal": "x"}, headers=A).json()["id"]
        for _ in range(50):
            if saved:
                break
            time.sleep(0.01)
        assert [r["id"] for r in c.get("/api/runs", headers=A).json()] == [rid]
        assert c.get("/api/runs", headers=B).json() == []
    assert saved and saved[0]["owner"] == main._owner(A["X-Client-Key"])
    main.RUNS.clear()


def test_invalid_run_ids_and_keys(client):
    c, run = client
    for bad in ("x" * 300, "a b", "..%2F..", "id.eq.x", "*"):
        r = c.get(f"/api/runs/{bad}", headers=A)
        assert r.status_code in (400, 404), bad
    for key in ("", "a" * 19, "a" * 201, "cli_" + "a" * 30 + "!"):
        assert c.get("/api/runs", headers={"X-Client-Key": key}).status_code == 400, key


def test_owner_hash_never_returned(client):
    c, run = client
    body = c.get("/api/runs", headers=A).text + c.get(f"/api/runs/{run.id}", headers=A).text
    assert run.owner not in body and A["X-Client-Key"] not in body


def test_other_client_cancel_does_not_touch_task(client):
    c, run = client
    cancelled = []

    class T:
        def cancel(self):
            cancelled.append(1)
    main.TASKS[run.id] = T()
    try:
        assert c.post(f"/api/runs/{run.id}/cancel", headers=B).status_code == 404
        assert not cancelled
        assert c.post(f"/api/runs/{run.id}/cancel", headers=A).status_code == 200
        assert cancelled
    finally:
        main.TASKS.pop(run.id, None)


def test_private_addresses_blocked():
    for host in ("127.0.0.1", "0.0.0.0", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1"):
        assert not _is_public_host(host), host


PUBLIC = {"public.example": "93.184.215.14", "other.example": "93.184.215.15"}


def _mock(monkeypatch, handler, resolve=None):
    import httpx
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr("app.tools._resolve_public", resolve or (lambda h: PUBLIC.get(h)))


async def test_redirect_to_internal_address_is_blocked(monkeypatch):
    import httpx
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.headers["host"] == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET-METADATA", headers={"content-type": "text/plain"})

    _mock(monkeypatch, handler)
    out = await fetch_url("http://public.example/start")
    assert "ممنوعة" in out and "SECRET" not in out
    assert len(seen) == 1  # the internal hop was never requested


async def test_public_redirect_is_followed(monkeypatch):
    import httpx

    def handler(request: httpx.Request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, text="final page", headers={"content-type": "text/plain"})

    _mock(monkeypatch, handler)
    out = await fetch_url("http://public.example/a")
    assert "final page" in out and "public.example/b" in out


async def test_connection_is_pinned_to_checked_address(monkeypatch):
    """DNS rebinding: the address that passed the check is the one we connect to."""
    import httpx
    hosts = []

    def handler(request: httpx.Request):
        hosts.append((request.url.host, request.headers["host"], request.extensions.get("sni_hostname")))
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    calls = []

    def resolve(h):
        calls.append(h)
        return PUBLIC.get(h)
    _mock(monkeypatch, handler, resolve)
    await fetch_url("https://public.example:8443/x")
    assert calls == ["public.example"]
    assert hosts == [("93.184.215.14", "public.example:8443", "public.example")]


async def test_too_many_redirects(monkeypatch):
    import httpx
    _mock(monkeypatch, lambda r: httpx.Response(302, headers={"location": "/again"}))
    assert "كثيرة" in await fetch_url("http://public.example/")


async def test_redirect_to_other_scheme_or_credentials_blocked(monkeypatch):
    import httpx
    _mock(monkeypatch, lambda r: httpx.Response(302, headers={"location": "file:///etc/passwd"}))
    assert "غير صالح" in await fetch_url("http://public.example/")
    assert "غير صالح" in await fetch_url("http://user:pw@public.example/")
    assert "غير صالح" in await fetch_url("ftp://public.example/")


async def test_body_is_capped(monkeypatch):
    import httpx
    from app import tools
    monkeypatch.setattr(tools, "MAX_BYTES", 1000)
    _mock(monkeypatch, lambda r: httpx.Response(200, content=b"A" * 50_000, headers={"content-type": "text/plain"}))
    out = await fetch_url("http://public.example/")
    assert out.count("A") <= 1000


def test_tricky_addresses_blocked():
    import ipaddress
    from app.tools import _ip_is_public
    for a in ("::ffff:127.0.0.1", "::ffff:169.254.169.254", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",
              "2002:7f00:1::", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "fe80::1", "fc00::1", "0.0.0.0",
              "255.255.255.255", "224.0.0.1", "192.0.0.1", "198.18.0.1"):
        assert not _ip_is_public(ipaddress.ip_address(a)), a
    for a in ("8.8.8.8", "::ffff:8.8.8.8", "2606:4700:4700::1111"):
        assert _ip_is_public(ipaddress.ip_address(a)), a


def test_hostnames_resolving_to_internal_blocked():
    for host in ("localhost", "0x7f000001", "2130706433", "127.1", "[::1]", "", "no-such-host.invalid"):
        assert not _is_public_host(host), host
