#!/usr/bin/env python3
"""R-169 v10 bench — thin wrapper over the FROZEN v5 grader (R-170 لاحق 80).

Per PREREGISTRATION_V10: v10 is the POWERED replication judgment package for
the frozen v4r candidate (unchanged since e41101c5, merged to main). The v9
verdict was structurally untestable (B1 margin >= 3 impossible with a control
arm failing Arabic 1/16), so this package is 90 groups / 180 cases sized by
the committed power calculation (scripts/r169_power_v10.py). No grader logic
changes — the frozen v5 grader is reused unchanged via delegation, exactly the
pattern of r169_bench_v7/r169_bench_v9.

If v10 ever needs a grader change, that change belongs in a NEW bench module
with its own declared defects — never here and never in v5.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_civilization.eval import r169_bench_v5 as _v5

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "benchmarks" / "r169" / "v10"
DATASET_VERSION = "r169-v10"

# re-export the frozen v5 grader surface unchanged (same list as r169_bench_v9)
GROUNDED_V2_SYSTEM = _v5.GROUNDED_V2_SYSTEM
GROUNDED_V2_NO_SOURCES_NOTE = _v5.GROUNDED_V2_NO_SOURCES_NOTE
GROUNDING_GLOSSARY = getattr(_v5, "GROUNDING_GLOSSARY", None)
REQUIRED = _v5.REQUIRED
DatasetIntegrityError = _v5.DatasetIntegrityError
verify_dataset = _v5.verify_dataset
grounded_v2_messages = _v5.grounded_v2_messages
baseline_text_payload = _v5.baseline_text_payload
extract_code = _v5.extract_code
run_code_tests = _v5.run_code_tests
score_case = _v5.score_case
# NOTE: score_record (which combines the bench predicates into pass/language_match)
# lives in scripts/r169_run_v5.py and is version-agnostic — the pilot reuses it.

SPLITS = ("holdout",)


def validate_case(c: dict) -> None:
    """v10 version/split tags, then every other structural check delegated to v5
    (the version/split tags are pure tags — spoofed in the delegation exactly
    as r169_bench_v7/r169_bench_v9 do; the real split value is verified by
    load_split)."""
    if c.get("version") != DATASET_VERSION:
        raise DatasetIntegrityError(f"{c.get('id')}: not a {DATASET_VERSION} case")
    if c.get("split") != "holdout":
        raise DatasetIntegrityError(f"{c.get('id')}: split {c.get('split')!r} != 'holdout'")
    _v5.validate_case({**c, "version": "r169-v5", "split": "test"})


def load_manifest() -> dict:
    return json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))


def load_split(split: str) -> list[dict]:
    if split not in SPLITS:
        raise DatasetIntegrityError(f"unknown split {split!r}")
    raw = (DATASET_DIR / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in raw if x.strip()]
    manifest = load_manifest()
    if len(rows) != manifest["total_cases"]:
        raise DatasetIntegrityError(
            f"{split}: {len(rows)} rows but the manifest declares {manifest['total_cases']}")
    out = []
    for c in rows:
        validate_case(c)
        out.append(c)
    return out
