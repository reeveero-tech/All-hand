"""SEC-06 (R-145): the public keyless LLM provider is opt-in and consent-gated.

Two independent gates, BOTH required before any text can leave the process
for the public provider:

1. Operator opt-in — ``AGOS_LLM_KEYLESS=1``/``true`` (default: disabled; an
   invalid value is a startup error, never an implicit enable).
2. Request consent — ``allow_public_llm=true`` on the request that carries the
   text, carried to the central ``LLMClient.complete`` gate via a ContextVar.

What these tests prove (no test touches the real internet — the transport is
a local fake whose calls are counted; a blocked request must never even build
the outbound request):

* fresh-process defaults: no key + no opt-in = provider ``none``; ``0``
  disables; ``1`` enables; invalid values fail startup; ``OPENROUTER_API_KEY``
  keeps its precedence and behaviour.
* without consent no keyless request is built or dispatched (call counter
  stays empty) — including direct ``get_llm().complete()`` calls, HTTP routes
  (``/api/tasks`` background execution, ``/api/chat``,
  ``/civilization/execute``) and background tasks.
* consent is per request: it never leaks to a later request, another context,
  or overrides the operator's opt-out; a valid API key is never treated as
  consent.
* nothing about the user's text, secrets or the consent value is logged.
"""
import asyncio
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.core import llm as L
from agent_civilization.core.llm import LLMClient

REPO = Path(__file__).resolve().parent.parent


def _fresh_env(**extra):
    """A scrubbed environment: nothing from the developer's shell (or the test
    process) can influence the result — the provider decision is reproducible."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "AGOS_ENV": "test",  # allow import without credentials (SEC-03)
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


def _run(code: str, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], env=_fresh_env(**extra), cwd=str(REPO),
        capture_output=True, text=True, timeout=120)


# ---------------------------------------------------------------------------
# 1-6: provider selection — fresh processes only, so an earlier import of
# llm.py or the developer's environment cannot affect the result.
# ---------------------------------------------------------------------------

def test_fresh_process_without_key_or_optin_is_none():
    r = _run("from agent_civilization.core.llm import LLMClient; print(LLMClient().provider)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "none"


def test_fresh_process_zero_disables_keyless():
    r = _run("from agent_civilization.core.llm import LLMClient; print(LLMClient().provider)",
             AGOS_LLM_KEYLESS="0")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "none"


def test_fresh_process_explicit_one_enables_keyless():
    r = _run("from agent_civilization.core.llm import LLMClient; print(LLMClient().provider)",
             AGOS_LLM_KEYLESS="1")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "keyless"


def test_fresh_process_true_enables_keyless():
    r = _run("from agent_civilization.core.llm import LLMClient; print(LLMClient().provider)",
             AGOS_LLM_KEYLESS="true")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "keyless"


@pytest.mark.parametrize("bad", ["on", "enabled", "yes please", "yes ", "2", "maybe", "opt-in"])
def test_invalid_values_never_implicitly_enable_and_fail_startup(bad):
    # importing llm (and therefore server) fails — never a silent enable
    r = _run("import agent_civilization.core.llm", AGOS_LLM_KEYLESS=bad)
    assert r.returncode != 0
    assert "AGOS_LLM_KEYLESS" in r.stderr
    # the server does not start either (SEC-03-style fail-fast, every env)
    r = _run("import server", AGOS_LLM_KEYLESS=bad)
    assert r.returncode != 0
    assert "AGOS_LLM_KEYLESS" in r.stderr


def test_production_default_is_safe_and_invalid_fails_startup():
    # AGOS_ENV unset = production (this project's rule); valid credentials so
    # the only variable under test is AGOS_LLM_KEYLESS.
    creds = {"AGOS_ENV": "", "AGOS_API_KEY": "x" * 24}
    r = _run("import server; print('STARTED')", **creds)
    assert r.returncode == 0, r.stderr
    assert "STARTED" in r.stdout  # no keyless opt-in: production starts, provider none
    r = _run("import server; print('STARTED')", AGOS_LLM_KEYLESS="1", **creds)
    assert r.returncode == 0, r.stderr
    assert "STARTED" in r.stdout  # explicit opt-in: starts, provider keyless (needs consent)
    r = _run("import server; print('STARTED')", AGOS_LLM_KEYLESS="on", **creds)
    assert r.returncode != 0
    assert "AGOS_LLM_KEYLESS" in r.stderr  # production fails closed on a broken setting


def test_openrouter_key_keeps_precedence():
    r = _run("from agent_civilization.core.llm import LLMClient; print(LLMClient().provider)",
             OPENROUTER_API_KEY="sk-or-test", AGOS_LLM_KEYLESS="1")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "openrouter"  # a valid key wins over the opt-in flag


def test_missing_key_is_not_consent_for_another_provider():
    # no key, opt-in disabled: provider none — never a fallback "elsewhere"
    r = _run("from agent_civilization.core.llm import LLMClient; c=LLMClient(); print(c.provider, c.available)")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "none False"


# ---------------------------------------------------------------------------
# 7-15: consent and transport blocking — local fake transport, call counter.
# ---------------------------------------------------------------------------

@pytest.fixture
def keyless_fake(monkeypatch):
    """Operator opt-in + a local fake transport. ``calls`` records every
    transport invocation: an empty list after a request is the proof that no
    outbound request was even built."""
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = []

    def fake_post(system, prompt, max_tokens, timeout):
        calls.append({"system": system, "prompt": prompt})
        # valid fenced Python: a code_generator request can genuinely succeed
        return {"choices": [{"message": {"content":
            f"```python\ndef solve_{len(calls)}():\n    return {len(calls)}\n```"}}],
                "model": "fake"}

    monkeypatch.setattr(L, "_keyless_post", fake_post)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(L, "KEYLESS_BUDGET_S", 5.0)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    monkeypatch.setattr(L, "_keyless_lock", None)
    monkeypatch.setattr(L, "_keyless_waiting", 0)
    return calls


def test_no_consent_blocks_transport_before_request_is_built(keyless_fake):
    client = LLMClient()
    assert client.provider == "keyless"  # operator opt-in is on...
    r = asyncio.run(client.complete("sys", "user text"))
    # ...but without request consent: structured refusal, no fabricated answer
    assert not r["ok"]
    assert r["error"] == L.PUBLIC_LLM_NO_CONSENT_ERROR
    assert "public LLM provider not used" in r["error"]
    assert r["provider"] == "keyless" and r["content"] == ""
    assert keyless_fake == []  # _keyless_post never invoked — nothing was built


def test_direct_get_llm_call_stays_closed(keyless_fake):
    # the central gate: calling the library directly, bypassing every route
    r = asyncio.run(L.get_llm().complete("sys", "text"))
    assert not r["ok"] and r["error"] == L.PUBLIC_LLM_NO_CONSENT_ERROR
    assert keyless_fake == []


def test_explicit_consent_reaches_fake_provider_only(keyless_fake):
    token = L.set_public_llm_consent(True)
    try:
        r = asyncio.run(LLMClient().complete("sys", "user text"))
        assert r["ok"], r
        assert r["content"].startswith("```python")
        assert r["model"] == "keyless:fake" and r["provider"] == "keyless"
    finally:
        L.reset_public_llm_consent(token)
    assert len(keyless_fake) == 1  # the fake transport — nothing else


def test_user_consent_does_not_override_operator_optout(keyless_fake, monkeypatch):
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "0")  # operator disabled it
    token = L.set_public_llm_consent(True)  # user consents anyway
    try:
        client = LLMClient()
        assert client.provider == "none"  # both gates required: opt-out wins
        r = asyncio.run(client.complete("sys", "user text"))
        assert not r["ok"]
        assert "OPENROUTER_API_KEY" in r["error"]  # plain provider=none failure
    finally:
        L.reset_public_llm_consent(token)
    assert keyless_fake == []


def test_consent_not_inferred_from_api_key(keyless_fake, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    token = L.set_public_llm_consent(False)  # explicit refusal
    try:
        client = LLMClient()
        assert client.provider == "openrouter"  # a valid key is not keyless consent
        assert L.public_llm_consent() is False
    finally:
        L.reset_public_llm_consent(token)
    assert keyless_fake == []


def test_invalid_consent_values_are_rejected_not_coerced():
    with pytest.raises(TypeError):
        L.set_public_llm_consent("true")  # a string is not consent
    with pytest.raises(TypeError):
        L.set_public_llm_consent(1)
    assert L.public_llm_consent() is False


def test_consent_does_not_leak_to_later_requests(keyless_fake):
    # a consenting request must not authorise the next one
    token = L.set_public_llm_consent(True)
    assert asyncio.run(LLMClient().complete("s", "first"))["ok"]
    after_first = len(keyless_fake)
    assert after_first >= 1
    L.reset_public_llm_consent(token)
    r = asyncio.run(LLMClient().complete("s", "second"))
    assert not r["ok"] and r["error"] == L.PUBLIC_LLM_NO_CONSENT_ERROR
    assert len(keyless_fake) == after_first  # "second" built nothing new


def test_consent_is_isolated_per_asyncio_task(keyless_fake):
    # background work inherits the consent of the request that started it —
    # and only that: a task started without consent stays closed even while
    # another task's consent is live.
    async def run():
        token = L.set_public_llm_consent(True)
        allowed = asyncio.create_task(_complete("consented"))
        L.reset_public_llm_consent(token)  # the handler's finally, before awaiting
        blocked = asyncio.create_task(_complete("unconsented"))
        return await allowed, await blocked

    async def _complete(tag):
        return await LLMClient().complete("s", tag)

    allowed, blocked = asyncio.run(run())
    assert allowed["ok"]
    assert not blocked["ok"] and blocked["error"] == L.PUBLIC_LLM_NO_CONSENT_ERROR


def test_nothing_about_text_or_consent_is_logged(keyless_fake, caplog):
    import logging
    token = L.set_public_llm_consent(True)
    try:
        with caplog.at_level(logging.DEBUG, logger="llm"):
            asyncio.run(LLMClient().complete("secret-system", "secret user text"))
    finally:
        L.reset_public_llm_consent(token)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret user text" not in joined  # no message content in logs
    assert "secret-system" not in joined
    assert "consent" not in joined.lower()  # the consent value itself is not logged


# ---------------------------------------------------------------------------
# 12-14: the live HTTP routes (background task execution included).
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tenant_auth):
    return TestClient(server.app, raise_server_exceptions=False, headers=tenant_auth.headers)


def _poll(client, task_id, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/tasks/{task_id}")
        if r.status_code == 200 and r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.05)
    raise AssertionError("task did not finish in time")


def test_tasks_route_without_consent_blocks_transport(client, keyless_fake):
    # a code-from-description task needs the LLM; no consent → refused with a
    # clear reason (not a fake success, not an unexplained failure)
    r = client.post("/api/tasks", json={
        "specialization": "code_generator", "prompt": "write a function that computes the average"})
    assert r.status_code == 202, r.text
    task = _poll(client, r.json()["task_id"])
    assert task["status"] == "failed"
    assert L.PUBLIC_LLM_NO_CONSENT_ERROR[:60] in str(task["result"])
    assert "allow_public_llm" in str(task["result"])
    assert keyless_fake == []  # the background execution built no request


def test_tasks_route_with_consent_carries_it_to_the_background_run(client, keyless_fake):
    r = client.post("/api/tasks", json={
        "specialization": "code_generator", "prompt": "write a function that computes the average",
        "allow_public_llm": True})
    assert r.status_code == 202, r.text
    task = _poll(client, r.json()["task_id"])
    assert task["status"] == "completed", task
    assert task["result"]["ok"]
    assert len(keyless_fake) >= 1  # the background task's LLM call reached the fake
    # the consent was never written into the task record or its result
    assert "allow_public_llm" not in str(task)


def test_consent_of_one_task_does_not_leak_to_the_next(client, keyless_fake):
    # consenting task first, then a non-consenting one: the second is refused
    r1 = client.post("/api/tasks", json={
        "specialization": "code_generator", "prompt": "write a function that computes the average",
        "allow_public_llm": True})
    r2 = client.post("/api/tasks", json={
        "specialization": "code_generator", "prompt": "write a function that computes the average"})
    t1 = _poll(client, r1.json()["task_id"])
    t2 = _poll(client, r2.json()["task_id"])
    assert t1["status"] == "completed" and t2["status"] == "failed"
    assert L.PUBLIC_LLM_NO_CONSENT_ERROR[:60] in str(t2["result"])
    assert sum(1 for c in keyless_fake if "average" in c["prompt"]) >= 1


def test_chat_route_without_consent_blocks_transport(client, keyless_fake):
    r = client.post("/api/chat", json={"message": "write a function that computes the average"})
    assert r.status_code == 200, r.text
    outcome = r.json()["outcome"]
    assert not outcome["ok"]
    assert L.PUBLIC_LLM_NO_CONSENT_ERROR[:60] in str(outcome)
    assert keyless_fake == []


def test_chat_route_with_consent_reaches_fake_provider(client, keyless_fake):
    r = client.post("/api/chat", json={
        "message": "write a function that computes the average", "allow_public_llm": True})
    assert r.status_code == 200, r.text
    outcome = r.json()["outcome"]
    assert outcome["ok"], outcome
    assert len(keyless_fake) >= 1
    assert "allow_public_llm" not in str(outcome)  # consent is not echoed back


def test_civilization_execute_without_consent_blocks_transport(client, keyless_fake):
    r = client.post("/civilization/execute", json={
        "message": "write a function that computes the average"})
    assert r.status_code == 200, r.text
    assert keyless_fake == []  # whatever the plan did, no public egress happened


def test_civilization_execute_with_consent_allows_the_pipeline(client, keyless_fake):
    r = client.post("/civilization/execute", json={
        "message": "write a function that computes the average", "allow_public_llm": True})
    assert r.status_code == 200, r.text
    assert len(keyless_fake) >= 1  # the pipeline's LLM stage reached the fake


# ---------------------------------------------------------------------------
# 10, 19: the request-level contract (strict bool, default refusal).
# ---------------------------------------------------------------------------

def test_non_boolean_consent_in_body_is_rejected(client, keyless_fake):
    for bad in (["true", 1, "yes", None]):
        r = client.post("/api/tasks", json={
            "specialization": "code_generator", "prompt": "x",
            "allow_public_llm": bad})
        assert r.status_code == 422, (bad, r.text)  # never coerced into consent
    assert keyless_fake == []


def test_system_status_discloses_provider_without_secrets(client, keyless_fake):
    r = client.get("/api/system/status")
    assert r.status_code == 200
    d = r.json()
    # the UI reads these to warn and ask for consent; they are non-secret
    assert d["llm_provider"] == "keyless"
    assert d["public_llm_enabled"] is True
    assert d["public_llm_requires_consent"] is True
    assert "OPENROUTER_API_KEY" not in str(d)  # no key material anywhere
    # provider none is disclosed as none (no false "public" claim)


# ---------------------------------------------------------------------------
# R-145 review round 2: independent no-egress guards, provider-path logging,
# concurrent isolation, the keyed provider's own path, self_developer retests.
# ---------------------------------------------------------------------------

def test_no_consent_never_reaches_the_network_layer(monkeypatch):
    # Independent of the fake-transport tests: the transport is NOT replaced
    # here. Guards sit at the real egress choke points (safety.safe_urlopen
    # and urllib request construction) and turn any outbound attempt into a
    # hard failure. With the provider opted in but no consent, the gate
    # returns before any of them is touched.
    from agent_civilization.core import safety
    import urllib.request

    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")  # operator opt-in: the gate is live
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def _no_egress(*a, **k):
        raise AssertionError("egress attempted without consent")

    monkeypatch.setattr(safety, "safe_urlopen", _no_egress)
    monkeypatch.setattr(urllib.request, "Request", _no_egress)

    async def go():
        return await L.get_llm().complete("sys", "prompt")
    r = asyncio.run(go())
    assert r["ok"] is False
    assert "did not consent" in r["error"]  # refusal, not an unguarded attempt


def test_concurrent_requests_carry_only_their_own_consent(keyless_fake):
    # Two requests in flight at once (same process, same tenant model): the
    # consenting one's text reaches the transport, the others' never do.
    async def req(consent, marker):
        token = L.set_public_llm_consent(consent)
        try:
            return await L.get_llm().complete("sys", f"prompt {marker}")
        finally:
            L.reset_public_llm_consent(token)

    async def go():
        return await asyncio.gather(req(True, "A"), req(False, "B"), req(False, "C"))
    asyncio.run(go())
    assert [c["prompt"] for c in keyless_fake] == ["prompt A"]


def test_openrouter_path_succeeds_without_public_consent(monkeypatch):
    # The keyed provider is the disclosed, operator-configured path: it carries
    # the request and answers without any keyless consent gate.
    import json as _json
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")  # operator enabled both; key wins
    sent = []

    class FakeResp:
        status = 200
        async def text(self):
            return _json.dumps({"choices": [{"message": {"content": "ANSWER-42"}}],
                                "model": "fake-or"})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def post(self, url, **kw):
            sent.append(kw)
            return FakeResp()

    async def fake_get_session(self):
        return FakeSession()

    monkeypatch.setattr(LLMClient, "_get_session", fake_get_session)
    c = LLMClient()
    assert c.provider == "openrouter"
    async def go():
        return await c.complete("sys", "prompt K")
    r = asyncio.run(go())
    assert r["ok"] is True and r["content"] == "ANSWER-42"
    assert len(sent) == 1
    msgs = sent[0]["json"]["messages"]
    assert msgs[-1]["content"] == "prompt K"  # the keyed provider got the text


def test_provider_error_and_exception_text_never_logged(monkeypatch, caplog):
    # The keyed provider's error bodies may echo the user's text; exceptions
    # can carry provider response text. Neither may reach the logs — the log
    # lines carry the HTTP status and the exception type only. The message is
    # still returned to the calling tenant (it is their data).
    import logging
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    caplog.set_level(logging.DEBUG)

    body_marker = "SECRET-PROMPT-ECHO"
    exc_marker = "SECRET-EXC-MESSAGE"

    class ErrResp:
        status = 500
        async def text(self):
            return body_marker
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    class ExcSession:
        def post(self, url, **kw):
            raise RuntimeError(exc_marker)

    class ErrSession:
        def post(self, url, **kw):
            return ErrResp()

    async def err_session(self):
        return ErrSession()

    async def exc_session(self):
        return ExcSession()

    monkeypatch.setattr(LLMClient, "_get_session", err_session)
    r = asyncio.run(LLMClient().complete("sys", "prompt E"))
    assert r["ok"] is False and "HTTP 500" in r["error"]
    assert body_marker in r["error"]  # returned to the caller (their data)
    assert body_marker not in caplog.text  # never logged

    monkeypatch.setattr(LLMClient, "_get_session", exc_session)
    r = asyncio.run(LLMClient().complete("sys", "prompt X"))
    assert r["ok"] is False and exc_marker in r["error"]
    assert exc_marker not in caplog.text  # type-only logging
    assert "LLM call failed: RuntimeError" in caplog.text


def test_invalid_keyless_value_is_not_echoed_in_the_error():
    # The broken value is operator input; the startup error names the variable
    # and the accepted values but does not echo the raw value into logs.
    r = _run("import agent_civilization.core.llm", AGOS_LLM_KEYLESS="SECRET-RAW-FLAG")
    assert r.returncode != 0
    assert "AGOS_LLM_KEYLESS" in r.stderr
    assert "SECRET-RAW-FLAG" not in r.stderr


def test_production_env_fully_unset_and_explicit_value_are_safe():
    # AGOS_ENV absent from the environment entirely (not merely empty) and the
    # literal value "production" are both the production case: valid
    # credentials, no keyless opt-in — provider none, zero transport without
    # consent.
    code = ("import server; from agent_civilization.core import llm as L;"
            "calls = [];"
            "L._keyless_post = lambda s, p, m, t: calls.append(1);"
            "import asyncio;"
            "r = asyncio.run(L.get_llm().complete('sys', 'prompt'));"
            "print(L.get_llm().provider, r['ok'], len(calls))")
    for agos_env in (None, "production"):
        env = _fresh_env(AGOS_API_KEY="x" * 24)
        env.pop("AGOS_ENV", None)
        if agos_env is not None:
            env["AGOS_ENV"] = agos_env
        r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(REPO),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "none False 0"


def test_status_fields_distinguish_operator_optin_from_selected_provider(client, monkeypatch):
    # With an OpenRouter key AND the operator opt-in, the keyed provider wins:
    # llm_provider=openrouter (selected), public_llm_enabled=True (operator
    # opt-in), public_llm_requires_consent=False (the public provider is not
    # the one serving requests).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    r = client.get("/api/system/status")
    assert r.status_code == 200
    d = r.json()
    assert d["llm_provider"] == "openrouter"
    assert d["public_llm_enabled"] is True
    assert d["public_llm_requires_consent"] is False


def test_civilization_execute_without_consent_reports_the_refusal(client, keyless_fake):
    # The plan is not silently "completed": the LLM-needing stage itself is
    # marked not-ok and carries the no-consent refusal, and the overall
    # result is a visible failure (not merely a missing "ok" field).
    r = client.post("/civilization/execute", json={
        "message": "write a function that computes the average"})
    assert r.status_code == 200, r.text
    assert keyless_fake == []  # no public egress happened
    body = r.json()
    # execute_plan returns {"stages", "final", ...} with no top-level "ok" —
    # the refusal must surface in the failing stage itself, not be hidden.
    assert body.get("ok") is not True
    stages = body.get("stages", [])
    bad = [st for st in stages if st.get("ok") is False]
    assert bad, stages  # a failing stage is reported, not hidden
    assert any("did not consent" in str(st.get("output") or st.get("error") or "")
               for st in bad)


def test_self_developer_retests_do_not_inherit_the_requests_consent(keyless_fake):
    # Remembered goals are texts from *past* requests. Even when the current
    # request consents, their re-execution runs without public-provider consent:
    # nothing is re-sent to the public provider and the retest is reported as
    # still failing with the no-consent reason — never a silent success.
    from agent_civilization.core import experience, tenancy
    from agent_civilization.core.agents import real_dispatcher as rd

    goal = "write a function that computes the average 1 2 3"

    async def fail_as(tenant):
        token = tenancy.set_tenant(tenant)
        try:
            async def call():
                return {"ok": False, "error": "boom"}
            await experience.run_with_experience("code_generator", "t",
                                                 {"prompt": goal, "goal": goal}, call)
        finally:
            tenancy.reset_tenant(token)

    asyncio.run(fail_as("tenant_a"))

    async def selfdev_with_consent():
        token = tenancy.set_tenant("tenant_a")
        consent = L.set_public_llm_consent(True)  # the request itself consents
        try:
            return await rd.dispatch("self_developer", {"prompt": "self improve"})
        finally:
            L.reset_public_llm_consent(consent)
            tenancy.reset_tenant(token)

    out = asyncio.run(selfdev_with_consent())
    assert keyless_fake == []  # the remembered goal was NOT re-sent
    retests = out["tool_output"]["retests"]
    assert len(retests) == 1
    assert retests[0]["fixed"] is False
    assert "did not consent" in retests[0]["reason"]


def _tenant_clients(monkeypatch, *tenants: str) -> dict:
    """One credentials map shared by all tenants (each client keeps its own
    identity — the SEC-02 auth model), with the portal kept alive for the
    whole test so slow background tasks are not cancelled at request end."""
    import hashlib
    import secrets

    from agent_civilization.core.tenancy import Principal

    creds, keys = {}, {}
    for tenant in tenants:
        keys[tenant] = f"k-{secrets.token_urlsafe(16)}"
        creds[hashlib.sha256(keys[tenant].encode()).hexdigest()] = Principal(tenant, "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    monkeypatch.setattr(server, "ENABLE_AUTH", True)

    class _Clients:
        def __init__(self):
            self._stack = None

        def __enter__(self):
            self._stack = ExitStack()
            return {t: self._stack.enter_context(TestClient(
                server.app, raise_server_exceptions=False,
                headers={"X-API-Key": keys[t]})) for t in tenants}

        def __exit__(self, *a):
            return self._stack.__exit__(*a)
    return _Clients()


def _counting_transport(monkeypatch):
    """Enable the keyless provider and replace the transport with a counter
    that also returns genuinely usable (valid Python) content."""
    calls = []

    def fake_post(system, prompt, max_tokens, timeout):
        calls.append(prompt)
        return {"choices": [{"message": {"content":
            "```python\ndef solve():\n    return 1\n```"}}], "model": "fake"}

    monkeypatch.setattr(L, "_keyless_post", fake_post)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(L, "KEYLESS_BUDGET_S", 5.0)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    monkeypatch.setattr(L, "_keyless_lock", None)
    monkeypatch.setattr(L, "_keyless_waiting", 0)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return calls


def test_consent_is_isolated_between_tenants_via_the_real_routes(monkeypatch):
    # Two tenants through the real authenticated routes: only the consenting
    # tenant's text ever reaches the transport; the other tenant's identical
    # need is refused; and a later non-consenting request from the first
    # tenant adds nothing. Consent follows the request, not the process.
    calls = _counting_transport(monkeypatch)

    with _tenant_clients(monkeypatch, "tenant_a", "tenant_b") as cs:
        ca, cb = cs["tenant_a"], cs["tenant_b"]
        ra = ca.post("/api/tasks", json={"specialization": "code_generator",
                                        "prompt": "goal of tenant a", "allow_public_llm": True})
        rb = cb.post("/api/tasks", json={"specialization": "code_generator",
                                         "prompt": "goal of tenant b"})
        assert ra.status_code == 202 and rb.status_code == 202

        out_a = _poll(ca, ra.json()["task_id"])
        out_b = _poll(cb, rb.json()["task_id"])
        assert out_a["status"] == "completed"
        assert out_b["status"] == "failed"
        assert "did not consent" in str(out_b["result"])
        assert calls == ["goal of tenant a"]  # only the consenting tenant's text left

        rc = ca.post("/api/tasks", json={"specialization": "code_generator",
                                         "prompt": "second goal of tenant a"})
        assert rc.status_code == 202
        out_c = _poll(ca, rc.json()["task_id"])
        assert out_c["status"] == "failed"  # consent did not stick to the tenant
        assert calls == ["goal of tenant a"]


def test_background_task_keeps_its_consent_after_the_request_returns(monkeypatch):
    # The transport is held on a barrier that only opens AFTER the POST
    # response has returned: the request context is gone by then, so the
    # background task must carry the consent on its own (the ContextVar copy
    # in the spawned task — not the request's lifetime).
    import threading

    gate = threading.Event()
    started = threading.Event()
    calls = []

    def slow_post(system, prompt, max_tokens, timeout):
        started.set()
        assert gate.wait(timeout=30), "barrier was never released"
        calls.append(prompt)
        return {"choices": [{"message": {"content":
            "```python\ndef solve():\n    return 1\n```"}}], "model": "fake"}

    monkeypatch.setattr(L, "_keyless_post", slow_post)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(L, "KEYLESS_BUDGET_S", 10.0)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    monkeypatch.setattr(L, "_keyless_lock", None)
    monkeypatch.setattr(L, "_keyless_waiting", 0)
    monkeypatch.setenv("AGOS_LLM_KEYLESS", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with _tenant_clients(monkeypatch, "tenant_a") as cs:
        ca = cs["tenant_a"]
        r = ca.post("/api/tasks", json={"specialization": "code_generator",
                                        "prompt": "delayed goal", "allow_public_llm": True})
        assert r.status_code == 202
        # the background task reached the transport, and the POST had already
        # returned — the transport is still blocked on the barrier
        assert started.wait(timeout=15)
        assert not gate.is_set()
        assert calls == []

        gate.set()  # release only now, with the request long gone
        out = _poll(ca, r.json()["task_id"], timeout_s=20)
        assert out["status"] == "completed"  # consent survived the request
        assert calls == ["delayed goal"]
