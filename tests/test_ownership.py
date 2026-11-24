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
    rows = {"mine": {"id": "mine", "goal": "g", "owner": main._owner(A["X-Client-Key"])},
            "legacy": {"id": "legacy", "goal": "قديم بلا مالك"}}

    async def load(run_id):
        return dict(rows[run_id]) if run_id in rows else None
    monkeypatch.setattr(store, "load", load)
    assert c.get("/api/runs/mine", headers=A).json()["goal"] == "g"
    assert "owner" not in c.get("/api/runs/mine", headers=A).json()
    assert c.get("/api/runs/mine", headers=B).status_code == 404
    assert c.get("/api/runs/legacy", headers=A).status_code == 404  # pre-ownership rows stay private


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


def test_private_addresses_blocked():
    for host in ("127.0.0.1", "0.0.0.0", "10.0.0.1", "169.254.169.254", "100.64.0.1", "::1"):
        assert not _is_public_host(host), host


async def test_redirect_to_internal_address_is_blocked(monkeypatch):
    import httpx

    def handler(request: httpx.Request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET-METADATA", headers={"content-type": "text/plain"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr("app.tools._is_public_host", lambda h: h == "public.example")
    out = await fetch_url("http://public.example/start")
    assert "ممنوعة" in out and "SECRET" not in out


async def test_public_redirect_is_followed(monkeypatch):
    import httpx

    def handler(request: httpx.Request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, text="final page", headers={"content-type": "text/plain"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr("app.tools._is_public_host", lambda h: True)
    out = await fetch_url("http://public.example/a")
    assert "final page" in out and "/b" in out
