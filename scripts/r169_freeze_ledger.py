"""R-169 freeze-drift ledger lookup (R-170 batch 12).

``benchmarks/r169/FREEZE_DRIFT.json`` records every reviewed drift of a frozen
R-169 file as a LINK ``frozen -> current``. Successive changes form a chain
(batch 10: 7e80… -> d661…; batch 12: d661… -> d3f8…). A drift is documented
only if a chain of links for the same benchmark/key/file leads EXACTLY from
the frozen hash to the current one. Tests use this; the runners' check_freeze
is not relaxed (any drift still refuses held-out runs).
"""
from __future__ import annotations

import json
from pathlib import Path

LEDGER_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "r169" / "FREEZE_DRIFT.json"


def entries():
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))["entries"]


def documented(version: str, key: str, path: str, frozen_h: str, current_h: str) -> bool:
    links = {e["frozen"]: e["current"] for e in entries()
             if version in e["benchmarks"] and e["key"] == key and e["file"] == path}
    h, seen = frozen_h, set()
    while h in links and h not in seen:
        seen.add(h)
        h = links[h]
        if h == current_h:
            return True
    return False
