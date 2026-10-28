#!/usr/bin/env python3
"""R-169 v7: the JUDGMENT holdout for the language-fix verification (R-170 لاحق 72).

Why v7 (advisor review): the preregistered order (§4) was "freeze the v4
candidate BEFORE creating the judgment set" — v6 was created BEFORE the v4 dev
runs, which is a documented deviation. v6 is KEPT AS IS (never used for
judgment, never deleted) and v7 is the judgment package created ONCE AFTER the
v4 candidate was frozen (commit 9b6de852 + the dev r2 verification).

Generation plan (declared here, before generation): identical family templates
and vocabulary pool to v6 (already disjoint from v1-v5), with a NEW SEED (1697)
so every case instance is new; the code tasks and the closed_book fact are the
same new ones authored for v6 (new relative to v1-v5; v6 was never consumed).
Graded by the frozen v5 grader (via r169_bench_v7, a thin wrapper).

Usage:
    python scripts/r169_build_dataset_v7.py            # (re)write benchmarks/r169/v7
    python scripts/r169_build_dataset_v7.py --check    # exit 1 if files differ
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import r169_build_dataset_v6 as base  # noqa: E402  (reuse every family template)

# the ONLY deltas from v6: version tag, seed, output directory
base.DATASET_VERSION = "r169-v7"
base.SEED = 1697
base.OUT = ROOT / "benchmarks" / "r169" / "v7"
base.PROV = "synthetic (CC0-1.0, authored in scripts/r169_build_dataset_v7.py)"


def main(argv=None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    sys.exit(main())
