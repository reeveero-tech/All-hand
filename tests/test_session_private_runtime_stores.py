"""Log 319 — the test session must not share runtime stores with the host.

The rate-limit counter and the SSE admission store default to one host-wide
file in the temp dir (intended for production: every worker on a host shares
it). Before log 319 the test session used that same file, so ANOTHER process
on the machine calling ``reset()`` on it wiped this session's counters and
``test_every_civilization_route_is_rate_limited`` saw [200, 200, 200] —
reproduced 6/6 with a concurrent reset loop (log 319). ``tests/conftest.py``
now gives each session private files. These tests pin both halves: the paths
are private, and the exact interference no longer reaches the limiter.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_civilization.core import rate_limit_store, sse_admission

REPO = Path(__file__).resolve().parents[1]
HOST_RL = os.path.join(tempfile.gettempdir(), "agos_rate_limit.sqlite3")
HOST_SSE = os.path.join(tempfile.gettempdir(), "agos_sse_admission.sqlite3")


def test_session_stores_are_not_the_host_wide_files():
    assert rate_limit_store.default_db_path() != HOST_RL
    assert sse_admission.sse_admission_default_db_path() != HOST_SSE
    import server
    assert server._RATE_LIMIT_STORE._db_path != HOST_RL


def test_production_default_is_still_the_host_wide_file(monkeypatch):
    """The isolation is a TEST-session choice; the product default (shared by
    every worker on the host) is untouched."""
    monkeypatch.delenv("AGOS_RATE_LIMIT_DB", raising=False)
    monkeypatch.delenv("AGOS_SSE_ADMISSION_DB", raising=False)
    assert rate_limit_store.default_db_path() == HOST_RL
    assert sse_admission.sse_admission_default_db_path() == HOST_SSE


_RESETTER = """
import sys, time
from agent_civilization.core.rate_limit_store import RateLimitStore
s = RateLimitStore(sys.argv[1])
print("ready", flush=True)
end = time.time() + float(sys.argv[2])
while time.time() < end:
    s.reset()
"""


def test_a_foreign_process_resetting_the_host_file_cannot_reach_the_limiter(
        monkeypatch, tenant_auth):
    """The log-319 reproduction, inverted: a separate process hammers
    ``reset()`` on the HOST-WIDE file while this session's limiter counts.
    Before the fix the 3rd call was 200; now it must be 429."""
    import server
    env = dict(os.environ)
    env.pop("AGOS_RATE_LIMIT_DB", None)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-c", _RESETTER, HOST_RL, "8"],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        monkeypatch.setattr(server, "RATE_LIMIT_REQUESTS", 2)
        server.reset_rate_limits()
        client = TestClient(server.app, headers=tenant_auth.headers,
                            raise_server_exceptions=False)
        codes = []
        for _ in range(3):
            codes.append(client.get("/civilization/registry").status_code)
            time.sleep(0.05)  # give the foreign reset loop room to interleave
        assert proc.poll() is None, "the interfering process must still be running"
        assert codes == [200, 200, 429], codes
    finally:
        proc.kill()
        proc.wait(timeout=10)
        server.reset_rate_limits()
