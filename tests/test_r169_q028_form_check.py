"""Q-028 reviewer form completeness checker (structure only, never content).

The checker must: report the committed form as INCOMPLETE (nobody has filled it);
accept a fully answered synthetic form regardless of WHAT was answered; reject
partial forms, a missing confirmed_human, missing metadata, changed case sets and
edits to the agent-owned context fields. It must never compute or suggest a verdict.
"""
import copy
import json
from pathlib import Path

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_q028_form as chk  # noqa: E402

FORM = Path(__file__).resolve().parents[1] / "docs/audit/M8/logs/raw/r169/v4/human_review/reviewer_form.json"


def _form():
    return json.loads(FORM.read_text(encoding="utf-8"))


def _filled(value=True, right="label"):
    f = _form()
    f["reviewer"].update(confirmed_human=True, name_or_handle="synthetic-test-only", review_date="2026-10-22", role="test")
    for sec in chk.SECTIONS:
        for v in f[sec].values():
            for k in chk.answer_fields(v):
                v[k] = right if k == "grader_or_label_who_is_right" else value
    return f


def test_committed_form_is_incomplete_and_unfilled():
    r = chk.check(_form(), _form())
    assert r["complete"] is False
    assert r["answered"] == 0 and r["cases"] == 27
    assert "reviewer.confirmed_human must be true" in r["problems"]


@pytest.mark.parametrize("value,right", [(True, "label"), (False, "grader"), (True, "neither, see comment")])
def test_any_fully_answered_form_is_complete_content_is_not_judged(value, right):
    r = chk.check(_filled(value, right), _form())
    assert r["complete"] is True and r["problems"] == [] and r["answered"] == 27


def test_partial_form_is_incomplete():
    f = _filled()
    next(iter(f["adjudication_of_disagreements"].values()))["overall_pass"] = None
    r = chk.check(f, _form())
    assert r["complete"] is False and r["answered"] == 26


def test_missing_metadata_rejected():
    f = _filled()
    f["reviewer"]["review_date"] = ""
    assert "reviewer.review_date is empty" in chk.check(f, _form())["problems"]


def test_case_set_change_rejected():
    f = _filled()
    f["label_verification_sample"].pop(next(iter(f["label_verification_sample"])))
    assert any("case set changed" in p for p in chk.check(f, _form())["problems"])


def test_agent_owned_fields_must_not_change():
    f = _filled()
    next(iter(f["adjudication_of_disagreements"].values()))["agent_label"] = "fail"
    assert any("agent-owned field changed" in p for p in chk.check(f, _form())["problems"])


def test_checker_output_has_no_verdict_or_agreement_keys():
    r = chk.check(_filled(), _form())
    assert set(r) == {"complete", "cases", "answered", "problems"}


def test_reference_is_not_mutated():
    ref = _form()
    before = copy.deepcopy(ref)
    chk.check(_filled(), ref)
    assert ref == before
