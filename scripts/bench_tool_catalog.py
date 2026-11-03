#!/usr/bin/env python3
"""R-175 batch 2 (D-126): on-demand catalog throughput benchmark.

The CI test measures at the spec's phase-2 number (1,000). This script runs
the SAME measurement at any scale on demand — including the 10,000 gate:

    python scripts/bench_tool_catalog.py --n 10000

The registry is a throwaway file (default: a temp dir, deleted afterwards);
nothing touches the shared store. The report is printed as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_civilization.core.tool_catalog_bench import measure_throughput
from agent_civilization.core.tool_registry_store import PersistentToolRegistry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=1_000,
                    help="catalog size to seed (default: 1000)")
    ap.add_argument("--certify-items", type=int, default=25,
                    help="governed walk items (default: 25; stays inside "
                         "the phase-1 ceiling of 100)")
    ap.add_argument("--keep-db", default=None,
                    help="optional path to keep the benchmark registry file")
    args = ap.parse_args()

    path = args.keep_db or os.path.join(tempfile.mkdtemp(prefix="bench_r175_"),
                                         "bench.sqlite3")
    reg = PersistentToolRegistry(path)
    try:
        report = measure_throughput(reg, n=args.n,
                                    certify_items=args.certify_items)
    finally:
        reg.close()
        if args.keep_db is None:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
