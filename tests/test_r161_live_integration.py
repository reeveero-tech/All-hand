"""R-161: the opt-in integration runner (scripts/r161_live_integration.py) and
its manual workflow (.github/workflows/live-integration.yml).

Evidence class of THESE tests: mock — the services are fakes written here,
served on 127.0.0.1 (aiohttp), so the runner is invoked with ``simulated=True``
and its results are class ``mock``. The real-service run is the CI job
``storage`` (PostgreSQL + PostgREST on loopback: local_integration). No test
here connects to anything outside loopback: the offline guard of
tests/conftest.py raises on any such attempt, which several tests rely on to
prove that a refused live configuration made no connection.
"""
import asyncio
import datetime as dt
import json
import pathlib
import re
import socket
import subprocess
import sys

import pytest
from aiohttp import web

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import r161_live_integration as R  # noqa: E402

SECRET = "r161-SYNTHETIC-SECRET-" + "x" * 24          # never a real credential
HANG_S = 4          # a "hanging" fake answers after 4 s — the probes' deadline is 1 s
WORKFLOW = ROOT / ".github" / "workflows" / "live-integration.yml"


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ----------------------------------------------------------- fake services
class FakePostgREST:
    """Just enough PostgREST for supabase_store + the runner's cleanup."""

    def __init__(self, mode="ok"):
        self.mode, self.tables, self.seen, self.next_id = mode, {"tasks": [], "events": []}, [], 1

    @staticmethod
    def _match(row, q):
        for k, v in q.items():
            if k in ("order", "limit", "select"):
                continue
            if v.startswith("eq.") and str(row.get(k)) != v[3:]:
                return False
        return True

    async def handle(self, request):
        table = request.match_info["table"]
        q = dict(request.query)
        body = await request.read()
        self.seen.append((request.method, table, q, request.headers.get("apikey")))
        if self.mode == "reject":
            return web.json_response({"message": f"JWT invalid {SECRET}"}, status=401)   # echoes the key
        if self.mode == "hang":
            await asyncio.sleep(HANG_S)
        if request.method == "POST":
            row = json.loads(body)
            row.setdefault("created_at", "2026-10-17T00:00:00+00:00")
            if table == "events":
                row["id"] = self.next_id
                self.next_id += 1
            self.tables[table].append(row)
            return web.Response(status=201)
        if request.method == "PATCH":
            for r in self.tables[table]:
                if self._match(r, q):
                    r.update(json.loads(body))
            return web.Response(status=204)
        if request.method == "GET":
            if self.mode == "malformed" and table == "tasks" and "select" not in q:
                return web.json_response({"rows": "not a list"})
            rows = [r for r in self.tables[table] if self._match(r, q)]
            return web.json_response(rows[: int(q.get("limit", 1000))])
        if request.method == "DELETE":
            if self.mode == "delete_fails":
                return web.json_response({"message": "permission denied"}, status=500)
            gone = [r for r in self.tables[table] if self._match(r, q)]
            if self.mode != "delete_noop":
                self.tables[table] = [r for r in self.tables[table] if not self._match(r, q)]
            return web.json_response(gone, status=200)
        return web.Response(status=405)


class FakeCompletion:
    def __init__(self, mode="ok"):
        self.mode, self.calls, self.auth = mode, 0, []

    async def handle(self, request):
        await request.read()
        self.calls += 1
        self.auth.append(request.headers.get("Authorization", ""))
        if self.mode == "reject":
            return web.json_response({"error": {"message": f"invalid key {SECRET}"}}, status=401)
        if self.mode == "hang":
            await asyncio.sleep(HANG_S)
        if self.mode == "empty":
            return web.json_response({"choices": [{"message": {"content": None}}]})
        if self.mode == "malformed":
            return web.Response(text="<html>gateway</html>", content_type="text/html")
        if self.mode == "wrong_word":
            return web.json_response({"choices": [{"message": {"content": "hello"}}]})
        return web.json_response({"choices": [{"message": {"content": "PONG"}}], "model": "fake-model"})


async def _serve(handler, route):
    app = web.Application()
    app.router.add_route("*", route, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}"


async def _with_storage(mode, env_extra=None, simulated=True, timeout="5"):
    fake = FakePostgREST(mode)
    runner, base = await _serve(fake.handle, "/rest/v1/{table}")
    try:
        env = {"R161_STORAGE_URL": base, "R161_STORAGE_KEY": SECRET, "R161_TIMEOUT_S": timeout, **(env_extra or {})}
        rep = await R.run(["storage"], env, simulated=simulated)
    finally:
        await runner.cleanup()
    return rep, fake


async def _with_completion(target, mode, timeout="5"):
    fake = FakeCompletion(mode)
    runner, base = await _serve(fake.handle, "/v1/chat/completions")
    try:
        url = base + "/v1/chat/completions"
        env = ({"R161_LLM_URL": url, "R161_LLM_API_KEY": SECRET} if target == "llm"
               else {"R161_EXTERNAL_URL": url, "R161_EXTERNAL_API_KEY": SECRET})
        env["R161_TIMEOUT_S"] = timeout
        rep = await R.run([target], env, simulated=True)
    finally:
        await runner.cleanup()
    return rep, fake


def _r(rep, target):
    return next(r for r in rep["results"] if r["target"] == target)


# ======================================================= off by default

def test_nothing_requested_is_not_run_and_not_success():
    rep = asyncio.run(R.run([], {}))
    assert {r["outcome"] for r in rep["results"]} == {"NOT_RUN"}
    assert rep["exit_code"] == 3


def test_missing_configuration_is_blocked_by_variable_name_only():
    rep = asyncio.run(R.run(list(R.TARGETS), {"R161_LLM_URL": "https://x.example/v1"}))
    by = {r["target"]: r for r in rep["results"]}
    assert by["storage"]["outcome"] == "BLOCKED" and "R161_STORAGE_URL, R161_STORAGE_KEY" in by["storage"]["reason"]
    assert by["llm"]["outcome"] == "BLOCKED" and "R161_LLM_API_KEY" in by["llm"]["reason"]
    assert by["external"]["outcome"] == "BLOCKED"
    assert by["mcp"]["outcome"] == "BLOCKED" and "no MCP client" in by["mcp"]["reason"]
    assert rep["exit_code"] == 2 and all(not r["connected"] for r in rep["results"])


@pytest.mark.parametrize("env,why", [
    ({}, "R161_LIVE is not 1"),
    ({"R161_LIVE": "true"}, "R161_LIVE is not 1"),
    ({"R161_LIVE": "1"}, "not in R161_ALLOWED_HOSTS"),
    ({"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "other.example"}, "not in R161_ALLOWED_HOSTS"),
    ({"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "proj.supabase.co"}, "R161_CREDENTIAL_EXPIRES_AT not declared"),
    ({"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "proj.supabase.co", "R161_CREDENTIAL_EXPIRES_AT": "tomorrow"},
     "not an ISO-8601 time"),
    ({"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "proj.supabase.co", "R161_CREDENTIAL_EXPIRES_AT": "2020-01-01T00:00:00Z"},
     "in the past"),
    ({"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "proj.supabase.co", "R161_CREDENTIAL_EXPIRES_AT": "2099-01-01T00:00:00Z"},
     "more than 7 days ahead"),
])
def test_live_is_refused_unless_explicitly_enabled_allowlisted_and_temporary(env, why):
    """No connection is attempted (the offline guard would raise if one were)."""
    full = {"R161_STORAGE_URL": "https://proj.supabase.co", "R161_STORAGE_KEY": SECRET, **env}
    rep = asyncio.run(R.run(["storage"], full))
    r = _r(rep, "storage")
    assert r["outcome"] == "BLOCKED" and why in r["reason"], r
    assert r["connected"] is False and rep["exit_code"] == 2


@pytest.mark.parametrize("url,why", [
    ("http://proj.supabase.co", "must use https"),
    ("https://user:pw@proj.supabase.co", "credentials inside the endpoint URL"),
    ("ftp://proj.supabase.co", "http(s) URL"),
    ("http://10.0.0.5:3000", "private/reserved address"),
    ("http://192.168.1.10", "private/reserved address"),
    ("not a url", "http(s) URL"),
])
def test_endpoint_policy(url, why):
    env = {"R161_STORAGE_URL": url, "R161_STORAGE_KEY": SECRET, "R161_LIVE": "1",
           "R161_ALLOWED_HOSTS": "proj.supabase.co",
           "R161_CREDENTIAL_EXPIRES_AT": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()}
    r = _r(asyncio.run(R.run(["storage"], env)), "storage")
    assert r["outcome"] == "BLOCKED" and why in r["reason"], r
    assert "pw" not in json.dumps(r)


def _live_env():
    return {"R161_STORAGE_URL": "https://proj.supabase.co", "R161_STORAGE_KEY": SECRET, "R161_LIVE": "1",
            "R161_ALLOWED_HOSTS": "proj.supabase.co",
            "R161_CREDENTIAL_EXPIRES_AT": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()}


def test_fully_enabled_live_endpoint_must_resolve_publicly(monkeypatch):
    """All switches set: the R-146 DNS policy runs before any request; a
    non-public resolution is BLOCKED and nothing is sent."""
    from agent_civilization.core import safety

    def refuse(url, allow_private=None):
        raise safety.UnsafeURLError("resolves to a private address")
    monkeypatch.setattr(safety, "check_url", refuse)
    r = _r(asyncio.run(R.run(["storage"], _live_env())), "storage")
    assert r["outcome"] == "BLOCKED" and "network policy" in r["reason"] and r["connected"] is False, r


def test_live_endpoint_without_network_is_a_failure_never_a_pass():
    """Here the conftest's fake DNS is accepted as public, then the offline
    guard refuses the connection: the live target FAILs (class live, no
    connection) — a live run that could not connect is not success."""
    rep = asyncio.run(R.run(["storage"], _live_env()))
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["evidence_class"] == "live_integration" and r["connected"] is False, r
    assert rep["summary"]["live_integration"] == {"PASS": 0, "FAIL": 1, "BLOCKED": 0, "NOT_RUN": 0}
    assert rep["exit_code"] == 1


def test_preflight_live_ok_and_classes():
    now = dt.datetime(2026, 10, 17, tzinfo=dt.timezone.utc)
    env = {"R161_LIVE": "1", "R161_ALLOWED_HOSTS": "proj.supabase.co",
           "R161_CREDENTIAL_EXPIRES_AT": "2026-10-17T06:00:00+00:00"}
    pf = R.preflight("storage", "https://proj.supabase.co", env, False, now=now)
    assert pf["ok"] and pf["class"] == "live_integration"
    assert R.preflight("storage", "http://127.0.0.1:3000", {}, False)["class"] == "local_integration"
    assert R.preflight("storage", "http://localhost:3000", {}, True)["class"] == "mock"
    assert not R.preflight("storage", "https://proj.supabase.co", env, True, now=now)["ok"]   # simulated: loopback only


# =========================================================== storage probe

async def test_storage_probe_passes_and_cleans_up_only_its_rows():
    fake = FakePostgREST()
    fake.tables["tasks"].append({"task_id": "keepme000000", "tenant_id": "someone", "status": "completed"})
    runner, base = await _serve(fake.handle, "/rest/v1/{table}")
    try:
        rep = await R.run(["storage"], {"R161_STORAGE_URL": base, "R161_STORAGE_KEY": SECRET}, simulated=True)
    finally:
        await runner.cleanup()
    r = _r(rep, "storage")
    assert r["outcome"] == "PASS" and r["evidence_class"] == "mock" and r["connected"], r
    assert all(c["ok"] for c in r["checks"]) and len(r["checks"]) >= 9
    assert r["cleanup"]["ok"] and r["cleanup"]["deleted"] == {"events": 1, "tasks": 1}
    assert fake.tables["tasks"] == [{"task_id": "keepme000000", "tenant_id": "someone", "status": "completed"}]
    deletes = [q for m, t, q, _ in fake.seen if m == "DELETE"]
    assert deletes and all(set(q) == {"tenant_id"} and q["tenant_id"] == "eq." + r["synthetic"]["tenant"] for q in deletes)
    assert rep["exit_code"] == 0 and rep["summary"]["mock"]["PASS"] == 1
    assert rep["summary"]["live_integration"]["PASS"] == 0


@pytest.mark.parametrize("mode,why", [
    ("reject", "check failed: insert_task accepted"),
    ("malformed", "check failed: get_task returns the row"),
])
async def test_storage_provider_failures_are_failures(mode, why):
    rep, fake = await _with_storage(mode)
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["reason"].startswith(why), r
    assert rep["exit_code"] == 1


async def test_storage_timeout_is_a_failure_and_cleanup_still_runs():
    rep, fake = await _with_storage("hang", timeout="1")
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and "timeout after 1s" in r["reason"]
    assert r["cleanup"]["ran"] and r["cleanup"]["ok"] is False          # the hanging server -> visible
    assert "cleanup failed" in r["reason"] and rep["exit_code"] == 1


@pytest.mark.parametrize("mode,detail", [
    ("delete_fails", "DELETE events -> HTTP 500"),
    ("delete_noop", "still present after cleanup"),
])
async def test_cleanup_failure_turns_a_passing_probe_into_a_failure(mode, detail):
    rep, fake = await _with_storage(mode)
    r = _r(rep, "storage")
    assert all(c["ok"] for c in r["checks"])             # the probe itself passed
    assert r["outcome"] == "FAIL" and detail in r["cleanup"]["detail"] and "cleanup failed" in r["reason"]
    assert rep["exit_code"] == 1


async def test_connection_refused_is_a_failure():
    env = {"R161_STORAGE_URL": f"http://127.0.0.1:{_port()}", "R161_STORAGE_KEY": SECRET, "R161_TIMEOUT_S": "3"}
    r = _r(await R.run(["storage"], env, simulated=True), "storage")
    assert r["outcome"] == "FAIL" and r["connected"] is False


async def test_cancelled_run_cleans_up_and_is_reported_as_failure():
    fake = FakePostgREST()
    orig = fake.handle
    gate = asyncio.Event()

    async def slow(request):
        if request.method == "GET" and request.match_info["table"] == "events" and "select" not in request.query:
            gate.set()
            await asyncio.sleep(HANG_S)
        return await orig(request)
    runner, base = await _serve(slow, "/rest/v1/{table}")
    try:
        task = asyncio.create_task(R.run(["storage", "llm"], {"R161_STORAGE_URL": base, "R161_STORAGE_KEY": SECRET,
                                                               "R161_LLM_API_KEY": SECRET}, simulated=True))
        await asyncio.wait_for(gate.wait(), 10)
        task.cancel()
        rep = await asyncio.wait_for(task, 20)
    finally:
        await runner.cleanup()
    r = _r(rep, "storage")
    assert r["outcome"] == "FAIL" and r["reason"] == "cancelled" and r["cleanup"]["ok"], r
    assert fake.tables == {"tasks": [], "events": []}
    assert _r(rep, "llm")["outcome"] == "FAIL" and _r(rep, "llm")["reason"] == "run cancelled before this target"
    assert rep["exit_code"] == 1


# ================================================== LLM / external probes

async def test_llm_probe_pass_uses_the_keyed_client_only():
    rep, fake = await _with_completion("llm", "ok")
    r = _r(rep, "llm")
    assert r["outcome"] == "PASS" and r["evidence_class"] == "mock" and fake.calls == 1
    assert fake.auth == [f"Bearer {SECRET}"]


@pytest.mark.parametrize("mode,why", [
    ("reject", "provider answered HTTP 401"),
    ("empty", "provider: empty completion"),
    ("malformed", "provider request failed"),
    ("wrong_word", "answer is the expected word"),
])
async def test_llm_provider_failures_are_failures(mode, why):
    rep, fake = await _with_completion("llm", mode)
    r = _r(rep, "llm")
    assert r["outcome"] == "FAIL" and why in r["reason"], r
    assert SECRET not in json.dumps(rep)          # the 401 body echoed the key


async def test_llm_timeout_is_a_failure():
    rep, _ = await _with_completion("llm", "hang", timeout="1")
    assert "timeout after 1s" in _r(rep, "llm")["reason"]


async def test_external_probe_pass():
    rep, fake = await _with_completion("external", "ok")
    r = _r(rep, "external")
    assert r["outcome"] == "PASS" and r["evidence_class"] == "mock" and fake.calls == 1


@pytest.mark.parametrize("mode,why", [
    ("reject", "HTTP 401"), ("empty", "empty completion"), ("malformed", "JSONDecodeError"),
    ("wrong_word", "answer is the expected word"),
])
async def test_external_failures_are_failures(mode, why):
    rep, _ = await _with_completion("external", mode)
    r = _r(rep, "external")
    assert r["outcome"] == "FAIL" and why in r["reason"], r


async def test_a_pass_needs_a_response(monkeypatch):
    """Even a probe claiming PASS without having connected is downgraded."""
    async def fake_probe(env, pf, t):
        return R._result("external", "PASS", "all checks passed", pf["class"], checks=[{"name": "x", "ok": True}])
    monkeypatch.setitem(R.PROBES, "external", fake_probe)
    r = _r(await R.run(["external"], {"R161_EXTERNAL_URL": "http://127.0.0.1:9/v1"}, simulated=True), "external")
    assert r["outcome"] == "FAIL" and r["reason"] == "no response was received from the endpoint"


# ================================================================ secrets

def test_redactor():
    env = {"R161_STORAGE_KEY": SECRET, "R161_LLM_URL": "https://u:pw-1234@h.example/x"}
    red = R.Redactor(env)
    s = red.text(f"key={SECRET} url=postgresql://a:b@db:5432/x Bearer abcdefghijkl jwt eyJhbGciOi.eyJyb2xlIjoi.c2lnbmF0dXJl pw-1234")
    assert SECRET not in s and "a:b@" not in s and "abcdefghijkl" not in s and "eyJhbGciOi" not in s and "pw-1234" not in s


async def test_secrets_never_reach_report_stdout_or_logs_even_on_exceptions(tmp_path, monkeypatch, caplog):
    """CLI end-to-end: a probe raising with the secret in its message, a
    provider echoing the key in its error body, and client logs — the report
    file, stdout/stderr and every log record are free of the secret."""
    fake = FakeCompletion("reject")
    runner, base = await _serve(fake.handle, "/v1/chat/completions")
    try:
        rep_path = tmp_path / "r161.json"
        script = (f"import sys,runpy; sys.argv=['x','--targets','llm,external,storage','--simulated',"
                  f"'--report',{str(rep_path)!r}]\n"
                  "import r161_live_integration as R\n"
                  "import logging\n"
                  "async def boom(env,pf,t):\n"
                  "    logging.getLogger('x').warning('client said %s', 'Bearer ' + env['R161_STORAGE_KEY'])\n"
                  "    logging.getLogger('y').error('raw %s', env['R161_STORAGE_KEY'])\n"
                  "    raise RuntimeError('leak ' + env['R161_STORAGE_KEY'])\n"
                  "R.PROBES['storage']=boom\n"
                  "sys.exit(R.main(sys.argv[1:]))\n")
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{ROOT}:{ROOT / 'scripts'}",
               "R161_LLM_URL": base + "/v1/chat/completions", "R161_LLM_API_KEY": SECRET,
               "R161_EXTERNAL_URL": base + "/v1/chat/completions", "R161_EXTERNAL_API_KEY": SECRET,
               "R161_STORAGE_URL": "http://127.0.0.1:9", "R161_STORAGE_KEY": SECRET}
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, env=env, cwd=str(ROOT),
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 60)
        assert rep_path.exists(), (proc.returncode, err.decode()[-2000:])
    finally:
        await runner.cleanup()
    text = out.decode() + err.decode() + rep_path.read_text()
    assert proc.returncode == 1
    assert SECRET not in text and "x" * 24 not in text
    rep = json.loads(rep_path.read_text())
    st = _r(rep, "storage")
    assert st["outcome"] == "FAIL" and st["reason"] == "probe error: RuntimeError"
    assert "client said Bearer <redacted>" in err.decode() and "raw <redacted>" in err.decode()
    assert fake.calls == 2                         # llm + external both reached the fake


# =========================================================== report shape

def test_report_separates_evidence_classes_and_cli_writes_it(tmp_path):
    rep_path = tmp_path / "rep.json"
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "r161_live_integration.py"),
                        "--targets", "storage,mcp", "--report", str(rep_path)],
                       capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                       env={"PATH": "/usr/bin:/bin"})
    assert p.returncode == 2
    rep = json.loads(rep_path.read_text())
    assert rep["report"] == "r161_integration" and rep["version"] == 1
    assert set(rep["summary"]) == {"mock", "local_integration", "live_integration", "unclassified"}
    assert "only live_integration PASS rows are live evidence" in p.stdout
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "r161_live_integration.py"), "--targets", "nope"],
                          capture_output=True, text=True, timeout=60).returncode == 64


def test_exit_codes():
    mk = lambda o: R._result("storage", o, "")  # noqa: E731
    assert R.exit_code([mk("PASS"), mk("NOT_RUN")]) == 0
    assert R.exit_code([mk("PASS"), mk("FAIL")]) == 1
    assert R.exit_code([mk("PASS"), mk("BLOCKED")]) == 2
    assert R.exit_code([mk("NOT_RUN")]) == 3


# ============================================================== workflow

def _wf():
    return WORKFLOW.read_text()


def test_workflow_is_manual_only_and_least_privilege():
    wf = _wf()
    on = re.search(r"^on:\n((?:  .*\n)+)", wf, re.M).group(1)
    assert "workflow_dispatch" in on
    for trig in ("push", "pull_request", "pull_request_target", "schedule", "workflow_run"):
        assert not re.search(rf"^\s+{trig}:", on, re.M), trig
    assert re.search(r"^permissions:\n  contents: read\n", wf, re.M)
    assert "environment: r161-staging" in wf
    assert "secrets.GITHUB_TOKEN" not in wf and "pull_request_target" not in wf


def test_workflow_secrets_only_in_the_runner_step_and_artifact_is_checked():
    wf = _wf()
    steps = re.split(r"\n      - name: ", wf)
    with_secrets = [s.split("\n", 1)[0] for s in steps if "secrets." in s]
    assert with_secrets == ["Run the integration runner"], with_secrets
    assert "set -x" not in wf
    assert "Check the report for secret values" in wf
    i_check, i_upload = wf.index("Check the report for secret values"), wf.index("actions/upload-artifact")
    assert i_check < i_upload
    assert re.search(r"path: r161_report\.json\n", wf)


def test_ci_never_receives_live_secrets_and_storage_job_runs_the_runner_locally():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "R161_" not in re.sub(r"(?s)  storage:.*?\n  network:", "", ci)     # only the storage job mentions R161_
    stack = (ROOT / "scripts" / "r042" / "run_local_stack.sh").read_text()
    assert "scripts/r161_live_integration.py --targets storage" in stack
    assert "R161_LIVE" not in stack                                         # loopback => local_integration
