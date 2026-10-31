#!/usr/bin/env python3
"""R-171 batch 3 (D-113): operator CLI for the SHARED tool registry.

Usage (on the host whose workers share ``AGOS_TOOL_REGISTRY_DB``):

    python scripts/tool_registry_admin.py list [--json]
    python scripts/tool_registry_admin.py disable <tool> --actor <who>
    python scripts/tool_registry_admin.py enable  <tool> --actor <who>
    python scripts/tool_registry_admin.py audit [--json] [--tool <tool_id>]

``<tool>`` is a tool id (``agos.internal.analyst``) or a specialization name
(``analyst``); the latest version is used. Every change is written to the
store's audit trail with the given actor and reaches every worker's next
check without a restart. ``enable`` moves ``disabled``/``failed`` back to
``registered`` (the registry's lifecycle graph). Exit codes: 0 ok; 2 usage /
unknown tool / refused transition; 3 store unavailable.

Not an API and not an authorization system: whoever can run this on the host
can change the registry (as with the store file itself).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_civilization.core import tool_runtime  # noqa: E402
from agent_civilization.core.tool_registry import RegistryError, ToolNotFound  # noqa: E402
from agent_civilization.core.tool_registry_store import RegistryStoreError  # noqa: E402


def _tool_id(name: str) -> str:
    return name if name.startswith(tool_runtime.TOOL_PREFIX) else tool_runtime.tool_id_for(name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Shared tool registry admin (R-171, D-113)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    for name in ("disable", "enable"):
        p = sub.add_parser(name)
        p.add_argument("tool")
        p.add_argument("--actor", required=True)
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--json", action="store_true")
    p_audit.add_argument("--tool")
    args = ap.parse_args(argv)

    try:
        reg = tool_runtime.runtime_registry()
        if args.cmd == "list":
            rows = [{"tool_id": r.tool_id, "version": r.version,
                     "lifecycle_state": r.lifecycle_state, "health_status": r.health_status,
                     "test_status": r.test_status}
                    for r in reg.search(include_disabled=True, limit=10_000)]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for r in rows:
                    print(f"{r['tool_id']}@{r['version']}\t{r['lifecycle_state']}\t"
                          f"{r['health_status']}")
            return 0
        if args.cmd == "audit":
            entries = reg.audit()
            if args.tool:
                entries = [e for e in entries if e.get("tool_id") == _tool_id(args.tool)]
            if args.json:
                print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
            else:
                for e in entries:
                    print(f"{e['seq']}\t{e['op']}\t{e['actor']}\t{e['tool_id']}@{e['version']}")
            return 0
        if not args.actor.strip():
            print("error: --actor must not be empty", file=sys.stderr)
            return 2
        tid = _tool_id(args.tool)
        try:
            rec = reg.get(tid)
        except ToolNotFound:
            print(f"error: unknown tool: {args.tool}", file=sys.stderr)
            return 2
        target = "disabled" if args.cmd == "disable" else "registered"
        if rec.lifecycle_state == target:
            print(f"{tid}@{rec.version} already {target}")
            return 0
        reg.update_status(tid, rec.version, actor=args.actor.strip(), lifecycle_state=target)
        print(f"{tid}@{rec.version}: {rec.lifecycle_state} -> {target}")
        return 0
    except RegistryStoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except RegistryError as e:
        print(f"error: refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
