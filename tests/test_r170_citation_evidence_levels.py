#!/usr/bin/env python3
"""R-170 (batch 10): citation evidence levels — format ≠ existence ≠ quote presence.

Offline tests inject a fetcher (the SSRF policy blocks localhost); live tests
are marked ``network``. None of these levels is semantic support.
"""
from __future__ import annotations

import socket

import pytest

from agent_civilization.core import citation_evidence as ce
from agent_civilization.core.output_validators import verify_success

PAGE = "<html><body><h1>Jeddah</h1><p>Jeddah is a port city on the Red Sea coast of Saudi Arabia.</p></body></html>"


def _fetcher(table):
    def f(url, timeout):
        v = table[url]
        if isinstance(v, Exception):
            raise v
        return v
    return f


def _res(sources):
    return {"ok": True, "answer": "Jeddah is a Red Sea port.", "tool_output": {"sources": sources}}


def _verify(result, opts, monkeypatch, table):
    monkeypatch.setattr(ce, "default_fetcher", _fetcher(table))
    return verify_success({"ok": True, "result": result}, {"citations": opts})


def test_format_only_does_not_fetch(monkeypatch):
    """Level 1 unchanged: ``citations: True`` never touches the network."""
    def boom(url, timeout):
        raise AssertionError("format check must not fetch")
    monkeypatch.setattr(ce, "default_fetcher", boom)
    fake = _res([{"url": "https://en.wikipedia.org/wiki/Invented_R170"}])
    assert verify_success({"ok": True, "result": fake}, {"citations": True}).ok   # format passes: by design


def test_existence_rejects_missing_source(monkeypatch):
    u = "https://en.wikipedia.org/wiki/Invented_R170"
    v = _verify(_res([{"url": u}]), {"exists": True}, monkeypatch, {u: (404, "")})
    assert not v.ok and any("HTTP 404" in r for r in v.reasons) and "citations.exists" in v.checks_run


def test_existence_accepts_existing_source(monkeypatch):
    u = "https://en.wikipedia.org/wiki/Jeddah"
    assert _verify(_res([{"url": u}]), {"exists": True}, monkeypatch, {u: (200, PAGE)}).ok


@pytest.mark.parametrize("outcome", [socket.timeout("t"), OSError("dns"), (403, ""), (429, ""), (503, "")])
def test_unverifiable_is_neither_pass_evidence_nor_rejection(monkeypatch, outcome):
    u = "https://example.org/x"
    v = _verify(_res([{"url": u}]), {"exists": True}, monkeypatch, {u: outcome})
    assert v.ok and "citations.unverifiable=1" in v.checks_run
    row = ce.check_sources(_res([{"url": u}]), fetcher=_fetcher({u: outcome}))["sources"][0]
    assert row["status"] == "unverifiable"


def test_quote_present_and_absent(monkeypatch):
    u = "https://en.wikipedia.org/wiki/Jeddah"
    good = _res([{"url": u, "snippet": "Jeddah is a port city on the Red Sea coast..."}])
    bad = _res([{"url": u, "snippet": "Jeddah is the capital city of Saudi Arabia and its largest"}])
    assert _verify(good, {"quote": True}, monkeypatch, {u: (200, PAGE)}).ok
    v = _verify(bad, {"quote": True}, monkeypatch, {u: (200, PAGE)})
    assert not v.ok and any("citations.quote" in r for r in v.reasons)


def test_quote_too_short_is_not_evidence():
    assert ce.quote_present(PAGE, "Jeddah") is False
    assert ce.quote_present(PAGE, "a port city on the Red Sea coast") is True


def test_quote_normalises_html_whitespace_case():
    assert ce.quote_present("<p>The   QUICK <b>brown</b>\nfox jumps</p>", "the quick brown fox jumps")


def test_existence_is_not_support():
    """An existing page with an unrelated quote passes level 2 but fails level 3;
    a page that contains the words passes level 3 — still not entailment."""
    u = "https://example.org/a"
    res = _res([{"url": u, "snippet": "the moon is made of green cheese entirely"}])
    page = "<p>Folklore says the moon is made of green cheese entirely; it is not.</p>"
    out = ce.check_sources(res, exists=True, quote=True, fetcher=_fetcher({u: (200, page)}))
    assert out["sources"][0]["quote"] == "present" and out["reasons"] == []   # words present ≠ claim true


def test_max_urls_bound():
    urls = [f"https://example.org/{i}" for i in range(8)]
    out = ce.check_sources(_res([{"url": u} for u in urls]), fetcher=_fetcher({u: (200, "") for u in urls}))
    assert [r["status"] for r in out["sources"]].count("not_checked") == 8 - ce.MAX_URLS


def test_default_fetcher_enforces_ssrf_policy():
    with pytest.raises(Exception):
        ce.default_fetcher("http://127.0.0.1:9/", 1.0)


@pytest.mark.network
def test_live_existing_and_invented_wikipedia_pages():
    real = ce.check_sources(_res([{"url": "https://en.wikipedia.org/wiki/Jeddah"}]))
    fake = ce.check_sources(_res([{"url": "https://en.wikipedia.org/wiki/R170_invented_source_0_abcdef"}]))
    if "unverifiable" in (real["sources"][0]["status"], fake["sources"][0]["status"]):
        pytest.skip("network unavailable for the evidence fetch")
    assert real["sources"][0]["status"] == "exists" and real["reasons"] == []
    assert fake["sources"][0]["status"] == "missing" and fake["reasons"]


@pytest.mark.network
def test_live_quote_present_on_example_com():
    res = _res([{"url": "https://example.com/", "snippet": "This domain is for use in documentation examples"}])
    out = ce.check_sources(res, quote=True)
    if out["sources"][0]["status"] == "unverifiable":
        pytest.skip("network unavailable")
    assert out["sources"][0]["status"] == "exists"
    assert out["sources"][0]["quote"] in ("present", "absent")    # page wording may change; recorded, not assumed
