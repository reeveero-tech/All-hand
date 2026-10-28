"""R-169 v6 bench — thin wrapper over the FROZEN v5 grader (R-170 لاحق 72).

The v6 holdout dataset (benchmarks/r169/v6, single ``holdout`` split) is
structurally identical to v5: same ten families, same case schema, same grader
logic. Only the dataset directory and the case ``version`` tag differ, so this
module reuses ``r169_bench_v5`` unchanged and does NOT copy any grader code:

* ``DATASET_DIR`` points at ``benchmarks/r169/v6``;
* ``validate_case`` checks ``version == "r169-v6"`` then delegates every other
  structural check to the frozen ``r169_bench_v5.validate_case`` (the version
  tag is the only field spoofed in the delegation — it is a pure tag, not an
  input to any structural check);
* ``load_split``/``SPLITS`` mirror v5 with ``holdout`` instead of the spent
  dev/validation/test splits;
* every grader function (``GROUNDED_V2_SYSTEM``, ``grounded_v2_messages``,
  ``extract_code``, ``score_record``, …) is re-exported from v5 unchanged.

If v6 ever needs a grader change, that change belongs in a NEW bench module with
its own declared defects — never here and never in v5.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_civilization.eval import r169_bench_v5 as _v5

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "benchmarks" / "r169" / "v6"
DATASET_VERSION = "r169-v6"

# re-export the frozen v5 grader surface unchanged
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
# lives in scripts/r169_run_v5.py and is version-agnostic — the v6 pilot reuses it.

SPLITS = ("holdout",)


def validate_case(c: dict) -> None:
    """v6 version/split tags, then every other structural check delegated to v5.

    ``version`` and ``split`` are pure tags (never inputs to a structural
    check), so the delegation spoofs only those two fields to their v5 values;
    the real split value is verified separately by ``load_split``.
    """
    if c.get("version") != DATASET_VERSION:
        raise DatasetIntegrityError(f"{c.get('id')}: not a {DATASET_VERSION} case")
    if c.get("split") != "holdout":
        raise DatasetIntegrityError(f"{c.get('id')}: split {c.get('split')!r} != 'holdout'")
    _v5.validate_case({**c, "version": "r169-v5", "split": "test"})


def load_split(split: str, root: Path = DATASET_DIR, verify: bool = True) -> list[dict]:
    """Same contract as ``r169_bench_v5.load_split`` but for the v6 holdout."""
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r} (expected one of {SPLITS})")
    if verify:
        verify_dataset(root)
    rows = [
        json.loads(line)
        for line in (root / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for r in rows:
        validate_case(r)
        if r["split"] != split:
            raise DatasetIntegrityError(f"{r['id']}: split field {r['split']!r} != {split!r}")
    return rows
