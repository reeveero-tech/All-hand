"""SEC-10 (D-135) — /health/ready must not answer 200 while the storage the
service depends on is down.

Before (docs/audit/M8/logs/raw/368_r177_completion_measurements.log, storage
outage scenario): PostgREST SIGSTOPped, /health/ready stayed 200 for the whole
outage while every storage-backed request hung up to 11.2 s.

Storage kinds the server supports: ``volatile`` (no SUPABASE_URL/key: in
memory, no external dependency — never probed) and ``supabase`` (PostgREST,
hosted or local — required). The probe is one read-only HEAD, bounded by
AGOS_READY_STORAGE_TIMEOUT_S, cached AGOS_READY_STORAGE_CACHE_S, single-flight,
redirects refused, fixed reasons only.

Every fake store here is a real HTTP server on loopback (no mocks of aiohttp).
The live PostgREST outage/recovery test runs only with AGOS_SEC10_LIVE=1.
"""

import asyncio
import logging
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from agent_civilization.core import backpressure
from agent_civilization.storage import supabase_store as st

SERVICE_KEY = "sb_secret_SEC10_DO_NOT_LEAK_7f3a"
PUBLIC_KEY = "sb_publishable_SEC10_public_key"


class _Store:
    """A loopback PostgREST stand-in whose answer can be changed live."""

    def __init__(self):
        self.status = 200
        self.delay = 0.0
        self.location = None
        self.requests = []          # (method, path, headers)
        store = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                store.requests.append((self.command, self.path, dict(self.headers)))
                if store.delay:
                    time.sleep(store.delay)
                self.send_response(store.status)
                if store.location:
                    self.send_header("Location", store.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def store():
    s = _Store()
    yield s
    s.close()


@pytest.fixture
def client():
    from server import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # getattr: the red-first run (369) executes this file against the pre-fix
    # code, where these names do not exist — the tests then fail on behaviour
    # (200 while the store is down), not on fixture setup.
    _rr = getattr(st, "reset_storage_readiness", lambda: None)
    _rr()
    st.reset_storage_stats()
    monkeypatch.setattr(st, "READY_CACHE_S", 0.0, raising=False)
    monkeypatch.setattr(st, "READY_TIMEOUT_S", 0.5, raising=False)
    yield
    _rr()
    st.reset_storage_stats()


def _probes():
    return getattr(st, "_READY_STATE", {}).get("probes", 0)


def _configure(monkeypatch, url, key=SERVICE_KEY, source="SUPABASE_SERVICE_KEY"):
    monkeypatch.setattr(st, "SUPABASE_URL", url)
    monkeypatch.setattr(st, "SUPABASE_KEY", key)
    monkeypatch.setattr(st, "SUPABASE_KEY_SOURCE", source)


def _volatile(monkeypatch):
    _configure(monkeypatch, "", "", "")


def _closed_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------
# volatile: never probed, never made not-ready by storage
# --------------------------------------------------------------------------

def test_volatile_mode_is_ready_without_any_probe(monkeypatch, client):
    _volatile(monkeypatch)
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert _probes() == 0
    assert st.storage_kind() == "volatile"


def test_volatile_readiness_result_shape():
    assert asyncio.run(st.storage_readiness()) == {
        "kind": "volatile", "ready": True, "code": None, "reason": None, "latency_ms": None}


# --------------------------------------------------------------------------
# supabase: positive
# --------------------------------------------------------------------------

def test_reachable_store_is_ready(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    r = client.get("/health/ready")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"status", "agents", "specializations"}  # ready v1 unchanged
    assert st.storage_kind() == "supabase"


def test_probe_is_a_single_read_only_head(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    for _ in range(3):
        client.get("/health/ready")
    assert store.requests, "the probe never reached the store"
    methods = {m for m, _, _ in store.requests}
    assert methods == {"HEAD"}, methods          # never POST/PATCH/DELETE/PUT
    for _, path, headers in store.requests:
        assert path.startswith("/rest/v1/tasks?")
        assert "select=task_id" in path and "limit=1" in path
        assert headers.get("apikey") == SERVICE_KEY  # authenticates like the store


@pytest.mark.parametrize("status", [200, 206])
def test_success_statuses(monkeypatch, client, store, status):
    _configure(monkeypatch, store.url)
    store.status = status
    assert client.get("/health/ready").status_code == 200


# --------------------------------------------------------------------------
# supabase: negative — every failure is a 503 with a fixed reason
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status,reason", [
    (401, "storage credentials rejected"),
    (403, "storage credentials rejected"),
    (404, "storage schema missing"),
    (500, "storage unavailable"),
    (502, "storage unavailable"),
    (503, "storage unavailable"),
    (418, "storage check failed"),
])
def test_bad_store_status_is_not_ready(monkeypatch, client, store, status, reason):
    _configure(monkeypatch, store.url)
    store.status = status
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": reason}


def test_unreachable_store_is_not_ready(monkeypatch, client):
    _configure(monkeypatch, f"http://127.0.0.1:{_closed_port()}")
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": "storage unreachable"}


def test_hanging_store_is_bounded_by_the_timeout(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(st, "READY_TIMEOUT_S", 0.3)
    store.delay = 3.0
    t0 = time.monotonic()
    r = client.get("/health/ready")
    elapsed = time.monotonic() - t0
    assert r.status_code == 503
    assert r.json()["reason"] == "storage timeout"
    assert elapsed < 1.5, elapsed               # 0.3 s budget, not the 3 s hang


def test_redirect_is_refused_and_key_never_replayed(monkeypatch, client, store):
    other = _Store()
    try:
        _configure(monkeypatch, store.url)
        store.status, store.location = 302, other.url + "/rest/v1/tasks"
        r = client.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["reason"] == "storage check failed"
        assert other.requests == []               # the key went nowhere else
    finally:
        other.close()


def test_public_key_is_not_ready_without_network(monkeypatch, client, store):
    _configure(monkeypatch, store.url, PUBLIC_KEY, "SUPABASE_ANON_KEY")
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "reason": "storage credentials cannot write"}
    assert store.requests == []


def test_civilization_failure_still_wins(monkeypatch, client, store):
    """The storage check is added after the civilization check, not instead."""
    import server
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(server, "civilization_status",
                        lambda c: {"ready": False, "reason": "civilization not initialized"})
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["reason"] == "civilization not initialized"
    assert store.requests == []


# --------------------------------------------------------------------------
# no secrets / connection details in responses or logs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["401", "500", "timeout", "unreachable", "redirect"])
def test_no_secret_or_connection_detail_leaks(monkeypatch, client, store, caplog, mode):
    url = store.url
    if mode == "unreachable":
        url = f"http://127.0.0.1:{_closed_port()}"
    elif mode == "timeout":
        store.delay = 2.0
        monkeypatch.setattr(st, "READY_TIMEOUT_S", 0.2)
    elif mode == "redirect":
        store.status, store.location = 307, "http://127.0.0.1:1/x"
    else:
        store.status = int(mode)
    _configure(monkeypatch, url)
    caplog.set_level(logging.DEBUG)
    r = client.get("/health/ready")
    assert r.status_code == 503
    port = url.rsplit(":", 1)[1]
    blobs = [r.text, caplog.text]
    for blob in blobs:
        for secret in (SERVICE_KEY, "Bearer", "127.0.0.1:" + port, port + "/rest",
                       "ClientConnectorError", "TimeoutError", "Traceback"):
            assert secret not in blob, (mode, secret)
    assert set(r.json()) == {"status", "reason"}
    assert r.json()["reason"] in st.READY_REASONS.values()


# --------------------------------------------------------------------------
# outage and recovery (loopback store)
# --------------------------------------------------------------------------

def test_outage_then_recovery(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    assert client.get("/health/ready").status_code == 200
    store.status = 503                                   # store down
    assert client.get("/health/ready").status_code == 503
    store.status, store.delay = 200, 2.0                 # store hangs
    assert client.get("/health/ready").json()["reason"] == "storage timeout"
    store.delay = 0.0                                    # store back
    assert client.get("/health/ready").status_code == 200


def test_store_process_gone_then_back(monkeypatch, client):
    s = _Store()
    port = s.httpd.server_address[1]
    _configure(monkeypatch, s.url)
    assert client.get("/health/ready").status_code == 200
    s.close()
    assert client.get("/health/ready").json()["reason"] == "storage unreachable"
    # a new store on the same port: readiness recovers with no restart
    s2 = None
    for _ in range(50):
        try:
            s2 = _Store.__new__(_Store)
            _Store.__init__(s2)
            break
        except OSError:
            time.sleep(0.05)
    try:
        _configure(monkeypatch, s2.url)
        assert client.get("/health/ready").status_code == 200
    finally:
        s2.close()
    assert port  # (documented: a store on any address recovers readiness)


# --------------------------------------------------------------------------
# bounded load on the store: cache + single flight
# --------------------------------------------------------------------------

def test_cache_bounds_probe_rate(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(st, "READY_CACHE_S", 60.0)
    for _ in range(25):
        assert client.get("/health/ready").status_code == 200
    assert len(store.requests) == 1


def test_cached_failure_also_bounded_and_expires(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(st, "READY_CACHE_S", 0.3)
    store.status = 503
    for _ in range(10):
        assert client.get("/health/ready").status_code == 503
    assert len(store.requests) == 1
    store.status = 200
    time.sleep(0.35)
    assert client.get("/health/ready").status_code == 200
    assert len(store.requests) == 2


def test_concurrent_callers_share_one_probe(monkeypatch, store):
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(st, "READY_CACHE_S", 60.0)
    store.delay = 0.2

    async def many():
        return await asyncio.gather(*[st.storage_readiness() for _ in range(50)])

    results = asyncio.run(many())
    assert all(r["ready"] for r in results)
    assert len(store.requests) == 1


def test_config_change_never_reuses_stale_answer(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    monkeypatch.setattr(st, "READY_CACHE_S", 60.0)
    assert client.get("/health/ready").status_code == 200
    _configure(monkeypatch, f"http://127.0.0.1:{_closed_port()}")
    assert client.get("/health/ready").status_code == 503


# --------------------------------------------------------------------------
# the probe is invisible to the data path
# --------------------------------------------------------------------------

def test_probe_touches_neither_stats_nor_breaker(monkeypatch, client, store):
    _configure(monkeypatch, store.url)
    backpressure.reset_all()
    before = backpressure.breaker("storage").snapshot()
    store.status = 503
    for _ in range(10):
        client.get("/health/ready")
    stats = st.storage_stats()
    assert stats["total_ok"] == 0 and stats["total_failed"] == 0
    assert stats["last_ok"] is None
    after = backpressure.breaker("storage").snapshot()
    assert (after["state"], after["failures"]) == (before["state"], before["failures"])


def test_contract_shapes(monkeypatch, client, store):
    from agent_civilization.core import contracts
    _configure(monkeypatch, store.url)
    ok = client.get("/health/ready")
    contracts.Ready.model_validate(ok.json())
    store.status = 500
    bad = client.get("/health/ready")
    contracts.NotReady.model_validate(bad.json())


@pytest.mark.parametrize("var,value,ok", [
    ("AGOS_READY_STORAGE_TIMEOUT_S", "2.5", True),
    ("AGOS_READY_STORAGE_TIMEOUT_S", "0", False),       # no unbounded / zero budget
    ("AGOS_READY_STORAGE_TIMEOUT_S", "999", False),
    ("AGOS_READY_STORAGE_CACHE_S", "0", True),
    ("AGOS_READY_STORAGE_CACHE_S", "3600", False),
])
def test_env_bounds_fail_fast(var, value, ok):
    """Out-of-range settings refuse to start (same rule as R-164 limits)."""
    import sys
    env = dict(os.environ, **{var: value})
    p = subprocess.run([sys.executable, "-c",
                        "from agent_civilization.storage import supabase_store as s;"
                        "print(s.READY_TIMEOUT_S, s.READY_CACHE_S)"],
                       env=env, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert (p.returncode == 0) is ok, p.stderr[-300:]


def test_defaults():
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGOS_READY_STORAGE_")}
    p = subprocess.run([sys.executable, "-c",
                        "from agent_civilization.storage import supabase_store as s;"
                        "print(s.READY_TIMEOUT_S, s.READY_CACHE_S)"],
                       env=env, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert p.stdout.split() == ["1.0", "1.0"], p.stderr[-300:]


# --------------------------------------------------------------------------
# live: a real PostgREST stack (opt-in, local only)
# --------------------------------------------------------------------------

LIVE = os.environ.get("AGOS_SEC10_LIVE") == "1"


def _postgrest_pid():
    out = subprocess.run(["pgrep", "-x", "postgrest"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


@pytest.mark.skipif(not LIVE, reason="needs the local PostgREST stack (AGOS_SEC10_LIVE=1)")
def test_live_postgrest_sigstop_and_recovery(monkeypatch, client):
    url = os.environ.get("AGOS_SEC10_URL", "http://127.0.0.1:54321")
    key = open(os.environ.get("AGOS_SEC10_KEY_FILE", "/tmp/svc_key")).read().strip()
    pid = _postgrest_pid()
    assert pid, "postgrest not running"
    _configure(monkeypatch, url, key)
    monkeypatch.setattr(st, "READY_TIMEOUT_S", 1.0)
    assert client.get("/health/ready").status_code == 200
    os.kill(pid, signal.SIGSTOP)
    try:
        t0 = time.monotonic()
        r = client.get("/health/ready")
        assert r.status_code == 503 and r.json()["reason"] == "storage timeout"
        assert time.monotonic() - t0 < 2.5
    finally:
        os.kill(pid, signal.SIGCONT)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.get("/health/ready").status_code == 200:
            break
        time.sleep(0.2)
    else:
        pytest.fail("readiness did not recover after SIGCONT")
    _configure(monkeypatch, url, "sb_secret_wrong_key_sec10")
    assert client.get("/health/ready").json()["reason"] == "storage credentials rejected"
