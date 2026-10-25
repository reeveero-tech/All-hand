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
import os
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
ATTEMPT_TIMEOUT_S = 600
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


def run_id(split: str, config: str, lang: str | None, tag: str = "") -> str:
    return f"{split}_{config}" + (f"_{lang}" if lang else "") + (f"_{tag}" if tag else "")


async def run(split: str, config: str, lang: str | None, tag: str = "") -> Path:
    from agent_civilization.core import llm as _llm
    from agent_civilization.eval import r169_bench_v5 as b
    if split == "test":
        raise SystemExit("refusing: the test split is not used for this pilot (insight leakage)")
    if not PREREG.exists() or subprocess.run(["git", "ls-files", "--error-unmatch", str(PREREG)], cwd=ROOT,
                                             capture_output=True).returncode:
        raise SystemExit("refusing: pre-registration not committed")
    rid = run_id(split, config, lang, tag)
    if os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("refusing: a paid key is set; this pilot uses the free keyless provider only")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    path = OUT / f"{rid}.jsonl"
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    if config == "grounded-v3-lang":
        b.GROUNDED_V2_SYSTEM = v3_system(b.GROUNDED_V2_SYSTEM)  # runtime only — file untouched
    _llm._keyless_post = b4.make_seeded_post("lang-pilot|" + rid)
    cases = [c for c in b.load_split(split) if lang is None or c["lang"] == lang]
    consent = _llm.set_public_llm_consent(True)  # synthetic benchmark fixtures only (as b4)
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
                    try:  # batch 13: a hang (e.g. sandbox suspension) becomes a recorded error, not a stall
                        rec = await asyncio.wait_for(v5._one(c, "baseline" if config == "baseline" else "grounded-v2"),
                                                     timeout=ATTEMPT_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        rec = {"id": c["id"], "reply": "", "error": f"attempt timeout > {ATTEMPT_TIMEOUT_S}s"}
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
    _llm.reset_public_llm_consent(consent)
    return path


def _scores(rid: str):
    from agent_civilization.eval import r169_bench_v5 as b
    p = OUT / f"{rid}.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    cases = {c["id"]: c for c in b.load_split(meta["split"])}
    return {json.loads(x)["id"]: v5.score_record(cases[json.loads(x)["id"]], json.loads(x)) for x in lines[1:] if x.strip()}


def score(tag: str = "r1") -> dict:
    """Paired comparison on validation for run tag ``tag`` (batch 13: tag + baseline + causes)."""
    from r169_language_analysis import classify
    from agent_civilization.eval import r169_bench_v5 as b
    cases = {c["id"]: c for c in b.load_split("validation")}
    runs = {cfg: _scores(f"validation_{cfg}_{tag}") for cfg in ("grounded-v2", "grounded-v3-lang", "baseline")
            if (OUT / f"validation_{cfg}_{tag}.jsonl").exists()}
    replies = {cfg: {json.loads(x)["id"]: json.loads(x) for x in
                     (OUT / f"validation_{cfg}_{tag}.jsonl").read_text(encoding="utf-8").splitlines()[1:] if x.strip()}
               for cfg in runs}

    def paired(a, c, key, sub=lambda k: True):
        ids = sorted(set(a) & set(c))
        both = [k for k in ids if sub(k) and not a[k].get("provider_error") and not c[k].get("provider_error")
                and a[k].get(key) is not None and c[k].get(key) is not None]
        a_only = sum(1 for k in both if a[k][key] and not c[k][key])
        c_only = sum(1 for k in both if c[k][key] and not a[k][key])
        return {"n": len(both), "a": sum(1 for k in both if a[k][key]), "b": sum(1 for k in both if c[k][key]),
                "a_only": a_only, "b_only": c_only, "mcnemar_exact_p": round(mcnemar_exact(a_only, c_only), 5)}

    res = {"tag": tag, "runs": {cfg: {"n": len(r), "provider_errors": sum(1 for v in r.values() if v.get("provider_error"))}
                                for cfg, r in runs.items()}, "comparisons": {}, "causes": {}}
    pairs = [("grounded-v2", "grounded-v3-lang"), ("baseline", "grounded-v3-lang"), ("baseline", "grounded-v2")]
    for a_cfg, c_cfg in pairs:
        if a_cfg in runs and c_cfg in runs:
            a, c = runs[a_cfg], runs[c_cfg]
            res["comparisons"][f"{a_cfg} (a) vs {c_cfg} (b)"] = {
                "language_match_ar": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "ar"),
                "language_match_en": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "en"),
                "pass": paired(a, c, "pass"),
                "pass_ar": paired(a, c, "pass", lambda k: cases[k]["lang"] == "ar"),
                "pass_en": paired(a, c, "pass", lambda k: cases[k]["lang"] == "en")}
    for cfg, r in runs.items():
        mism = [k for k, v in r.items() if v.get("language_match") is False and not v.get("provider_error")]
        causes = {}
        for k in mism:
            causes.setdefault(classify(cases[k], replies[cfg][k].get("reply") or ""), []).append(k)
        ie = [k for k, v in replies[cfg].items() if "INSUFFICIENT_EVIDENCE" in (v.get("reply") or "")]
        res["causes"][cfg] = {"mismatches": {c: len(v) for c, v in causes.items()}, "mismatch_ids": causes,
                              "insufficient_evidence_replies": len(ie),
                              "insufficient_evidence_then_other_language": len(causes.get("ar_q:token_then_english_explanation", []))}
    v = res["comparisons"].get("grounded-v2 (a) vs grounded-v3-lang (b)")
    if v:
        n_err = sum(res["runs"][x]["provider_errors"] for x in ("grounded-v2", "grounded-v3-lang"))
        ar, en, ps = v["language_match_ar"], v["language_match_en"], v["pass"]
        res["criteria"] = {"A1": ar["b"] > ar["a"] and ar["b_only"] - ar["a_only"] >= 3 and ar["a_only"] <= 1,
                           "A2": en["a_only"] - en["b_only"] <= 1, "A3": ps["a_only"] - ps["b_only"] <= 2,
                           "provider_error_share": round(n_err / max(2 * len(cases), 1), 4),
                           "inconclusive_by_errors": n_err > 0.10 * 2 * len(cases)}
    (OUT / f"validation_score_{tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    r.add_argument("--config", required=True, choices=["grounded-v2", "grounded-v3-lang", "baseline"])
    r.add_argument("--lang", choices=["ar", "en"])
    r.add_argument("--tag", default="")
    sc = sub.add_parser("score")
    sc.add_argument("--tag", default="r1")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        print(asyncio.run(run(a.split, a.config, a.lang, a.tag)))
    else:
        print(json.dumps(score(a.tag), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
