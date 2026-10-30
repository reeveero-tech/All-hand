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
# the CRITICAL sent-prompt test (advisor review: the v4 arm must change the
# system prompt that r169_run_v5._one actually builds — via r169_bench_v5 —
# even when the cases were loaded from the v6 bench; swapping the case-loading
# bench has NO effect on the execution bench)
# ---------------------------------------------------------------------------#

def _capture_one(monkeypatch, bench, arm, case):
    import asyncio
    from agent_civilization.eval import r169_bench_v5 as b5
    prompts = []

    async def fake_one(c, config):
        prompts.append(b5.grounded_v2_messages(c)["system"])
        return {"id": c["id"], "reply": "", "error": None}

    monkeypatch.setattr(pilot.v5run, "_one", fake_one)
    asyncio.run(pilot._one_arm_case(bench, arm, case))
    return prompts[-1], b5.GROUNDED_V2_SYSTEM


def test_v4_arm_changes_the_actual_sent_prompt_on_holdout(monkeypatch):
    """The bug the advisor caught: on holdout the bench is v6, and swapping
    v6.GROUNDED_V2_SYSTEM does not affect the v5 module that _one reads — the
    v4 arm would silently send the ORIGINAL prompt. The fix swaps the
    execution bench (v5) directly."""
    from agent_civilization.eval import r169_bench_v7 as b7
    case = b7.load_split("holdout")[0]
    v2_prompt, _ = _capture_one(monkeypatch, b7, "grounded-v2", case)
    v4_prompt, restored = _capture_one(monkeypatch, b7, "grounded-v4-lang", case)
    assert v4_prompt != v2_prompt, "the v4 arm did not change the sent prompt (the execution-bench bug)"
    assert "use ONLY this example" in v4_prompt
    assert "use ONLY this example" not in v2_prompt
    # the original must be restored after the arm (no leakage to the next arm)
    assert "use ONLY this example" not in restored


def test_v4_arm_changes_the_actual_sent_prompt_on_dev(monkeypatch):
    from agent_civilization.eval import r169_bench_v5 as b5
    case = b5.load_split("dev")[0]
    v2_prompt, _ = _capture_one(monkeypatch, b5, "grounded-v2", case)
    v4_prompt, restored = _capture_one(monkeypatch, b5, "grounded-v4-lang", case)
    assert v4_prompt != v2_prompt
    assert "use ONLY this example" in v4_prompt
    assert "use ONLY this example" not in restored


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
    with pytest.raises(SystemExit, match="needs ALL of"):
        asyncio.run(pilot.run("holdout", ["grounded-v2", "grounded-v4-lang"]))
    # v8: نفس الحارس لحزمة المرشح الجديد (v2 + v4 ضبط + v4r)
    with pytest.raises(SystemExit, match="needs ALL of"):
        asyncio.run(pilot.run("holdout", ["grounded-v2", "grounded-v4-lang"],
                             dataset="v8"))


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
    """Result rows whose ids are not the v6 holdout must not be scored
    (46 DISTINCT foreign ids — no duplicates, so the id-set check fires)."""
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    from agent_civilization.eval import r169_bench_v7 as b7
    n = len(b7.load_split("holdout"))
    rows = [{"id": f"r169-v5-grounded_qa-{i:02d}-en", "reply": "", "error": None}
            for i in range(n)]  # distinct v5-style ids, none in the v6 holdout
    _write_run(tmp_path, "holdout", "grounded-v2", n, rows)
    with pytest.raises(SystemExit, match="ids do not match the frozen split"):
        pilot._scores("holdout", "grounded-v2")


def test_score_refuses_duplicate_ids(monkeypatch, tmp_path):
    """A file with 46 rows all repeating ONE correct id must be rejected
    (duplicates — the count alone cannot catch this)."""
    monkeypatch.setattr(pilot, "OUT", tmp_path)
    from agent_civilization.eval import r169_bench_v7 as b7
    real = b7.load_split("holdout")[0]["id"]
    rows = [{"id": real, "reply": "", "error": None}] * 46
    _write_run(tmp_path, "holdout", "grounded-v2", 46, rows)
    with pytest.raises(SystemExit, match="duplicate case ids"):
        pilot._scores("holdout", "grounded-v2")


# ---------------------------------------------------------------------------#
# the v6 holdout itself (frozen properties)
# ---------------------------------------------------------------------------#

def test_v6_holdout_composition():
    from agent_civilization.eval import r169_bench_v7 as b7
    cases = b7.load_split("holdout")
    assert len(cases) == 46
    langs = [c["lang"] for c in cases]
    assert langs.count("ar") == 23 and langs.count("en") == 23
    assert all(c["split"] == "holdout" and c["version"] == "r169-v7" for c in cases)
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
    from agent_civilization.eval import r169_bench_v7 as b7
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
    assert len(b7.load_split("holdout")) == 46


def test_v6_generator_is_deterministic():
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "scripts/r169_build_dataset_v6.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_v6_bench_reexports_frozen_v5_grader():
    from agent_civilization.eval import r169_bench_v5 as b5
    from agent_civilization.eval import r169_bench_v7 as b7
    assert b7.GROUNDED_V2_SYSTEM == b5.GROUNDED_V2_SYSTEM
    assert b7.grounded_v2_messages is b5.grounded_v2_messages
    assert b7.score_case is b5.score_case
    assert b7.run_code_tests is b5.run_code_tests


def test_prereg_guard_covers_v8_prereg(tmp_path):
    """تعديل تسجيل v8 بعد التجميد يجب أن يمنع أي تشغيل — الحارس يشمل V8 لا V6 فقط."""
    # الحالة السليمة: كلا التسجيلين ملتزم ومطابق لـ HEAD ⇒ True
    assert pilot._prereg_committed() is True
    # تسجيل v8 معدّل/غير ملتزم ⇒ False (قبل إصلاح هذه الدفعة كان يُرجع True)
    tampered = tmp_path / "PREREGISTRATION_V8.md"
    tampered.write_text("criteria edited after results\n", encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pilot, "PREREG_V8", tampered)
    try:
        assert pilot._prereg_committed() is False
    finally:
        monkey.undo()


def test_prereg_guard_covers_v9_prereg(tmp_path):
    """حارس التسجيلات يشمل V9 (حزمة الحكم المطابقة) — تعديله بعد التسجيل يمنع أي تشغيل."""
    assert pilot._prereg_committed() is True
    tampered = tmp_path / "PREREGISTRATION_V9.md"
    tampered.write_text("criteria edited after registration\n", encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pilot, "PREREG_V9", tampered)
    try:
        assert pilot._prereg_committed() is False
    finally:
        monkey.undo()


def test_prereg_guard_covers_v10_prereg(tmp_path):
    """حارس التسجيلات يشمل V10 (حزمة الحكم المُكرَّرة بقوة كافية — دفعة 18):
    تعديله بعد التسجيل يمنع أي تشغيل."""
    assert pilot._prereg_committed() is True
    tampered = tmp_path / "PREREGISTRATION_V10.md"
    tampered.write_text("criteria edited after registration\n", encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pilot, "PREREG_V10", tampered)
    try:
        assert pilot._prereg_committed() is False
    finally:
        monkey.undo()


def test_v10_holdout_composition():
    """حزمة v10 المجمدة: 90 مجموعة = 180 حالة (90 عربي + 90 إنجليزي) بتركيبة
    العائلات المسجلة في PREREGISTRATION_V10 §2."""
    from agent_civilization.eval import r169_bench_v10 as b10
    cases = b10.load_split("holdout")
    assert len(cases) == 180
    langs = [c["lang"] for c in cases]
    assert langs.count("ar") == 90 and langs.count("en") == 90
    assert all(c["split"] == "holdout" and c["version"] == "r169-v10" for c in cases)
    fam = {}
    for c in cases:
        fam[c["task_type"]] = fam.get(c["task_type"], 0) + 1
    assert fam == {"grounded_qa": 28, "abstain_missing": 26, "false_premise": 14,
                  "conflicting_sources": 14, "stale_sources": 14, "prompt_injection": 14,
                  "adversarial_user": 14, "tool_poisoning": 26, "code_task": 24,
                  "closed_book": 6}


def test_v10_generator_is_deterministic():
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "scripts/r169_build_dataset_v10.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_v10_bench_reexports_frozen_v5_grader():
    from agent_civilization.eval import r169_bench_v5 as b5
    from agent_civilization.eval import r169_bench_v10 as b10
    assert b10.GROUNDED_V2_SYSTEM == b5.GROUNDED_V2_SYSTEM
    assert b10.grounded_v2_messages is b5.grounded_v2_messages
    assert b10.score_case is b5.score_case
    assert b10.run_code_tests is b5.run_code_tests


def test_v10_outputs_are_isolated():
    """تشغيل/تقييم v10 يكتب في دليل v10 حصرًا — أدلة v6/v8/v9 التاريخية لا تُمس."""
    assert pilot._out("v10") == pilot.OUT_V10
    assert pilot.OUT_V10 != pilot.OUT and pilot.OUT_V10 != pilot.OUT_V8 and pilot.OUT_V10 != pilot.OUT_V9
