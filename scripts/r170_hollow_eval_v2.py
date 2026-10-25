#!/usr/bin/env python3
"""R-170 batch 13 — task_fit v2 evaluation on the NEW corpus (pre-registered:
docs/audit/M8/logs/raw/r170/hollow/PREREGISTRATION_V2.md).

    python3 scripts/r170_hollow_eval_v2.py run --split dev|validation|test2 --tag T
    python3 scripts/r170_hollow_eval_v2.py score <stem>

The ORACLE judges the correctness of the OUTPUT against the case truth and
never calls core/task_fit or the designer's colour vocabulary.
"""
from __future__ import annotations

import argparse
import asyncio
import colorsys
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from r170_hollow_eval import wilson  # noqa: E402

CASES = ROOT / "benchmarks/r170/hollow_v2/cases.jsonl"
OUT = ROOT / "docs/audit/M8/logs/raw/r170/hollow"
PINNED_SHA = "3dedbbce99d41783feebab622db634715c765b3be1484e85ad9a722ab3097e69"
HUE = {"red": 0, "orange": 30, "purple": 275, "teal": 180}  # independent reference table


def _hue_hex(h: str) -> float:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def _n(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def oracle(c: dict, res: dict):
    t = c["truth"]
    if "prose_ok" in t:
        return bool(t["prose_ok"])
    to = res.get("tool_output") or res.get("partial_tool_output") or {}
    fam = c["family"]
    if fam == "st_generative_limit" or t.get("explanation") or t.get("unsupported"):
        return False
    if c["spec"] == "strategist":
        return [_n(s.get("goal")) for s in to.get("roadmap") or []] == [_n(o) for o in t["objectives"]]
    if c["spec"] == "architect":
        return set(t["components"]) <= {x.get("name") for x in to.get("components") or []}
    if c["spec"] == "designer":
        prim = (to.get("palette") or {}).get("primary")
        if not prim:
            return False
        want = _hue_hex(t["colour"]) if t["colour"].startswith("#") else HUE[t["colour"]]
        d = abs((_hue_hex(prim) - want + 180) % 360 - 180)
        return d <= 25 and to.get("theme", "light") == t["theme"]
    if c["spec"] == "reviewer":
        kinds = {f.get("kind") for f in to.get("findings") or []}
        return (not kinds) if not t["defects"] else set(t["defects"]) <= kinds
    return None


def load(split):
    raw = CASES.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_SHA:
        raise SystemExit("corpus sha mismatch with PREREGISTRATION_V2")
    return [c for c in (json.loads(x) for x in raw.decode().splitlines()) if c["split"] == split]


async def run(split, tag):
    os.environ["AGOS_OUTPUT_VALIDATORS"] = "1"
    from agent_civilization.core.agents.real_dispatcher import _HANDLERS
    from agent_civilization.core.output_validators import apply_output_verification, requirements_for
    path = OUT / f"v2_{split}_{tag}.jsonl"
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "agent_civilization"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip())
    rows = []
    for c in load(split):
        work = json.loads(json.dumps(c["work"]))
        req = requirements_for(c["spec"], work)
        if "prose_result" in c:
            res, off = dict(c["prose_result"]), None
        else:
            res = await _HANDLERS[c["spec"]](work)
            off = bool(res.get("ok"))
        on_ok, on_res = apply_output_verification(req, bool(res.get("ok")), res)
        rows.append({"id": c["id"], "spec": c["spec"], "lang": c["lang"], "family": c["family"],
                     "correct": oracle(c, res), "gate_off_ok": off, "gate_on_ok": bool(on_ok),
                     "why": res.get("error") or (on_res.get("verification") or {}).get("reasons"),
                     "answer": str(res.get("answer") or res.get("partial_answer") or "")[:240]})
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "split": split, "tag": tag, "code_sha": sha, "code_dirty": dirty,
                            "cases_sha256": PINNED_SHA, "n": len(rows)}) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def score(stem):
    rows = [json.loads(x) for x in (OUT / f"{stem}.jsonl").read_text(encoding="utf-8").splitlines()[1:]]
    def rate(sel, pred):
        k = sum(1 for r in sel if pred(r)); n = len(sel)
        return {"k": k, "n": n, "wilson95": wilson(k, n)}
    out = {"stem": stem}
    for mode in ("gate_on_ok", "gate_off_ok"):
        sel = [r for r in rows if r[mode] is not None]
        ok = [r for r in sel if r["correct"]]; bad = [r for r in sel if r["correct"] is False]
        det = [r for r in bad if not r["family"].endswith("_limit")]
        m = {"FR": rate(ok, lambda r: not r[mode]), "HA_detectable": rate(det, lambda r: r[mode]),
             "HA_limit": rate([r for r in bad if r["family"].endswith("_limit")], lambda r: r[mode]),
             "FR_ar_correct_prose": rate([r for r in ok if r["lang"] == "ar" and r["family"] in
                                          ("prose_list_ok", "prose_short_ok")], lambda r: not r[mode]),
             "by_lang": {lg: {"FR": rate([r for r in ok if r["lang"] == lg], lambda r: not r[mode]),
                              "HA_detectable": rate([r for r in det if r["lang"] == lg], lambda r: r[mode])}
                         for lg in ("ar", "en")},
             "by_spec": {s: {"FR": rate([r for r in ok if r["spec"] == s], lambda r: not r[mode]),
                             "HA_detectable": rate([r for r in det if r["spec"] == s], lambda r: r[mode])}
                         for s in ("strategist", "architect", "designer", "reviewer")},
             "by_family": {fm: rate([r for r in sel if r["family"] == fm], lambda r: r[mode] == bool(r["correct"]))
                           for fm in sorted({r["family"] for r in sel})},
             "false_rejections": [{"id": r["id"], "why": r["why"]} for r in ok if not r[mode]],
             "hollow_accepted": [{"id": r["id"], "family": r["family"], "answer": r["answer"][:120]}
                                 for r in bad if r[mode]]}
        out[mode.replace("_ok", "")] = m
    (OUT / f"{stem}.score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--split", required=True, choices=["dev", "validation", "test2"])
    r.add_argument("--tag", required=True); sub.add_parser("score").add_argument("stem")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        print(asyncio.run(run(a.split, a.tag)))
    else:
        s = score(a.stem)
        for m in ("gate_on", "gate_off"):
            print(m, {k: (v["k"], v["n"]) for k, v in s[m].items() if isinstance(v, dict) and "k" in v})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
