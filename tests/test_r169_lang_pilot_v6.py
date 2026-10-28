"""R-170 لاحق 72 — harness guard tests for the v6 language pilot (red-first).

PREREGISTRATION_V6.md §8: the tool must REFUSE (before any live call) to run
before the prereg is committed, to overwrite existing results, to run on a
split other than dev/holdout, to score an incomplete run, and to score data
whose ids are not the frozen v6 holdout. It also pins the v4 candidate rule 9
(symmetric instructions and BOTH examples — the v3 failure was Arabic-only)
and the frozen v6 holdout properties.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import r169_lang_pilot_v6 as pilot  # noqa: E402


# ---------------------------------------------------------------------------#
# the v4 candidate rule 9 (pinned — symmetric, both examples)
# ---------------------------------------------------------------------------#

def test_rule9_v4_is_symmetric_with_both_examples():
    assert "المصادر المتاحة لا تذكر هذه المعلومة" in pilot.RULE9_V4
    assert "The available sources do not mention this fact" in pilot.RULE9_V4
    assert pilot.RULE9_V4.count("use ONLY this example") == 2
    # symmetric instructions (the v3 failure mode was an Arabic-only prohibition)
    assert "If the question is Arabic, everything after the token MUST be Arabic" in pilot.RULE9_V4
    assert "If the question is English, everything after the token MUST be English" in pilot.RULE9_V4
    assert "use ONLY this example" in pilot.RULE9_V4
    assert "Never use the example of the other language" in pilot.RULE9_V4
    assert "Never write an English sentence in reply to an Arabic question." not in pilot.RULE9_V4


def test_v4_system_replaces_only_rule9():
    from agent_civilization.eval import r169_bench_v5 as b
    v2 = b.GROUNDED_V2_SYSTEM
    v4 = pilot.v4_system(v2)
    assert v4 != v2
    assert v4.count("9. ") == 1
    start = v2.index(pilot.RULE9_V2_PREFIX)
    end = v2.index("\n", start) + 1
    # everything outside the rule-9 line is identical
    assert v4[:start] == v2[:start]
    assert v4[start + len(pilot.RULE9_V4):] == v2[end:]


# ---------------------------------------------------------------------------#
# guards (no live call may happen when a guard fires)
# ---------------------------------------------------------------------------#

def test_run_refuses_when_prereg_not_committed(monkeypatch):
    monkeypatch.setattr(pilot, "_prereg_committed", lambda: False)
    with pytest.raises(SystemExit, match="PREREGISTRATION_V6.md is not committed"):
        asyncio.run(pilot.run("dev", ["grounded-v4-lang"]))


def test_run_refuses_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot, "_prereg_committed", lambda: True)
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    (tmp_path / "dev_grounded-v4-lang.jsonl").write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        asyncio.run(pilot.run("dev", ["grounded-v4-lang"]))


def test_run_refuses_unknown_split():
    with pytest.raises(SystemExit, match="not dev or holdout"):
        asyncio.run(pilot.run("test", ["grounded-v2"]))  # the test split is forbidden


def test_run_holdout_requires_all_three_arms(monkeypatch):
    monkeypatch.setattr(pilot, "_prereg_committed", lambda: True)
    with pytest.raises(SystemExit, match="ALL THREE arms"):
        asyncio.run(pilot.run("holdout", ["grounded-v2", "grounded-v4-lang"]))


def test_run_refuses_paid_key(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot, "_prereg_committed", lambda: True)
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
    with pytest.raises(SystemExit, match="paid key"):
        asyncio.run(pilot.run("dev", ["grounded-v2"]))


def test_bench_for_rejects_spent_validation_split():
    with pytest.raises(SystemExit, match="not dev or holdout"):
        pilot._bench_for("validation")  # spent for this candidate (log 296)


# ---------------------------------------------------------------------------#
# score guards (incomplete run / mismatched ids)
# ---------------------------------------------------------------------------#

def _write_run(out: Path, split: str, arm: str, n_cases: int, rows: list[dict]) -> None:
    p = out / f"{split}_{arm}.jsonl"
    p.write_text(json.dumps({"kind": "meta", "split": split, "config": arm, "cases": n_cases})
                 + "\n" + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


def test_score_refuses_incomplete_run(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    # declare 46 cases but write only 3 rows → incomplete
    _write_run(tmp_path, "holdout", "grounded-v2", 46,
               [{"id": "x", "reply": "", "error": None}] * 3)
    with pytest.raises(SystemExit, match="incomplete run"):
        pilot._scores("holdout", "grounded-v2")


def test_score_refuses_missing_holdout_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    with pytest.raises(FileNotFoundError):
        pilot._scores("holdout", "grounded-v4-lang")


def test_score_refuses_mismatched_ids(monkeypatch, tmp_path):
    """Result rows whose ids are not the v6 holdout must not be scored."""
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    from agent_civilization.eval import r169_bench_v6 as b6
    n = len(b6.load_split("holdout"))
    rows = [{"id": "r169-v5-grounded_qa-00-en", "reply": "", "error": None}] * n  # v5 ids
    _write_run(tmp_path, "holdout", "grounded-v2", n, rows)
    with pytest.raises(KeyError):
        pilot._scores("holdout", "grounded-v2")


# ---------------------------------------------------------------------------#
# the v6 holdout itself (frozen properties)
# ---------------------------------------------------------------------------#

def test_v6_holdout_composition():
    from agent_civilization.eval import r169_bench_v6 as b6
    cases = b6.load_split("holdout")
    assert len(cases) == 46
    langs = [c["lang"] for c in cases]
    assert langs.count("ar") == 23 and langs.count("en") == 23
    assert all(c["split"] == "holdout" and c["version"] == "r169-v6" for c in cases)
    fam = {}
    for c in cases:
        fam[c["task_type"]] = fam.get(c["task_type"], 0) + 1
    assert fam == {"grounded_qa": 6, "abstain_missing": 6, "false_premise": 4,
                  "conflicting_sources": 4, "stale_sources": 4, "prompt_injection": 4,
                  "adversarial_user": 4, "tool_poisoning": 6, "code_task": 6, "closed_book": 2}


def test_v6_holdout_vocabulary_disjoint_from_v5():
    """No v6 FICTIONAL vocabulary (towns/names/packages/companies) may appear
    anywhere in the v5 dataset files. Common English words (Windows, Linux, API…)
    are template words, not vocabulary."""
    from agent_civilization.eval import r169_bench_v6 as b6
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "r169_build_dataset_v6", ROOT / "scripts/r169_build_dataset_v6.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    v5_all = ""
    for split in ("dev", "validation", "test"):
        v5_all += (ROOT / f"benchmarks/r169/v5/{split}.jsonl").read_text(encoding="utf-8")
    vocab = ([t[0] for t in gen.TOWNS] + [n[0] for n in gen.NAMES] + list(gen.PACKAGES)
             + [c[0] for c in gen.COMPANIES] + list(gen.FORBIDDEN_NAMES))
    for w in vocab:
        assert w not in v5_all, f"v6 vocabulary {w!r} appears in the v5 dataset"
    # and the holdout actually uses this vocabulary (smoke: first case id exists)
    assert len(b6.load_split("holdout")) == 46


def test_v6_generator_is_deterministic():
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "scripts/r169_build_dataset_v6.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_v6_bench_reexports_frozen_v5_grader():
    from agent_civilization.eval import r169_bench_v5 as b5
    from agent_civilization.eval import r169_bench_v6 as b6
    assert b6.GROUNDED_V2_SYSTEM == b5.GROUNDED_V2_SYSTEM
    assert b6.grounded_v2_messages is b5.grounded_v2_messages
    assert b6.score_case is b5.score_case
    assert b6.run_code_tests is b5.run_code_tests
