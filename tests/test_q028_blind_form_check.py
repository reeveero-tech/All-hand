"""Tests for scripts/check_q028_blind_form.py (Q-028 Phase A, structure only).

Defect being fixed (recorded in log 261): the handoff instructions told the
coordinator to run ``check_q028_form.py`` on the returned Phase A form at receipt.
That checker validates the *official* form; on a complete blind form it reports
"incomplete" and prints original case ids and selection sections (Phase B
information). The new checker must accept a complete blind form and must not
reference or print any Phase B material.

All forms below are SYNTHETIC fixtures built in code; none is a review.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_q028_blind_form.py"
sys.path.insert(0, str(ROOT / "scripts"))
import check_q028_blind_form as chk  # noqa: E402


def _blank() -> dict:
    return {
        "kind": "Q-028 Phase A — synthetic test blank",
        "instructions": "synthetic",
        "frozen_evaluated_commit": "0" * 40,
        "reviewer": {"confirmed_human": False, "confirmed_no_prior_verdicts": False,
                     "name_or_handle": "", "review_date": "", "role": ""},
        "cases": [{"case_ref": f"C{i:02d}", "anchor": f"Case C{i:02d}", "independent_judgment": None,
                   "rationale": "", "confidence": None} for i in range(1, 28)],
    }


def _complete() -> dict:
    f = _blank()
    f["reviewer"].update(confirmed_human=True, confirmed_no_prior_verdicts=True,
                         name_or_handle="synthetic-fixture", review_date="2026-10-22", role="SYNTHETIC")
    for c in f["cases"]:
        c.update(independent_judgment="uncertain", rationale="SYNTHETIC FIXTURE - not a review", confidence="low")
    return f


def test_complete_synthetic_form_passes_with_and_without_blank():
    assert chk.check(_complete())["complete"] is True
    r = chk.check(_complete(), _blank())
    assert r["complete"] is True and r["answered"] == 27


def test_blank_form_is_incomplete():
    r = chk.check(_blank(), _blank())
    assert r["complete"] is False and r["answered"] == 0


def test_invalid_values_and_missing_fields_are_reported_by_blind_ref():
    f = _complete()
    f["cases"][2]["independent_judgment"] = "maybe"
    f["cases"][5]["rationale"] = "  "
    f["cases"][7]["confidence"] = 0.9
    f["reviewer"]["confirmed_no_prior_verdicts"] = False
    r = chk.check(f, _blank())
    joined = "\n".join(r["problems"])
    assert r["complete"] is False and r["answered"] == 24
    for ref in ("C03", "C06", "C08"):
        assert ref in joined
    assert "confirmed_no_prior_verdicts" in joined


def test_case_set_order_anchor_and_frozen_fields_are_protected():
    f = _complete()
    f["cases"].pop()
    f["cases"][0], f["cases"][1] = f["cases"][1], f["cases"][0]
    f["cases"][3]["anchor"] = "renamed"
    f["frozen_evaluated_commit"] = "1" * 40
    joined = "\n".join(chk.check(f, _blank())["problems"])
    assert "case set/order changed" in joined and "C27" in joined
    assert "C04: anchor changed" in joined
    assert "top-level field changed: frozen_evaluated_commit" in joined


def test_cli_exit_codes_and_sha(tmp_path):
    good, bad, blank = tmp_path / "good.json", tmp_path / "bad.json", tmp_path / "blank.json"
    good.write_text(json.dumps(_complete(), ensure_ascii=False), encoding="utf-8")
    bad.write_text(json.dumps(_blank(), ensure_ascii=False), encoding="utf-8")
    blank.write_text(json.dumps(_blank(), ensure_ascii=False), encoding="utf-8")
    ok = subprocess.run([sys.executable, str(SCRIPT), str(good), "--blank", str(blank)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    assert re.search(r'"sha256": "[0-9a-f]{64}"', ok.stdout)
    ko = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
    assert ko.returncode == 1


def test_checker_is_phase_a_safe():
    """No Phase B paths/terms in the tool; output never carries verdict counts."""
    src = SCRIPT.read_text(encoding="utf-8")
    for term in ("REVIEWER_CASE_MAPPING", "reviewer_form.json\"", "adjudication_of", "label_verification",
                 "agent_label", "grader_fail_reason", "r169-v4-"):
        assert term not in src, term
    f = _complete()
    f["cases"][0]["independent_judgment"] = "pass"
    out = json.dumps(chk.check(copy.deepcopy(f)), ensure_ascii=False)
    for word in ("pass_count", "fail_count", "agreement", "distribution"):
        assert word not in out


# --- D-100: AI council mode ---------------------------------------------------
# Defect before the fix: the only mode required reviewer.confirmed_human == true,
# so an honest AI council form (confirmed_human == false) could only pass by
# lying. The council mode must require confirmed_human to be exactly false.

def _council_complete() -> dict:
    f = _complete()
    f["reviewer"].update(confirmed_human=False, name_or_handle="synthetic-member",
                         role="AI council member (D-100) — SYNTHETIC")
    return f


def test_human_mode_rejects_honest_council_form():
    r = chk.check(_council_complete(), _blank())
    assert r["complete"] is False
    assert any("confirmed_human must be true" in p for p in r["problems"])


def test_council_mode_accepts_honest_council_form():
    r = chk.check(_council_complete(), _blank(), council=True)
    assert r["complete"] is True and r["answered"] == 27


def test_council_mode_rejects_model_claiming_to_be_human():
    f = _council_complete()
    f["reviewer"]["confirmed_human"] = True
    r = chk.check(f, _blank(), council=True)
    assert r["complete"] is False
    assert any("must be false" in p for p in r["problems"])


def test_council_mode_requires_role_declaration_and_no_prior_verdicts():
    f = _council_complete()
    f["reviewer"]["role"] = "reviewer"
    f["reviewer"]["confirmed_no_prior_verdicts"] = False
    probs = chk.check(f, _blank(), council=True)["problems"]
    assert any("ai council member" in p for p in probs)
    assert any("confirmed_no_prior_verdicts" in p for p in probs)


def test_council_mode_cli_reports_mode(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps(_council_complete()), encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text(json.dumps(_blank()), encoding="utf-8")
    out = subprocess.run([sys.executable, str(SCRIPT), str(p), "--blank", str(b), "--council"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not human" in json.loads(out.stdout)["mode"]
