"""R-186: unit tests for the do-not-merge hold-marker gate.

Red-first: the detection function fails on the exact marker shapes used by
PRs #215/#216/#217 ("DO NOT MERGE", "لا يُدمج") and must not fire on
ordinary text that merely mentions merging in a non-hold sense.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_do_not_merge import _strip_marks, find_markers


# --- detection: the exact incident shapes -----------------------------------

def test_incident_title_en():
    hits = find_markers(
        "R-169 v4: fixed grader + new test split + blind protocol (DO NOT MERGE)", "")
    assert hits and hits[0]["where"] == "title"


def test_incident_body_ar():
    hits = find_markers("", "> **لا يُدمج (DO NOT MERGE)** — القياس لم يكتمل.")
    assert hits, "Arabic hold marker must be detected"


def test_ar_marker_without_diacritics():
    hits = find_markers("", "هذا الطلب لا يدمج قبل استيفاء الشروط.")
    assert hits


def test_ar_marker_with_extra_diacritics():
    hits = find_markers("", "لا يُدْمَجُ هذا")
    assert hits


def test_ar_marker_with_tatweel_and_rtl_marks():
    body = "لا يـدمج" + "‎"  # tatweel + LRM
    assert find_markers("", body)


def test_dnm_abbreviation_not_matched():
    assert find_markers("DNM: quick fix", "") == []


def test_plain_merge_mention_not_matched():
    assert find_markers("How to merge branches in git", "") == []
    assert find_markers("", "merge conflict resolution notes") == []


def test_do_not_merge_hyphenated():
    assert find_markers("[DO-NOT-MERGE] experiment", "")


def test_case_insensitive():
    assert find_markers("do Not Merge this", "")


def test_multiline_body():
    body = "## Summary\n\nfixes a bug\n\n> DO NOT MERGE until reviewed"
    assert find_markers("", body)


def test_hold_merge_alias():
    assert find_markers("hold-merge: awaiting owner", "")


def test_empty_pr():
    assert find_markers("", "") == []


def test_marker_in_body_only():
    hits = find_markers("Add feature X", "Some text\n\nDO NOT MERGE\n")
    assert len(hits) == 1 and hits[0]["where"] == "body"


def test_strip_marks_removes_diacritics():
    assert _strip_marks("يُدمج") == "يدمج"
    assert "ـ" not in _strip_marks("لا يـدمج")
