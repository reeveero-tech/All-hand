#!/usr/bin/env python3
"""R-169 run ledger — OFFLINE, read-only inventory of EVERY committed R-169 run.

For each raw run file under ``docs/audit/M8/logs/raw/r169`` (all versions, incl.
superseded, aborted, stopped and duplicate runs) it records, mechanically:

* provider_called  — per record: did the runner actually get a provider response
  (reply received) / attempt and fail (provider error) / never call it
  (configuration error such as a missing key or missing keyless consent);
* completeness     — records vs the expected case count of the split (and lang filter);
* duplicates       — duplicate case ids inside a run, and reply-identity with an
  EARLIER run of the same (version, split, config) — byte-identical replies mean
  the sample is not independent (b3 vs b2 provider caching);
* provenance       — tree_dirty, dataset sha vs manifest, superseded marker;
* split role       — dev = development, validation = validation, test = held-out;
* valid_for_analysis + the reasons.

It does NOT call any provider, does NOT score anything (stored summaries are only
quoted) and never scores the stopped lang-pilot runs (PREREGISTRATION §8).

    python3 scripts/r169_run_ledger.py [--out-json PATH] [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "docs/audit/M8/logs/raw/r169"
BENCH = ROOT / "benchmarks/r169"
OUT_JSON = RAW / "RUN_LEDGER.json"

# Documented, run-specific context (each entry cites the committed document that
# establishes it). Mechanical checks below are independent of these notes.
DOC_NOTES = {
    "v5/test_grounded-v2_b3.jsonl": ("not_independent", "v5/b3/RESULT_B3.md: 118/118 replies byte-identical to b2r2 (provider cache)"),
    "v5/test_baseline_b3.jsonl": ("not_independent", "v5/b3/RESULT_B3.md: 118/118 replies byte-identical to b2 (provider cache)"),
    "v5/lang_pilot/dev_grounded-v3-lang_ar.jsonl": ("aborted", "lang_pilot/PREREGISTRATION.md §7: no keyless consent -> no provider call"),
    "v5/lang_pilot/validation_grounded-v2_r1.jsonl": ("stopped", "lang_pilot/PREREGISTRATION.md §8: stalled 32/46, kept unscored"),
    "v5/lang_pilot/validation_grounded-v2_r2.jsonl": ("stopped", "lang_pilot/PREREGISTRATION.md §8: parallel duplicate 11/46, stopped, kept unscored"),
}
NOT_CALLED = re.compile(r"not configured|no api key|consent|keyless.*disabled|no provider", re.I)
ERR_CLASSES = [
    ("config_no_provider", NOT_CALLED),
    ("timeout", re.compile(r"timeout|timed out", re.I)),
    ("rate_limit", re.compile(r"\b429\b|rate.?limit", re.I)),
    ("http_5xx", re.compile(r"\b5\d\d\b")),
    ("malformed", re.compile(r"malformed|json|decode", re.I)),
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def err_class(e: str) -> str:
    for name, rx in ERR_CLASSES:
        if rx.search(e):
            return name
    return "other_provider_error"


def version_of(rel: str, meta: dict) -> str:
    v = meta.get("benchmark_version") or meta.get("dataset_version")
    if v:
        return v.replace("r169-", "")
    m = re.match(r"(v\d)/", rel)
    return m.group(1) if m else "v1"


def expected_cases(version: str, split: str, lang):
    f = BENCH / version / f"{split}.jsonl"
    if not f.exists():
        return None
    cases = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    if lang:
        cases = [c for c in cases if c.get("lang") == lang]
    return len(cases)


def manifest_sha(version: str):
    f = BENCH / version / "manifest.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8")).get("dataset_sha256")


def load(path: Path):
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    meta = rows[0] if rows and rows[0].get("kind") == "meta" else {}
    body = [r for r in rows if r.get("kind") != "meta"]
    return meta, body


def summary_quote(path: Path):
    s = path.parent / (path.stem + ".summary.json")
    if not s.exists():
        return None
    try:
        d = json.loads(s.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — quoted only
        return {"file": s.relative_to(RAW).as_posix(), "parse_error": True}
    keep = {k: d[k] for k in ("n", "records", "pass_rate", "language_match", "superseded", "note") if k in d}
    keep["file"] = s.relative_to(RAW).as_posix()
    return keep


def classify_run(rel: str, meta: dict, body: list, earlier: dict) -> dict:
    split = meta.get("split") or ("test" if "/test_" in "/" + rel or rel.startswith("test_") else None)
    config = meta.get("config")
    version = version_of(rel, meta)
    lang = meta.get("lang") if meta.get("lang") not in (None, "None") else None
    exp = expected_cases(version, split, lang) if split else None
    ids = [r.get("id") for r in body]
    dup_ids = len(ids) - len(set(ids))
    replied = called_err = not_called = 0
    err_hist: dict = {}
    latencies = []
    for r in body:
        e = r.get("error")
        rep = r.get("reply")
        if e:
            c = err_class(str(e))
            err_hist[c] = err_hist.get(c, 0) + 1
            if c == "config_no_provider":
                not_called += 1
            else:
                called_err += 1
        elif rep is not None and str(rep).strip() != "":
            replied += 1
        else:
            called_err += 1  # call returned, empty reply
            err_hist["empty_reply"] = err_hist.get("empty_reply", 0) + 1
        if isinstance(r.get("latency_s"), (int, float)):
            latencies.append(r["latency_s"])
    reply_sha = {r.get("id"): sha((r.get("reply") or "").encode("utf-8")) for r in body if r.get("reply")}
    key = (version, split, config)
    identical_to = None
    max_identity = None
    for prev_rel, prev_sha in earlier.get(key, []):
        common = set(prev_sha) & set(reply_sha)
        if common:
            same = sum(prev_sha[i] == reply_sha[i] for i in common)
            cand = {"run": prev_rel, "identical": same, "compared": len(common)}
            if max_identity is None or (same / len(common), len(common)) > (
                    max_identity["identical"] / max_identity["compared"], max_identity["compared"]):
                max_identity = cand
            if identical_to is None and same / len(common) >= 0.95 and len(common) >= 10:
                identical_to = cand
    earlier.setdefault(key, []).append((rel, reply_sha))
    ms = manifest_sha(version)
    ds_ok = None if not meta.get("dataset_sha256") or not ms else meta["dataset_sha256"] == ms
    superseded = "superseded" in Path(rel).name
    doc = DOC_NOTES.get(rel)

    reasons = []
    if not body:
        reasons.append("no records")
    if body and replied == 0 and not_called == len(body):
        provider = "never_called"
        reasons.append("provider never called (configuration error on every record)")
    elif replied == 0:
        provider = "called_no_reply"
    else:
        provider = "called"
    if exp is not None and len(set(ids)) < exp:
        reasons.append(f"incomplete {len(set(ids))}/{exp}")
    if dup_ids:
        reasons.append(f"{dup_ids} duplicate case ids")
    if identical_to:
        reasons.append(f"replies byte-identical to earlier run {identical_to['run']} — not an independent sample")
    if superseded:
        reasons.append("marked superseded (pre-audit scorer)")
    if meta.get("tree_dirty") is True:
        reasons.append("tree_dirty=True at run time (development only)")
    if ds_ok is False:
        reasons.append("dataset sha does not match manifest")
    perr = called_err / len(body) if body else 0
    if perr > 0.10:
        reasons.append(f"provider errors {called_err}/{len(body)} > 10%")
    if doc and doc[0] in ("aborted", "stopped", "not_independent"):
        reasons.append(f"documented: {doc[1]}")
    blocking = [r for r in reasons if not r.startswith("tree_dirty")]
    if not blocking:
        status = "valid"
    elif identical_to or (doc and doc[0] == "not_independent"):
        status = "duplicate_not_independent"
    elif provider == "never_called":
        status = "no_provider_call"
    elif doc and doc[0] == "stopped" or (exp is not None and len(set(ids)) < exp):
        status = "incomplete_stopped"
    elif superseded:
        status = "superseded"
    elif perr > 0.10:
        status = "provider_error_heavy"
    else:
        status = "invalid_other"
    role = {"dev": "development", "validation": "validation", "test": "held-out test"}.get(split, "unknown")
    return {
        "run": rel, "version": version, "split": split, "split_role": role, "config": config,
        "lang_filter": lang, "started_utc": meta.get("started_utc"), "code_sha": meta.get("code_sha"),
        "provider_url": meta.get("provider_url"), "tree_dirty": meta.get("tree_dirty"),
        "dataset_sha_matches_manifest": ds_ok,
        "records": len(body), "unique_ids": len(set(ids)), "expected": exp,
        "provider": provider, "replied": replied, "provider_errors": called_err, "not_called": not_called,
        "error_classes": err_hist, "provider_error_rate": round(perr, 4),
        "provider_error_rate_ci95": wilson(called_err, len(body)),
        "median_latency_s": (sorted(latencies)[len(latencies) // 2] if latencies else None),
        "identical_to_earlier": identical_to, "max_reply_identity_vs_earlier": max_identity,
        "records_with_retries": sum(bool(r.get("retry_errors")) for r in body), "superseded": superseded,
        "status": status, "valid_for_analysis": status == "valid", "reasons": reasons,
        "stored_summary": summary_quote(RAW / rel),
        "file_sha256": sha((RAW / rel).read_bytes()) if (RAW / rel).exists() else None,
    }


def run_files():
    out = []
    for p in sorted(RAW.rglob("*.jsonl")):
        rel = p.relative_to(RAW).as_posix()
        if "/labels/" in rel or "human_review" in rel or "independent_review" in rel or "/blind" in rel:
            continue  # label / review sheets, not runs
        out.append(p)
    return sorted(out, key=lambda p: (load(p)[0].get("started_utc") or "", p.as_posix()))


def build() -> dict:
    earlier: dict = {}
    runs = []
    for p in run_files():
        meta, body = load(p)
        runs.append(classify_run(p.relative_to(RAW).as_posix(), meta, body, earlier))
    tally: dict = {}
    for r in runs:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    return {
        "generated_by": "scripts/r169_run_ledger.py (offline; no provider calls; no scoring)",
        "runs_total": len(runs), "status_counts": dict(sorted(tally.items())),
        "provider_called_runs": sum(r["provider"] == "called" for r in runs),
        "never_called_runs": sum(r["provider"] == "never_called" for r in runs),
        "runs": runs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--check", action="store_true", help="fail if the committed ledger is stale")
    a = ap.parse_args(argv)
    led = build()
    text = json.dumps(led, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    out = Path(a.out_json)
    if a.check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print("RUN_LEDGER.json is stale — rerun scripts/r169_run_ledger.py", file=sys.stderr)
            return 1
        print("ledger up to date")
        return 0
    out.write_text(text, encoding="utf-8")
    for r in led["runs"]:
        print(f"{r['status']:26} {r['provider']:13} {r['records']:>4}/{r['expected']!s:>4} err={r['provider_errors']:<3} {r['run']}")
    print(json.dumps({k: led[k] for k in ("runs_total", "status_counts", "provider_called_runs", "never_called_runs")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
