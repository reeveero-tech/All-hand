#!/usr/bin/env python3
"""R-202: write / check the committed JSON Schemas of the AGOS v2 contracts.

    python3 scripts/export_v2_contract_schemas.py          # (re)write docs/governance/contracts/v2/
    python3 scripts/export_v2_contract_schemas.py --check  # rc=1 on drift or a missing file

Source of truth: agos_v2/contracts.py. Each contract ``<name>`` at version
``v`` lives in ``docs/governance/contracts/v2/<name>.v<v>.schema.json``.
A schema file that no longer matches the code is drift (rc=1); a schema file
of an older version is kept (D-064) and not reported.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "governance" / "contracts" / "v2"
sys.path.insert(0, str(ROOT))

from agos_v2 import contracts  # noqa: E402


def _dump(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _path(name: str) -> pathlib.Path:
    return OUT / f"{name}.v{contracts.CONTRACT_VERSION}.schema.json"


def main(argv: list[str]) -> int:
    schemas = contracts.json_schemas()
    if "--check" in argv:
        bad = 0
        for name, schema in sorted(schemas.items()):
            p = _path(name)
            if not p.exists():
                print(f"MISSING {p.relative_to(ROOT)}")
                bad += 1
            elif p.read_text(encoding="utf-8") != _dump(schema):
                print(f"DRIFT {name} vs {p.relative_to(ROOT)} (regenerate in the same batch)")
                bad += 1
        print(f"v2 contracts: {len(schemas)} checked, {bad} problem(s)")
        return 1 if bad else 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, schema in sorted(schemas.items()):
        _path(name).write_text(_dump(schema), encoding="utf-8")
        print(f"wrote {_path(name).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
