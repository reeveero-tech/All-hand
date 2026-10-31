"""G-160-1 remainder (R-170 batch 24, D-110): local filesystem paths and the
hosts of configured internal services must not reach a tenant-visible failure
result; user-supplied hosts/URLs and the diagnostic around them stay.

Batch 20 (D-105) declared as a limit: "internal host names and paths are NOT
treated as secrets". A failed handler that embeds ``str(e)`` can carry
``/home/runner/work/All-hand/agent_civilization/...`` (a traceback fragment,
``FileNotFoundError``) or the Supabase/Redis/Postgres host taken from the
environment (``Cannot connect to host db.internal-xyz.supabase.co:5432``).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from agent_civilization.core import public_text

REPO = str(Path(public_text.__file__).resolve().parents[2])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for k in ("SUPABASE_URL", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(k, raising=False)
    public_text.reset_cache()
    yield
    public_text.reset_cache()


def test_repo_root_path_is_redacted_keeping_relative_tail():
    s = f'File "{REPO}/agent_civilization/core/tools.py", line 10'
    out = public_text.redact(s)
    assert REPO not in out
    assert "<path>/agent_civilization/core/tools.py" in out
    assert "line 10" in out


@pytest.mark.parametrize("prefix", [sys.prefix, tempfile.gettempdir(), os.path.expanduser("~")])
def test_runtime_prefixes_are_redacted(prefix):
    if len(prefix) < 4 or prefix == "/":
        pytest.skip("prefix too short to redact safely")
    out = public_text.redact(f"[Errno 2] No such file or directory: '{prefix}/x/y.json'")
    assert prefix + "/x" not in out
    assert "<path>/x/y.json" in out
    assert "No such file or directory" in out


def test_internal_service_hosts_are_redacted(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abcd1234wxyz.supabase.co")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Pw-db-9f8e7d6c5b@db.internal-q7.example.net:5432/app")
    monkeypatch.setenv("REDIS_URL", "redis://cache-7.svc.cluster.local:6379/0")
    public_text.reset_cache()
    s = ("Cannot connect to host abcd1234wxyz.supabase.co:443 ssl:default; "
         "pg: db.internal-q7.example.net:5432 refused; redis cache-7.svc.cluster.local timeout")
    out = public_text.redact(s)
    for h in ("abcd1234wxyz.supabase.co", "db.internal-q7.example.net", "cache-7.svc.cluster.local"):
        assert h not in out
    assert out.count("<internal-host>") == 3
    assert "Cannot connect to host" in out and ":5432 refused" in out


def test_user_supplied_hosts_and_urls_are_kept(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abcd1234wxyz.supabase.co")
    public_text.reset_cache()
    s = ("http: 404 for https://example.com/tmp/report.csv and "
         "https://other.supabase.co/x (user url); see docs.python.org")
    assert public_text.redact(s) == s


def test_host_match_is_whole_label_only(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://cache.local:6379")
    public_text.reset_cache()
    s = "mycache.local and cache.local.evil.com and cache.locally"
    assert public_text.redact(s) == s


def test_url_path_segments_are_not_treated_as_local_paths():
    tmp = tempfile.gettempdir()
    s = f"GET https://example.com{tmp}/a failed"
    assert public_text.redact(s) == s


def test_failure_result_walk_redacts_paths_and_hosts(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://cache-7.svc.cluster.local:6379/0")
    public_text.reset_cache()
    r = {"ok": False, "error": f"{REPO}/data/x: cache-7.svc.cluster.local down", "n": 3}
    out = public_text.public_failure_result(r)
    assert out == {"ok": False, "error": "<path>/data/x: <internal-host> down", "n": 3}
    assert r["error"].startswith(REPO)  # input not mutated


def test_credentials_still_redacted_alongside(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Pw-db-9f8e7d6c5b@db.internal-q7.example.net:5432/app")
    public_text.reset_cache()
    s = "postgresql://u:Pw-db-9f8e7d6c5b@db.internal-q7.example.net:5432/app failed"
    out = public_text.redact(s)
    assert "Pw-db-9f8e7d6c5b" not in out and "db.internal-q7.example.net" not in out
    assert "<redacted>@<internal-host>:5432/app" in out
