"""Q-028 reviewer case index (R-169 v4 b1) — integrity of the additive companion file.

The index must (a) point every case to the right heading line in the blind sheet,
(b) carry reply fingerprints that match the published blind labels, (c) cover exactly
the cases in reviewer_form.json, and (d) have only resolvable relative links.
It must never contain a human verdict: the form stays unfilled (confirmed_human false).
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HR = ROOT / "docs/audit/M8/logs/raw/r169/v4/human_review"
INDEX = HR / "REVIEWER_CASE_INDEX.md"
FORM = HR / "reviewer_form.json"
ROW = re.compile(r"^\| (\d+) \| [^|]+ \| `([^`]+)` \| (\S+) \| \[`([^:]+):(\d+)`\]\(([^)]+)\) \| `([0-9a-f]{12})` \|$")


def _rows():
    return [m.groups() for m in map(ROW.match, INDEX.read_text(encoding="utf-8").splitlines()) if m]


def _form_ids():
    f = json.loads(FORM.read_text(encoding="utf-8"))
    return f, [(cid, v["run"]) for s in ("adjudication_of_disagreements", "adjudication_of_uncertain",
                                          "label_verification_sample") for cid, v in f[s].items()]


def test_index_covers_exactly_the_form_cases():
    _, ids = _form_ids()
    assert [(r[1], r[2]) for r in _rows()] == ids
    assert len(ids) == 27


def test_line_anchors_point_to_case_headings():
    for _, cid, run, sheet, line, href, _ in _rows():
        path = (HR / href.split("#")[0]).resolve()
        assert path.name == sheet == f"{run}.sheet.md"
        text = path.read_text(encoding="utf-8").splitlines()
        assert text[int(line) - 1].startswith("## ") and f"`{cid}`" in text[int(line) - 1]


def test_fingerprints_match_raw_replies_and_labels():
    for _, cid, run, _, _, _, sha in _rows():
        raw = [json.loads(x) for x in (ROOT / f"docs/audit/M8/logs/raw/r169/v4/{run}.jsonl").open(encoding="utf-8")][1:]
        reply = next(r for r in raw if r["id"] == cid).get("reply") or ""
        assert hashlib.sha256(reply.encode()).hexdigest()[:12] == sha
        labels = [json.loads(x) for x in (ROOT / f"benchmarks/r169/v4/blind_labels/{run}.labels.jsonl").open(encoding="utf-8")]
        lab = next(x for x in labels if x.get("id") == cid)
        assert lab["reply_sha256"].startswith(sha)


def test_all_relative_links_resolve():
    for href in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", INDEX.read_text(encoding="utf-8")):
        assert (HR / href).resolve().exists(), href


def test_form_remains_unfilled_by_agent():
    f, _ = _form_ids()
    assert f["reviewer"]["confirmed_human"] is False and f["reviewer"]["name_or_handle"] == ""
    for s in ("adjudication_of_disagreements", "adjudication_of_uncertain", "label_verification_sample"):
        assert all(v["overall_pass"] is None for v in f[s].values())
