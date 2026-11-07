#!/usr/bin/env python3
"""SEC-09 (D-137): operator CLI for the OFFLINE master-key rotation.

OWNER-ONLY, under an approved change window, with the server STOPPED.
Default is --dry-run (re-encrypt + verify + ROLLBACK). ``--apply`` commits,
after taking an online SQLite backup of the store next to it (the backup is
still encrypted under the OLD key — it is the rollback path, and it is
old-key material: destroy it with the old key file once the rotation is
verified; this tool never deletes anything).

Usage:
  python scripts/sec09_rekey_secret_store.py --db PATH --old-key PATH --new-key PATH [--apply]
  python scripts/sec09_rekey_secret_store.py --db PATH --verify-key PATH

Prints a JSON report with counts only — never a secret value.
Exit: 0 ok; 1 refused/rolled back; 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_civilization.core.secret_rekey import (  # noqa: E402
    RekeyError, rekey_secret_store, verify_store_key,
)


def _backup(db: str) -> str:
    dest = f"{db}.pre-rekey-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.bak"
    if os.path.exists(dest):
        raise RekeyError(f"backup target exists: {dest}")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--old-key")
    ap.add_argument("--new-key")
    ap.add_argument("--verify-key")
    ap.add_argument("--apply", action="store_true",
                    help="COMMIT the rotation (default: dry-run + rollback)")
    a = ap.parse_args(argv)
    try:
        if a.verify_key:
            ok = verify_store_key(a.db, a.verify_key)
            print(json.dumps({"key_opens_store": ok}))
            return 0 if ok else 1
        if not (a.old_key and a.new_key):
            ap.error("--old-key and --new-key are required (or --verify-key)")
        report = {}
        if a.apply:
            # prove it first, on the real file, then back up, then commit
            rekey_secret_store(a.db, a.old_key, a.new_key, dry_run=True)
            report["backup"] = _backup(a.db)
        report.update(rekey_secret_store(a.db, a.old_key, a.new_key, dry_run=not a.apply))
        if a.apply:
            report["new_key_opens_store"] = verify_store_key(a.db, a.new_key)
            report["old_key_opens_store"] = verify_store_key(a.db, a.old_key)
        print(json.dumps(report, indent=2))
        return 0
    except RekeyError as exc:
        print(json.dumps({"refused": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
