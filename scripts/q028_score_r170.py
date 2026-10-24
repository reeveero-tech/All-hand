#!/usr/bin/env python3
"""Q-028 (R-170) — score a RETURNED human form against the agent's labels.

    python3 scripts/q028_score_r170.py path/to/returned_reviewer_form.json

Run ONLY after the returned form is committed unchanged (its sha256 recorded).
Reports human-vs-agent agreement per part with Cohen's kappa and every
disagreement. It never edits labels or thresholds; a disagreement is evidence
against the agent's label, not a reason to rewrite either side.
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "docs/audit/M8/q028/r170_key/KEY.json"
NORM = {"pass": "yes", "fail": "no", "match": "yes", "mismatch": "no", "yes": "yes", "no": "no"}


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def main(path: str) -> int:
    raw = Path(path).read_bytes()
    form = json.loads(raw)
    rv = form.get("reviewer", {})
    flags = ("confirmed_human", "confirmed_no_ai_tools", "confirmed_no_repository_access", "confirmed_no_prior_verdicts")
    key = {k["id"]: k for k in json.loads(KEY.read_text())}
    got = {c["id"]: c for c in form.get("cases", [])}
    out = {"form_sha256": hashlib.sha256(raw).hexdigest(), "reviewer_flags": {f: rv.get(f) for f in flags},
           "independent_human": all(rv.get(f) is True for f in flags),
           "missing": sorted(set(key) - set(got)), "parts": {}, "disagreements": [], "uncertain": []}
    by = collections.defaultdict(list)
    for hid, k in key.items():
        v = (got.get(hid) or {}).get("verdict", "").strip().lower()
        if v == "uncertain":
            out["uncertain"].append(hid)
        elif v in ("yes", "no"):
            agent = NORM[k["agent_label"]]
            by[k["part"]].append((agent, v))
            if agent != v:
                out["disagreements"].append({"id": hid, "part": k["part"], "source": k["source"], "case": k["id"],
                                             "agent": agent, "human": v, "note": got[hid].get("note", "")})
    for p, pairs in sorted(by.items()):
        out["parts"][p] = {"n": len(pairs), "agree": sum(a == b for a, b in pairs), "kappa": kappa(pairs)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
