"""Q-028 blind-first reviewer packet — alias integrity and reply fingerprints."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HR = ROOT / "docs/audit/M8/logs/raw/r169/v4/human_review"
INDEX = HR / "REVIEWER_CASE_INDEX_PHASE_A.md"
CASE_BUNDLE = HR / "BLIND_REVIEW_CASES_PHASE_A.md"
FORM = HR / "reviewer_form.json"
BLIND_FORM = HR / "reviewer_blind_form.json"
MAPPING = HR / "REVIEWER_CASE_MAPPING_PHASE_B.json"
PACKAGE = HR / "REVIEW_PACKAGE.md"
INDEX_ROW = re.compile(r"^\| (\d+) \| `(C\d{2})` \| \[`Case (C\d{2})`]\(BLIND_REVIEW_CASES_PHASE_A\.md#case-(c\d{2})\) \|$")


def _index_rows():
    return [m.groups() for m in map(INDEX_ROW.match, INDEX.read_text(encoding="utf-8").splitlines()) if m]


def _forms():
    return json.loads(FORM.read_text(encoding="utf-8")), json.loads(BLIND_FORM.read_text(encoding="utf-8")), json.loads(MAPPING.read_text(encoding="utf-8"))


def _form_pairs(form):
    pairs = []
    for section in ("adjudication_of_disagreements", "adjudication_of_uncertain", "label_verification_sample"):
        pairs.extend((cid, value["run"]) for cid, value in form[section].items())
    return pairs


def _fnv(value):
    h = 2166136261
    for char in value:
        h = ((h ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return h


def test_phase_a_index_and_bundle_use_neutral_aliases_only():
    rows = _index_rows()
    assert len(rows) == 27
    assert [r[1] for r in rows] == [f"C{i:02d}" for i in range(1, 28)]
    assert all(r[1] == r[2] and r[2].lower() == r[3] for r in rows)
    idx = INDEX.read_text(encoding="utf-8")
    bundle = CASE_BUNDLE.read_text(encoding="utf-8")
    combined = idx + "\n" + bundle
    assert "94.85%" not in combined
    assert "grader_fail_reason" not in combined and "agent_label" not in combined
    assert "test_baseline_b1" not in combined and "test_grounded-v2_b1" not in combined
    assert "خلاف صارم" not in combined and "عينة تحقق" not in combined
    assert "family:" not in bundle
    assert not re.search(r"r169-v4-[a-z_]+-\d{2}-(?:en|ar)", bundle)


def test_phase_a_form_has_only_aliases_and_empty_judgments():
    official, blind, mapping = _forms()
    refs = [case["case_ref"] for case in blind["cases"]]
    assert refs == [f"C{i:02d}" for i in range(1, 28)]
    assert all(set(case).issubset({"case_ref","anchor","independent_judgment","rationale","confidence"}) for case in blind["cases"])
    assert all(case["independent_judgment"] is None and case["rationale"] == "" and case["confidence"] is None for case in blind["cases"])
    assert blind["reviewer"]["confirmed_human"] is False and blind["reviewer"]["confirmed_no_prior_verdicts"] is False
    assert official["reviewer"]["confirmed_human"] is False and official["reviewer"]["name_or_handle"] == ""
    for section in ("adjudication_of_disagreements","adjudication_of_uncertain","label_verification_sample"):
        assert all(v["overall_pass"] is None for v in official[section].values())


def test_phase_b_mapping_covers_exactly_the_27_source_cases_and_is_not_in_phase_a():
    official, blind, mapping = _forms()
    pairs = _form_pairs(official)
    mapped = mapping["cases"]
    assert len(mapped) == 27
    assert len({item["blind_case_ref"] for item in mapped}) == 27
    assert {(item["case_id"],item["run"]) for item in mapped} == set(pairs)
    assert [item["blind_case_ref"] for item in mapped] == [f"C{i:02d}" for i in range(1, 28)]
    assert mapping["do_not_distribute_before_phase_a_freeze"] is True
    package_phase_a = PACKAGE.read_text(encoding="utf-8").split("## المرحلة ب")[0]
    assert "REVIEWER_CASE_MAPPING_PHASE_B.json" not in package_phase_a


def test_alias_order_is_stable_and_mapping_fingerprints_match_original_replies():
    _, _, mapping = _forms()
    pairs = [item["run"] + "::" + item["case_id"] for item in mapping["cases"]]
    assert pairs == sorted(pairs, key=lambda value: (_fnv(value), value))
    bundle = CASE_BUNDLE.read_text(encoding="utf-8")
    for item in mapping["cases"]:
        assert f"## Case {item['blind_case_ref']}" in bundle
        assert item["case_id"] not in bundle
        raw_path = ROOT / f"docs/audit/M8/logs/raw/r169/v4/{item['run']}.jsonl"
        raw = [json.loads(x) for x in raw_path.read_text(encoding="utf-8").splitlines()][1:]
        reply = next(row for row in raw if row["id"] == item["case_id"]).get("reply") or ""
        assert hashlib.sha256(reply.encode()).hexdigest()[:12] == item["reply_sha256_prefix"]
        labels_path = ROOT / f"benchmarks/r169/v4/blind_labels/{item['run']}.labels.jsonl"
        labels = [json.loads(x) for x in labels_path.read_text(encoding="utf-8").splitlines()]
        label = next(row for row in labels if row.get("id") == item["case_id"])
        assert label["reply_sha256"].startswith(item["reply_sha256_prefix"])


def test_all_phase_a_relative_links_resolve_and_official_form_remains_empty():
    for rel in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", INDEX.read_text(encoding="utf-8")):
        assert (HR / rel).resolve().exists(), rel
    for rel in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", PACKAGE.read_text(encoding="utf-8")):
        assert (HR / rel).resolve().exists(), rel
    official, _, _ = _forms()
    assert official["reviewer"]["confirmed_human"] is False
    for section in ("adjudication_of_disagreements","adjudication_of_uncertain","label_verification_sample"):
        assert all(v["overall_pass"] is None for v in official[section].values())


def test_package_does_not_disclose_results_before_phase_a_is_frozen():
    package = PACKAGE.read_text(encoding="utf-8")
    phase_a = package.split("## المرحلة ب")[0]
    assert "94.85%" not in phase_a
    assert "R169_REPORT_V4_B1.md" not in phase_a
    assert "REVIEWER_CASE_MAPPING_PHASE_B.json" not in phase_a
    assert "REVIEWER_CASE_INDEX.md" not in phase_a
