"""R-107: researcher retry — one transient network error must not fail the task.

Before R-107, `_researcher` had a single live call to api.duckduckgo.com and no
retry: 4 transient SSL/connection failures were recorded in a single session
(`raw/32` SSL handshake timeout, `raw/33`/`raw/34`/`raw/48` SSL UNEXPECTED_EOF,
and CI job `network` on PR #109 — Errno 104 Connection reset, `raw/59`). After
R-107: one transient failure is retried once; hard errors (policy refusal,
HTTP 4xx/5xx, empty query) are not retried; total budget is bounded.
"""
import asyncio
import json
import urllib.error

from agent_civilization.core import safety
from agent_civilization.core.agents import real_dispatcher as rd


class _FakeResp:
    def read(self):
        return json.dumps({
            "AbstractText": "AGOS is a real agent platform.",
            "AbstractURL": "https://example.com/agos",
            "RelatedTopics": [],
        }).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_transient_error_is_retried_once_and_then_succeeds(monkeypatch):
    """Discriminating: pre-R-107 this returns ok: False; post-R-107 it succeeds on retry."""
    calls = {"n": 0}

    def flaky_urlopen(req, timeout=8, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(
                ConnectionResetError(104, "Connection reset by peer"))
        return _FakeResp()

    monkeypatch.setattr(safety, "safe_urlopen", flaky_urlopen)

    async def go():
        return await rd._researcher({"prompt": "AGOS agent platform"})
    out = asyncio.run(go())
    assert out["ok"] is True
    assert calls["n"] == 2  # first attempt failed transiently, retry succeeded
    assert out["tool_output"]["retried"] is True
    assert out["tool_output"]["attempts"] == 2


def test_ssl_unexpected_eof_is_treated_as_transient(monkeypatch):
    """Discriminating: the exact error seen in raw/33/34/48 must be retried."""
    import ssl
    calls = {"n": 0}

    def flaky_urlopen(req, timeout=8, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(
                ssl.SSLError(8, "UNEXPECTED_EOF_WHILE_READING"))
        return _FakeResp()

    monkeypatch.setattr(safety, "safe_urlopen", flaky_urlopen)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is True and calls["n"] == 2


def test_hard_error_is_not_retried(monkeypatch):
    """Policy refusal (a non-network error) must not be retried — the address guard stays intact."""
    calls = {"n": 0}

    def refused(req, timeout=8, **kw):
        calls["n"] += 1
        raise PermissionError("blocked by address policy")

    monkeypatch.setattr(safety, "safe_urlopen", refused)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert calls["n"] == 1  # single attempt — no retry for hard errors


def test_persistent_transient_failure_reports_both_attempts(monkeypatch):
    """Two transient failures in a row must fail honestly with the attempts count."""
    def always_fail(req, timeout=8, **kw):
        raise urllib.error.URLError(
            ConnectionResetError(104, "Connection reset by peer"))
    monkeypatch.setattr(safety, "safe_urlopen", always_fail)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert "transient" in out["error"] and "2 attempts" in out["error"]


def test_empty_query_is_not_retried():
    """Empty query stays a hard error (no retry, no network call)."""
    async def go():
        return await rd._researcher({"prompt": ""})
    out = asyncio.run(go())
    assert out["ok"] is False and out["error"] == "empty query"


def test_is_transient_classifier_covers_the_seen_flakes():
    """Classifier: the exact failures seen in the wild are transient; HTTP 404 is not."""
    import ssl
    assert rd._is_transient_search_error(
        urllib.error.URLError(ConnectionResetError(104, "reset"))) is True
    assert rd._is_transient_search_error(
        urllib.error.URLError(ssl.SSLError(8, "UNEXPECTED_EOF"))) is True
    assert rd._is_transient_search_error(
        urllib.error.URLError(TimeoutError("handshake timeout"))) is True
    assert rd._is_transient_search_error(urllib.error.HTTPError("u", 404, "NF", {}, None)) is False
    assert rd._is_transient_search_error(PermissionError("policy")) is False
    assert rd._is_transient_search_error(ValueError("bad json")) is False


# -------------------------------------------------- R-107b fixes (certificate + budget)
def test_certificate_verification_error_is_not_retried(monkeypatch):
    """R-107b: a TLS certificate problem is a hard error — retrying wastes the budget."""
    import ssl
    calls = {"n": 0}

    def cert_fail(req, timeout=8, **kw):
        calls["n"] += 1
        raise urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED"))

    monkeypatch.setattr(safety, "safe_urlopen", cert_fail)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert calls["n"] == 1  # single attempt — certificate errors are hard


def test_http_error_is_explicitly_not_retried(monkeypatch):
    """R-107b: HTTP status errors (4xx/5xx) are hard — explicit, not incidental."""
    calls = {"n": 0}

    def http_404(req, timeout=8, **kw):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(safety, "safe_urlopen", http_404)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert calls["n"] == 1
    assert "404" in out["error"]


def test_total_budget_is_enforced_across_attempts(monkeypatch):
    """R-107b: a slow first attempt that eats the budget must prevent a second one,
    and the error must report the attempts that actually ran (not a fixed 2)."""
    import time as _time
    calls = {"n": 0}

    def slow_fail(req, timeout=8, **kw):
        calls["n"] += 1
        _time.sleep(0.05)
        raise urllib.error.URLError(
            ConnectionResetError(104, "Connection reset by peer"))

    monkeypatch.setattr(safety, "safe_urlopen", slow_fail)
    # Shrink the budget so the first (slow) attempt fits but the second cannot:
    # remaining > 0.5 must hold before attempt 1 (budget 0.52) and remaining <= 0.5
    # after attempt 1 consumed 0.05s (0.52 - 0.05 = 0.47 <= 0.5).
    monkeypatch.setattr(rd, "_RESEARCHER_TOTAL_TIMEOUT_S", 0.52)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert calls["n"] == 1          # second attempt prevented by the budget
    assert "1 attempts" in out["error"]  # the real count, not the fixed 2


def test_attempts_count_reflects_actual_runs(monkeypatch):
    """R-107b: persistent transient failure reports the attempts that actually ran."""
    calls = {"n": 0}

    def always_fail(req, timeout=8, **kw):
        calls["n"] += 1
        raise urllib.error.URLError(
            ConnectionResetError(104, "Connection reset by peer"))

    monkeypatch.setattr(safety, "safe_urlopen", always_fail)

    async def go():
        return await rd._researcher({"prompt": "test"})
    out = asyncio.run(go())
    assert out["ok"] is False
    assert calls["n"] == 2 and "2 attempts" in out["error"]
