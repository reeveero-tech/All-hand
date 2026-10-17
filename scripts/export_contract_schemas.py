#!/usr/bin/env python3
"""R-160: write / check the committed JSON Schemas of the AGOS contracts.

    python3 scripts/export_contract_schemas.py          # (re)write docs/contracts/*.schema.json
    python3 scripts/export_contract_schemas.py --check  # rc=1 on drift, rc=0 in sync

Each contract ``<name>`` at version ``v`` lives in
``docs/contracts/<name>.v<v>.schema.json``. ``--check`` regenerates the schemas
from the code (agent_civilization/core/contracts.py + the server's request
models) and compares them with the committed files:

- identical -> ok;
- different at the SAME version -> drift: lists the additive and breaking
  differences (contracts.classify_changes). Additive-only drift is still a
  failure (the committed schema must be regenerated in the same batch), but it
  is reported as such; breaking drift additionally says a version bump is
  needed;
- a new version -> its file must exist, and the previous version's file stays
  (D-064: no deletion) so old consumers keep a reference.

Importing ``server`` builds the civilization; the script sets AGOS_ENV=test
when unset (schema export needs no credentials) and never starts a server.
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "contracts"
sys.path.insert(0, str(ROOT))


def generated() -> dict:
    os.environ.setdefault("AGOS_ENV", "test")
    from agent_civilization.core import contracts
    req = {name: getattr(importlib.import_module(mod), attr)
           for name, (mod, attr) in contracts.REQUEST_MODELS.items()}
    return contracts.json_schemas(req)


def _rel(p: pathlib.Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def _path(name: str, schema: dict) -> pathlib.Path:
    return OUT / f"{name}.v{schema['x-contract-version']}.schema.json"


def _dump(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def check(schemas: dict) -> int:
    from agent_civilization.core import contracts
    problems = 0
    for name, schema in sorted(schemas.items()):
        path = _path(name, schema)
        if not path.exists():
            print(f"MISSING {_rel(path)} (new contract or version: run without --check)")
            problems += 1
            continue
        committed = json.loads(path.read_text(encoding="utf-8"))
        if _dump(committed) == _dump(schema):
            continue
        diff = contracts.classify_changes(committed, schema)
        problems += 1
        kind = "BREAKING" if diff["breaking"] else "ADDITIVE"
        print(f"DRIFT {name} v{schema['x-contract-version']} ({kind}) vs {_rel(path)}")
        for d in diff["breaking"]:
            print(f"  breaking: {d}")
        for d in diff["additive"]:
            print(f"  additive: {d}")
        if diff["breaking"]:
            print("  -> breaking change: bump the contract version (keep the old schema file)")
        elif not diff["additive"]:
            print("  -> non-structural difference (description/constraint): regenerate")
    for name, schema in schemas.items():  # previous versions must remain (D-064)
        for v in range(1, schema["x-contract-version"]):
            old = OUT / f"{name}.v{v}.schema.json"
            if not old.exists():
                print(f"MISSING PREVIOUS VERSION {_rel(old)}")
                problems += 1
    print(f"contracts={len(schemas)} problems={problems}")
    return 1 if problems else 0


def main(argv) -> int:
    schemas = generated()
    if "--check" in argv:
        return check(schemas)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, schema in sorted(schemas.items()):
        _path(name, schema).write_text(_dump(schema), encoding="utf-8")
    print(f"wrote {len(schemas)} schemas to {_rel(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
