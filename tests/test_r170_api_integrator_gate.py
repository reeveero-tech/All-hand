#!/usr/bin/env python3
"""R-170 (batch 10): api_integrator under the completion gate, and the
payload-vs-self-report boundary of the tool-output contradiction check.

Offline tests replace ``safety.safe_urlopen`` (the SSRF policy blocks
localhost, so a local server cannot be used); the live test is ``network``.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent_civilization.core import safety
from agent_civilization.core.output_validators import (
    DEFAULT_REQUIREMENTS, requirements_for, tool_output_failure, verify_success)

GITLAB_LIKE = [{"id": 1, "status": "failed", "ref": "main"},
               {"id": 2, "status": "success", "ref": "main"}]
CI_LIKE = {"total_count": 2, "workflow_runs": [{"status": "completed", "conclusion": "failure"}]}


class _Resp:
    def __init__(self, body, ct="application/json", status=200):
        self._b = body.encode() if isinstance(body, str) else body
        self.headers = {"content-type": ct}
        self.status = status

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body, ct="application/json", status=200, exc=None):
    def fake(req, timeout=None):
        if exc:
            raise exc
        return _Resp(body, ct, status)
    monkeypatch.setattr(safety, "safe_urlopen", fake)


def _agent():
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl="real")["api_integrator"][0]


def _run(work):
    return asyncio.run(_agent().execute(work))


def test_default_requirement_is_tool_output():
    assert DEFAULT_REQUIREMENTS["api_integrator"] == {"tool_output": True}
    assert requirements_for("api_integrator", {"prompt": "GET x"}) == {"tool_output": True}


@pytest.mark.parametrize("payload", [GITLAB_LIKE, CI_LIKE, {"success": False, "errors": ["quota"]},
                                     {"failed": 3, "total": 10, "passed": 7}])
def test_api_data_with_failure_words_is_not_a_false_rejection(monkeypatch, payload):
    """RED before batch 10 for GITLAB_LIKE / the counters: the contradiction
    walker descended into ``tool_output.sample`` (the API's own data) and read
    ``status: "failed"`` / ``failed: 3`` as a failure of the call itself."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    _serve(monkeypatch, json.dumps(payload))
    out = _run({"prompt": "GET https://api.example.org/pipelines"})
    assert out["ok"] is True, out["result"].get("verification")
    assert "tool_output" in out["result"]["verification"]["checks_run"]


def test_api_http_error_is_a_failure_not_a_success(monkeypatch):
    import urllib.error
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    _serve(monkeypatch, "", exc=urllib.error.HTTPError("u", 503, "down", {}, None))
    out = _run({"prompt": "GET https://api.example.org/x"})
    assert out["ok"] is False


def test_api_blocked_by_ssrf_policy_fails(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    out = _run({"prompt": "GET http://127.0.0.1:8080/admin"})
    assert out["ok"] is False


def test_api_stripped_success_is_rejected_by_default(monkeypatch):
    """F2b: the real answer kept, the evidence removed ⇒ rejected (was accepted
    as unverified: api_integrator had no default requirement)."""
    forged = {"ok": True, "answer": "GET https://api.example.org/x -> HTTP 200 (application/json, 12 bytes, 40 ms)"}
    v = verify_success({"ok": True, "result": forged}, requirements_for("api_integrator", {}))
    assert not v.ok and "tool_output" in v.checks_run


def test_api_text_body_success_passes(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    _serve(monkeypatch, "plain text body", ct="text/plain")
    out = _run({"prompt": "GET https://api.example.org/robots.txt"})
    assert out["ok"] is True and out["result"]["tool_output"]["sample"] == "plain text body"


# --------------------------------------------- boundary of the contradiction check
def test_self_report_failure_still_detected_outside_payload():
    assert tool_output_failure({"checks": {"failed": 2, "passed": 5}})
    assert tool_output_failure({"summary": "ALL PASSED", "status": "failed"})
    assert tool_output_failure({"exit_code": 1, "sample": {"ok": True}})


@pytest.mark.parametrize("key", sorted(["sample", "data", "payload", "body", "response", "content",
                                        "raw", "records", "rows", "items", "documents", "json"]))
def test_payload_containers_are_not_read_as_self_report(key):
    assert tool_output_failure({key: {"status": "failed", "failed": 4}}) is None
    assert tool_output_failure({key: [{"status": "error"}]}) is None


def test_gate_off_api_unchanged(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    _serve(monkeypatch, json.dumps(GITLAB_LIKE))
    out = _run({"prompt": "GET https://api.example.org/pipelines"})
    assert out["ok"] is True and "verification" not in out["result"]


@pytest.mark.network
def test_live_public_api_with_failed_pipelines_passes_gate(monkeypatch):
    """Live: GitLab's public pipeline list (contains ``status: failed`` items)."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    out = _run({"prompt": "GET https://gitlab.com/api/v4/projects/278964/pipelines?status=failed&per_page=3"})
    if not out["result"].get("tool_output"):
        pytest.skip(f"service unavailable: {out['result'].get('error')}")
    sample = out["result"]["tool_output"]["sample"]
    assert isinstance(sample, list) and any(p.get("status") == "failed" for p in sample)  # the hazard is present
    assert out["ok"] is True, out["result"].get("verification")
