#!/usr/bin/env python3
"""R-169 b4 runner: seed derivation, request shape, guard, restore (offline)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import r169_run_b4 as b4  # noqa: E402


def test_seed_is_deterministic_and_distinct():
    assert b4.seed_for("b4", "c1", 0) == b4.seed_for("b4", "c1", 0)
    seeds = {b4.seed_for("b4", c, i) for c in ("c1", "c2") for i in (0, 1)}
    assert len(seeds) == 4 and all(0 <= s < b4.SEED_MODULUS for s in seeds)
    assert b4.seed_for("b4", "c1", 0) != b4.seed_for("b5", "c1", 0)


class _Resp:
    def __init__(self, cache):
        self.headers = {"X-Cache": cache} if cache else {}

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("cache", [None, "HIT"])
def test_seeded_post_adds_seed_and_records_cache(monkeypatch, cache):
    from agent_civilization.core import safety
    sent = []

    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return _Resp(cache)
    monkeypatch.setattr(safety, "safe_urlopen", fake_urlopen)
    b4.CALLS.clear()
    tok = b4._case.set("case-x")
    try:
        post = b4.make_seeded_post("b4")
        out = post("sys", "user", 50, 5.0)
        post("sys", "user", 50, 5.0)
    finally:
        b4._case.reset(tok)
    assert out["choices"][0]["message"]["content"] == "hi"
    assert sent[0]["seed"] == b4.seed_for("b4", "case-x", 0)
    assert sent[1]["seed"] == b4.seed_for("b4", "case-x", 1)        # retry = new seed
    assert sent[0]["messages"][1]["content"] == "user" and sent[0]["max_tokens"] == 50
    assert set(sent[0]) == {"model", "messages", "max_tokens", "seed"}  # the ONLY addition
    assert [c["x_cache"] for c in b4.CALLS] == [cache, cache]


@pytest.mark.parametrize("args,needle", [
    ((False, ["llm.py"], True, True), "drift"),
    ((True, [], True, True), "dirty"),
    ((False, [], False, True), "remote"),
    ((False, [], True, False), "pre-registration"),
])
def test_guard_refuses(args, needle):
    assert needle in b4.guard(*args)


def test_guard_passes_when_clean():
    assert b4.guard(False, [], True, True) is None


def test_prereg_committed_and_frozen_files_untouched():
    assert b4.PREREG.exists()
    v5 = b4.v5
    assert v5.check_freeze(v5.freeze_state(), json.loads(v5.FREEZE.read_text())) == []


def test_score_refuses_without_committed_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(b4, "LABELS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        b4.score("test_grounded-v2_nonexistent")
