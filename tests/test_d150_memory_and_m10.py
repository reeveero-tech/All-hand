"""D-150: project memory (MEMORY.md) is mandatory in every batch (R0), and the
M10 program (R-200..R-243) is present, strictly ordered and table-synced."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_atomic_batch as cab  # noqa: E402

GOV = ROOT / "docs" / "governance"
MEM = "docs/governance/MEMORY.md"
BASE = ["docs/governance/ROADMAP.md", "docs/governance/PROJECT_STATE.md",
        "docs/governance/CHANGELOG.md"]


def test_memory_is_mandatory_in_r0():
    assert MEM in cab.MANDATORY
    got = cab.evaluate(BASE + ["docs/governance/API.md"])
    assert any(v.startswith("R0") and MEM in v for v in got), got


def test_docs_only_batch_with_memory_passes_r0():
    got = cab.evaluate(BASE + [MEM])
    assert not any(v.startswith("R0") for v in got), got


def test_memory_file_exists_and_names_next_item():
    text = (GOV / "MEMORY.md").read_text(encoding="utf-8")
    assert "البند التالي" in text and "R-2" in text


def test_m10_items_complete_and_chained():
    road = (GOV / "ROADMAP.md").read_text(encoding="utf-8")
    ids = re.findall(r"^#### (R-2\d\d) — ", road, re.M)
    assert ids == [f"R-{n}" for n in range(200, 244)]
    for i, rid in enumerate(ids):
        want = "لا شيء" if i == 0 else ids[i - 1]
        assert f"- الاعتماديات ({rid}): {want}\n" in road, rid


def test_m10_table_present_in_project_state():
    state = (GOV / "PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "## حالة بنود M10" in state
    for n in range(200, 244):
        assert re.search(rf"^\| R-{n} \| `\w+` \|", state, re.M), n

