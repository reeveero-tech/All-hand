"""R-100 (D-044): runtime safety of the real agent tools.

Before R-100, in the default open mode (no AGOS_API_KEY), an anonymous
``POST /api/tasks`` to ``test_runner`` ran arbitrary Python that could print
the server's secrets; URL tools reached loopback/private/metadata addresses;
SQLite had no time budget.  Evidence: docs/audit/M6/logs/raw/27_r100_probe_before_main.log.

Every assertion below exercises the real code path (real subprocess, real
local HTTP server, real SQLite) — nothing is mocked except the offline DNS /
transport for external hosts that tests/conftest.py provides for all tests.
"""
import asyncio
import http.server
import importlib
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent_civilization.agents.external_adapter import ExternalAgentAdapter
from agent_civilization.core import safety
from agent_civilization.core.agents import real_dispatcher as rd


# ---------------------------------------------------------------- helpers

class _CountingHandler(http.server.BaseHTTPRequestHandler):
    hits = 0

    def _ok(self):
        type(self).hits += 1
        body = b'{"internal": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = _ok

    def log_message(self, *a):
        pass


@pytest.fixture
def internal_server():
    _CountingHandler.hits = 0
    srv = http.server.HTTPServer(("127.0.0.1", 0), _CountingHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/internal", _CountingHandler
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def open_mode_server(monkeypatch):
    """server module reloaded with the production default: no API key."""
    monkeypatch.delenv("AGOS_API_KEY", raising=False)
    import server
    srv = importlib.reload(server)
    yield srv
    importlib.reload(server)


def _run_task(client, spec, prompt):
    tid = client.post("/api/tasks", json={"specialization": spec, "prompt": prompt}).json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("completed", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish: {t}")


# ---------------------------------------------------------------- 1. code execution policy

def test_policy_matrix(monkeypatch):
    assert safety.code_execution_policy()[0] is False            # open mode default
    monkeypatch.setenv("AGOS_API_KEY", "k")
    assert safety.code_execution_policy()[0] is True             # authenticated
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "0")
    assert safety.code_execution_policy()[0] is False            # explicit deny wins
    monkeypatch.delenv("AGOS_API_KEY")
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")
    assert safety.code_execution_policy()[0] is True             # explicit opt-in


def test_open_mode_test_runner_does_not_execute(tmp_path):
    marker = tmp_path / "ran"
    out = asyncio.run(rd.dispatch("test_runner", {"data": f"open({str(marker)!r}, 'w').write('x')\n"}))
    assert out["ok"] is False and "disabled in open mode" in out["error"]
    assert out["tool_output"] == {"executed": False}
    assert not marker.exists()


def test_open_mode_api_does_not_leak_secrets(open_mode_server, monkeypatch, request):
    # SEC-02: no anonymous tasks any more; a tenant user key (AGOS_TENANT_KEYS,
    # no AGOS_API_KEY) is used — code execution must still be off (R-100 policy).
    auth = request.getfixturevalue("tenant_auth")
    monkeypatch.setenv("R100_PROBE_SECRET", "must-not-leak-7f3a")
    with TestClient(open_mode_server.app, headers=auth.headers) as c:
        t = _run_task(c, "test_runner", "```python\nimport os; print(os.environ.get('R100_PROBE_SECRET'))\n```")
        assert "must-not-leak-7f3a" not in str(t)
        assert "disabled in open mode" in t["result"]["result"]["error"]
        policy = c.get("/api/system/status").json()["security"]["tool_policy"]
        assert policy["code_execution"]["allowed"] is False


# ---------------------------------------------------------------- 2. sandbox (opt-in)

@pytest.mark.code_exec
def test_sandbox_child_gets_no_server_environment(monkeypatch):
    monkeypatch.setenv("R100_PROBE_SECRET", "must-not-leak-7f3a")
    code = "import os, sys\nprint(sorted(os.environ))\nprint(os.getcwd())\nprint(sys.flags.isolated)\n"
    out = asyncio.run(rd.dispatch("test_runner", {"data": code}))
    assert out["ok"] is True and out["tool_output"]["sandbox"] is True
    env_line, cwd_line, isolated = out["tool_output"]["stdout"].splitlines()
    assert "R100_PROBE_SECRET" not in env_line and "SUPABASE" not in env_line
    assert os.path.basename(cwd_line).startswith("agos-sbx-")
    assert not os.path.exists(cwd_line)                           # temp dir removed
    assert isolated == "1"                                        # python -I


@pytest.mark.code_exec
def test_sandbox_child_cannot_read_server_startup_environment():
    assert safety.harden_server_process() is True
    code = ("import os\ntry:\n    open('/proc/%d/environ' % os.getppid(), 'rb').read()\n"
            "    print('READ')\nexcept PermissionError:\n    print('DENIED')\n")
    out = asyncio.run(rd.dispatch("test_runner", {"data": code}))
    assert out["tool_output"]["stdout"].strip() == "DENIED"


def test_sandbox_cpu_limit_kills_busy_loop():
    r = safety.run_sandboxed_python("while True:\n    pass\n", limits={"cpu_seconds": 1})
    assert r["returncode"] < 0 and r["timed_out"] is False
    assert r["elapsed_s"] < safety.SANDBOX_WALL_TIMEOUT_S


def test_sandbox_wall_timeout_kills_sleeper():
    r = safety.run_sandboxed_python("import time\ntime.sleep(30)\n", timeout_s=1)
    assert r["timed_out"] is True and r["elapsed_s"] < 5


def test_sandbox_memory_limit():
    r = safety.run_sandboxed_python("x = bytearray(2 * 1024 ** 3)\n")
    assert r["returncode"] != 0 and "MemoryError" in r["stderr"]


@pytest.mark.code_exec
def test_sandbox_reports_signal_kill_honestly(monkeypatch):
    monkeypatch.setitem(safety.SANDBOX_LIMITS, "cpu_seconds", 1)
    out = asyncio.run(rd.dispatch("test_runner", {"data": "while True:\n    pass\n"}))
    assert out["ok"] is False and "killed by signal" in out["answer"]


# ---------------------------------------------------------------- 3. outbound URLs

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1",
                                "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1",
                                "fd00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1",
                                # IPv6 forms that embed a private IPv4 (NAT64 / 6to4)
                                "64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1", "2002:7f00:1::1"])
def test_non_public_addresses_are_blocked(ip):
    assert safety.is_public_address(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "192.0.2.10",
                                "64:ff9b::808:808"])
def test_public_and_documentation_addresses_are_allowed(ip):
    assert safety.is_public_address(ip) is True


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x", "gopher://x"])
def test_non_http_schemes_are_blocked(url):
    with pytest.raises(safety.UnsafeURLError, match="scheme"):
        safety.check_url(url)


def test_api_integrator_cannot_reach_internal_server(internal_server):
    url, handler = internal_server
    out = asyncio.run(rd.dispatch("api_integrator", {"prompt": f"GET {url}"}))
    assert out["ok"] is False and out["error"].startswith("blocked:")
    out = asyncio.run(rd.dispatch("network_client", {"prompt": f"HEAD {url}"}))
    assert out["ok"] is False and out["error"].startswith("blocked:")
    assert handler.hits == 0                                     # no request left the process


def test_private_targets_allowed_only_with_explicit_flag(internal_server, monkeypatch):
    url, handler = internal_server
    monkeypatch.setenv("AGOS_ALLOW_PRIVATE_NETWORK", "1")
    out = asyncio.run(rd.dispatch("api_integrator", {"prompt": f"GET {url}"}))
    assert out["ok"] is True and out["tool_output"]["sample"] == {"internal": True}
    assert handler.hits == 1


def test_redirect_to_private_address_is_blocked():
    import urllib.request
    h = safety._CheckedRedirectHandler(allow_private=False)
    req = urllib.request.Request("https://public.example.org/start")
    with pytest.raises(safety.UnsafeURLError):
        h.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/")
    ok = h.redirect_request(req, None, 302, "Found", {}, "https://other.example.org/next")
    assert ok.full_url == "https://other.example.org/next"


def test_public_url_still_works_on_offline_transport(_offline_external_network):
    out = asyncio.run(rd.dispatch("api_integrator", {"prompt": "GET https://api.example.org/v1/items"}))
    assert out["ok"] is True and out["tool_output"]["sample"]["path"] == "/v1/items"


def test_api_registered_external_agent_is_checked_on_every_call(internal_server):
    url, handler = internal_server
    blocked = ExternalAgentAdapter("r100-ext", url, allow_private=False)
    out = asyncio.run(blocked.execute({"prompt": "hi"}))
    assert out["ok"] is False and "blocked non-public address" in out["result"]["error"]
    assert handler.hits == 0


def test_open_mode_registration_of_loopback_agent_is_rejected(open_mode_server, monkeypatch):
    # SEC-02: external agents are shared by all tenants, so registration needs
    # the admin role — an anonymous client is refused before any URL handling,
    # even with AGOS_ALLOW_PRIVATE_NETWORK=1 (stricter than the R-100 400).
    with TestClient(open_mode_server.app) as c:
        r = c.post("/api/agents/external", json={"name": "r100-loop", "url": "http://127.0.0.1:9/v1"})
        assert r.status_code == 401
        monkeypatch.setenv("AGOS_ALLOW_PRIVATE_NETWORK", "1")
        r = c.post("/api/agents/external", json={"name": "r100-loop", "url": "http://127.0.0.1:9/v1"})
        assert r.status_code == 401
        assert "r100-loop" not in open_mode_server.EXTERNAL_AGENTS


# ---------------------------------------------------------------- 4. SQLite

def test_sqlite_attach_and_vacuum_into_are_denied(tmp_path):
    target = tmp_path / "escape.db"
    for stmts in (["SELECT 1", f"ATTACH DATABASE '{target}' AS x"],
                  ["CREATE TABLE t(a)", f"VACUUM INTO '{target}'"]):
        with pytest.raises(Exception, match="not authorized|authorization denied"):
            safety.run_sqlite_statements(stmts)
    assert not target.exists()


def test_sqlite_runaway_query_is_interrupted():
    t0 = time.monotonic()
    with pytest.raises(Exception, match="interrupted"):
        safety.run_sqlite_statements(
            ["WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"],
            time_budget_s=0.5)
    assert time.monotonic() - t0 < 3


def test_db_manager_still_runs_real_sql():
    out = asyncio.run(rd.dispatch("db_manager", {
        "prompt": "CREATE TABLE t(x INTEGER); INSERT INTO t VALUES (1),(2),(3); SELECT sum(x) AS s FROM t"}))
    assert out["ok"] is True and out["tool_output"] == {"columns": ["s"], "rows": [(6,)]}


def test_sqlite_huge_value_cannot_be_allocated_in_the_server():
    # Without a length limit SQLite would allocate ~1 GB inside the server process.
    with pytest.raises(Exception, match="too big"):
        safety.run_sqlite_statements(["SELECT length(randomblob(1000000000))"])


def test_sandbox_tolerates_invalid_utf8_output():
    r = safety.run_sandboxed_python("import sys; sys.stdout.buffer.write(b'ok\\xff\\xfe'); sys.stdout.flush()")
    assert r["returncode"] == 0 and r["stdout"].startswith("ok")


def test_sandbox_timeout_does_not_hang_on_escaped_descendant():
    # A grandchild in its own session keeps the pipes open after the group is killed.
    code = ("import subprocess, time\n"
            "subprocess.Popen(['sleep', '6'], start_new_session=True)\n"
            "time.sleep(30)\n")
    t0 = time.monotonic()
    r = safety.run_sandboxed_python(code, timeout_s=1)
    assert r["timed_out"] is True
    assert time.monotonic() - t0 < 5

