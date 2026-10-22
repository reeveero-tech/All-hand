"""Q-028 blind-first reviewer materials — integrity, neutrality, and fingerprints."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HR = ROOT / "docs/audit/M8/logs/raw/r169/v4/human_review"
INDEX = HR / "REVIEWER_CASE_INDEX_PHASE_A.md"
FORM = HR / "reviewer_form.json"
BLIND_FORM = HR / "reviewer_blind_form.json"
PACKAGE = HR / "REVIEW_PACKAGE.md"
ROW = re.compile(r"^\| (\d+) \| `([^\x60]+)` \| (\S+) \| \[`([^:]+):(\d+)`\]\(([^)]+)\) \| `([0-9a-f]{12})` \|$")


def _rows():
    return [m.groups() for m in map(ROW.match, INDEX.read_text(encoding="utf-8").splitlines()) if m]


def _form_pairs():
    form = json.loads(FORM.read_text(encoding="utf-8"))
    pairs = []
    for section in ("adjudication_of_disagreements", "adjudication_of_uncertain", "label_verification_sample"):
        pairs.extend((cid, value["run"]) for cid, value in form[section].items())
    return form, pairs


def _fnv(value):
    h = 2166136261
    for char in value:
        h = ((h ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return h


def test_phase_a_index_covers_exact_form_cases_without_verdicts_or_categories():
    form, pairs = _form_pairs()
    rows = _rows()
    assert len(rows) == 27
    assert {(r[1], r[2]) for r in rows} == set(pairs)
    assert len({(r[1], r[2]) for r in rows}) == 27
    phase_a_index = INDEX.read_text(encoding="utf-8")
    assert "خلاف صارم" not in phase_a_index
    assert "عينة تحقق" not in phase_a_index
    assert "94.85%" not in phase_a_index
    assert "grader_fail_reason" not in phase_a_index
    assert "| القسم |" not in phase_a_index


def test_phase_a_form_has_same_cases_without_agent_or_grader_verdict_fields():
    _, pairs = _form_pairs()
    blind = json.loads(BLIND_FORM.read_text(encoding="utf-8"))
    refs = [item["case_ref"] for item in blind["cases"]]
    assert len(refs) == 27 and len(set(refs)) == 27
    assert set(refs) == {run + "::" + cid for cid, run in pairs}
    forbidden = {"agent_label", "grader_fail_reason", "gate_result", "grader_verdict", "selection_category"}
    for case in blind["cases"]:
        assert not (forbidden & set(case))
        assert case["independent_judgment"] is None
        assert case["rationale"] == "" and case["confidence"] is None
    assert blind["reviewer"]["confirmed_human"] is False
    assert blind["reviewer"]["confirmed_no_prior_verdicts"] is False
    assert [item["case_ref"] for item in blind["cases"]] == sorted(refs, key=lambda value: (_fnv(value), value))


def test_case_anchors_and_reply_fingerprints_remain_unchanged():
    for _, cid, run, sheet, line, href, sha in _rows():
        path = (HR / href.split("#")[0]).resolve()
        assert path.name == sheet == f"{run}.sheet.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[int(line) - 1].startswith("## ") and cid in lines[int(line) - 1]
        raw = [json.loads(x) for x in (ROOT / f"docs/audit/M8/logs/raw/r169/v4/{run}.jsonl").open(encoding="utf-8")][1:]
        reply = next(item for item in raw if item["id"] == cid).get("reply") or ""
        assert hashlib.sha256(reply.encode()).hexdigest()[:12] == sha
        labels = [json.loads(x) for x in (ROOT / f"benchmarks/r169/v4/blind_labels/{run}.labels.jsonl").open(encoding="utf-8")]
        label = next(item for item in labels if item.get("id") == cid)
        assert label["reply_sha256"].startswith(sha)


def test_all_phase_a_relative_links_resolve():
    for href in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", INDEX.read_text(encoding="utf-8")):
        assert (HR / href).resolve().exists(), href
    for href in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", PACKAGE.read_text(encoding="utf-8")):
        assert (HR / href).resolve().exists(), href


def test_phase_a_package_does_not_disclose_aggregate_result():
    package = PACKAGE.read_text(encoding="utf-8")
    phase_a = package.split("## المرحلة ب")[0]
    assert "94.85%" not in phase_a
    assert "R169_REPORT_V4_B1.md" not in phase_a
    assert "12 خلاف" not in phase_a


def test_official_reviewer_form_remains_unfilled_by_agent():
    form, _ = _form_pairs()
    assert form["reviewer"]["confirmed_human"] is False and form["reviewer"]["name_or_handle"] == ""
    for section in ("adjudication_of_disagreements", "adjudication_of_uncertain", "label_verification_sample"):
        assert all(value["overall_pass"] is None for value in form[section].values())
