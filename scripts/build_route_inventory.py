#!/usr/bin/env python3
"""R-200 (M10/P0, D-150): inventory of every HTTP route and who really uses it.

Usage::

    python3 scripts/build_route_inventory.py <out.csv> [--live live.json]

Static part (reproducible): every ``@app.<method>("<path>")`` in server.py and
agent_civilization/civilization_routes.py, with its file:line, whether the
which frontend calls it:
the deployed UI (``web/src``) and the undeployed second UI (``src/``).

Live part (optional): ``--live`` is a JSON object {"<METHOD> <path>": <status
without a key>} measured against the deployed service (see the R-200 raw log);
it is merged as a column, never fetched here (no network in this script).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("server.py", "agent_civilization/civilization_routes.py")
ROUTE_RE = re.compile(r"""@app\.(get|post|put|patch|delete|websocket)\(\s*["']([^"']+)["']""")
COLUMNS = ["method", "path", "source", "used_by_web_ui", "used_by_src_ui",
           "live_status_no_key", "requires_key_live", "note"]


def _calls(front: Path) -> List[str]:
    out: List[str] = []
    for f in sorted(front.rglob("*")):
        if f.suffix in (".js", ".jsx", ".ts", ".tsx") and f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)       # block comments
            text = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", text)     # line comments
            out += re.findall(r"""[`'"](?:\$\{[^}]*\})?(/(?:api|civilization|health|metrics)[^`'"?$]*)""", text)
            # src/lib/api.ts: request<T>("/tasks") is fetched as `${base}${path}`
            # where base = the URL the user pasted (no /api suffix is added)
            out += re.findall(r"""request<[^>]*>\(\s*[`'"](/[^`'"?$]*)""", text)
    return out


def _match(route: str, calls: List[str]) -> bool:
    pat = "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(route)) + "/?$"
    if route == "/{full_path:path}":
        return False  # the SPA fallback serves the UI itself; not an API call
    for c in calls:
        c = re.sub(r"\$\{[^}]*\}", "X", c)
        if re.match(pat, c) or c.rstrip("/") == route.rstrip("/"):
            return True
        if "{" in route and c.startswith(route.split("{")[0]) and (
                c != route.split("{")[0] or c.endswith("/")):
            return True  # a template call such as `/api/agents/external/${name}`
    return False


def unmatched_calls(front: Path, rows: List[Dict[str, str]]) -> List[str]:
    """Frontend calls that match NO server route (a broken or dead call)."""
    routes = [r["path"] for r in rows]
    return sorted({c for c in _calls(front) if not any(_match(rt, [c]) for rt in routes)})


def build(root: Path, live: Optional[Dict[str, int]] = None) -> List[Dict[str, str]]:
    web, src = _calls(root / "web" / "src"), _calls(root / "src")
    rows = []
    for rel in SOURCES:
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = ROUTE_RE.search(line)
            if not m:
                continue
            method, path = m.group(1).upper(), m.group(2)
            key = f"{method} {path}"
            st = (live or {}).get(key)
            # auth is enforced by middleware/Security deps, so it is MEASURED
            # live (401 without a key), not guessed from the source.
            if st is None:
                req = "not probed" + (" (side effects: only GET is probed)" if method != "GET" else "")
            else:
                req = "yes" if st in (401, 403) else "no"
            rows.append({"method": method, "path": path, "source": f"{rel}:{i + 1}",
                         "used_by_web_ui": "yes" if _match(path, web) else "no",
                         "used_by_src_ui": "yes" if _match(path, src) else "no",
                         "live_status_no_key": "" if st is None else str(st),
                         "requires_key_live": req,
                         "note": "SPA fallback: serves the deployed web UI" if path == "/{full_path:path}" else ""})
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_csv")
    ap.add_argument("--live")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    live = json.loads(Path(a.live).read_text(encoding="utf-8")) if a.live else None
    rows = build(Path(a.root), live)
    with open(a.out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    used = sum(r["used_by_web_ui"] == "yes" for r in rows)
    orphan = unmatched_calls(Path(a.root) / "src", rows)
    print(json.dumps({"routes": len(rows), "used_by_web_ui": used,
                      "unused_by_web_ui": len(rows) - used,
                      "src_ui_calls_matching_no_route": orphan}, ensure_ascii=False))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
