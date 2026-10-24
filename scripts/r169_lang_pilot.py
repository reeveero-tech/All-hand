#!/usr/bin/env python3
"""R-169 language pilot (R-170 batch 12) — candidate prompt fix for the Arabic
language regression of grounded-v2, measured on dev/validation ONLY.

The cause (scripts/r169_language_analysis.py on b2 + b4s test replies): Arabic
questions answered "INSUFFICIENT_EVIDENCE: The provided source does not ..." —
the English token primes an English explanation. Because the cause was found by
reading TEST replies, the test split is not used to judge the fix (insight
leakage). Pre-registration: docs/audit/M8/logs/raw/r169/v5/lang_pilot/PREREGISTRATION.md.

Nothing frozen is edited: ``grounded-v3-lang`` = the frozen grounded-v2 messages
with rule 9 replaced AT RUNTIME (module attribute swap inside this process);
every keyless request carries a seed (b4 method) so no provider cache is reused.

    python3 scripts/r169_lang_pilot.py run --split dev|validation --config grounded-v2|grounded-v3-lang [--lang ar]
    python3 scripts/r169_lang_pilot.py score
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import r169_run_v5 as v5  # noqa: E402
import r169_run_b4 as b4  # noqa: E402
from r169_language_analysis import mcnemar_exact  # noqa: E402

OUT = ROOT / "docs/audit/M8/logs/raw/r169/v5/lang_pilot"
PREREG = OUT / "PREREGISTRATION.md"
RULE9_V2_PREFIX = "9. Write the ENTIRE reply in the language of the question"
RULE9_V3 = (
    "9. Write the ENTIRE reply in the language of the question: Arabic if the question is Arabic, English "
    "if the question is English. The INSUFFICIENT_EVIDENCE token itself is the ONLY English allowed in an "
    "Arabic reply: everything after it (the explanation) MUST be Arabic. Example for an Arabic question: "
    "\"INSUFFICIENT_EVIDENCE — المصادر المتاحة لا تذكر هذه المعلومة.\" Never write an English sentence in "
    "reply to an Arabic question.\n"
)


def v3_system(v2_system: str) -> str:
    start = v2_system.index(RULE9_V2_PREFIX)
    end = v2_system.index("\n", start) + 1
    return v2_system[:start] + RULE9_V3 + v2_system[end:]


def run_id(split: str, config: str, lang: str | None) -> str:
    return f"{split}_{config}" + (f"_{lang}" if lang else "")


async def run(split: str, config: str, lang: str | None) -> Path:
    from agent_civilization.core import llm as _llm
    from agent_civilization.eval import r169_bench_v5 as b
    if split == "test":
        raise SystemExit("refusing: the test split is not used for this pilot (insight leakage)")
    if not PREREG.exists() or subprocess.run(["git", "ls-files", "--error-unmatch", str(PREREG)], cwd=ROOT,
                                             capture_output=True).returncode:
        raise SystemExit("refusing: pre-registration not committed")
    rid = run_id(split, config, lang)
    path = OUT / f"{rid}.jsonl"
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    if config == "grounded-v3-lang":
        b.GROUNDED_V2_SYSTEM = v3_system(b.GROUNDED_V2_SYSTEM)  # runtime only — file untouched
    _llm._keyless_post = b4.make_seeded_post("lang-pilot|" + rid)
    cases = [c for c in b.load_split(split) if lang is None or c["lang"] == lang]
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "split": split, "config": config, "lang": lang, "cases": len(cases),
                            "code_sha": sha, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "system_prompt_sha256": __import__("hashlib").sha256(
                                b.GROUNDED_V2_SYSTEM.encode()).hexdigest()}) + "\n")
        for i, c in enumerate(cases, 1):
            rec, history = None, []
            b4.CALLS.clear()
            ctx = b4._case.set(c["id"])
            try:
                for attempt in range(3):
                    rec = await v5._one(c, "grounded-v2")
                    if not rec.get("error"):
                        break
                    history.append(rec.get("error"))
                    await asyncio.sleep(30 * (attempt + 1))
            finally:
                b4._case.reset(ctx)
            rec["config"], rec["calls"], rec["retry_errors"] = config, list(b4.CALLS), history
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            hits = sum(1 for x in rec["calls"] if (x.get("x_cache") or "").upper() == "HIT")
            print(f"[{i}/{len(cases)}] {c['id']} calls={len(rec['calls'])} hits={hits} err={'y' if rec.get('error') else 'n'}", flush=True)
    return path


def _scores(rid: str):
    from agent_civilization.eval import r169_bench_v5 as b
    p = OUT / f"{rid}.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    cases = {c["id"]: c for c in b.load_split(meta["split"])}
    return {json.loads(x)["id"]: v5.score_record(cases[json.loads(x)["id"]], json.loads(x)) for x in lines[1:] if x.strip()}


def score() -> dict:
    a, c = _scores("validation_grounded-v2"), _scores("validation_grounded-v3-lang")
    ids = sorted(set(a) & set(c))
    def paired(key, sub=lambda k: True):
        both = [k for k in ids if sub(k) and a[k].get(key) is not None and c[k].get(key) is not None]
        v2_only = sum(1 for k in both if a[k][key] and not c[k][key])
        v3_only = sum(1 for k in both if c[k][key] and not a[k][key])
        return {"n": len(both), "v2": sum(1 for k in both if a[k][key]), "v3": sum(1 for k in both if c[k][key]),
                "v2_only": v2_only, "v3_only": v3_only, "mcnemar_exact_p": round(mcnemar_exact(v2_only, v3_only), 5)}
    res = {"language_match_ar": paired("language_match", lambda k: a[k]["lang"] == "ar"),
           "language_match_en": paired("language_match", lambda k: a[k]["lang"] == "en"),
           "pass": paired("pass"),
           "provider_errors": {"v2": sum(1 for k in a if a[k].get("provider_error")),
                               "v3": sum(1 for k in c if c[k].get("provider_error"))}}
    (OUT / "validation_score.json").write_text(json.dumps(res, indent=1) + "\n")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    r.add_argument("--config", required=True, choices=["grounded-v2", "grounded-v3-lang"])
    r.add_argument("--lang", choices=["ar", "en"])
    sub.add_parser("score")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        print(asyncio.run(run(a.split, a.config, a.lang)))
    else:
        print(json.dumps(score(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
