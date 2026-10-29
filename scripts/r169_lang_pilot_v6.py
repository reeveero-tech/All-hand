#!/usr/bin/env python3
"""R-170 لاحق 72 — v6 pilot: language-fix candidate v4 judged on the v6 holdout.

Why v4 (log 296 root cause): the failed v3 candidate's rule 9 carried an Arabic
example only and an Arabic-focused "never write English" instruction, and it
was developed on Arabic dev only — so it over-generalized to English questions
(A2 failed: 3 English questions answered in Arabic/mixed). The v4 candidate
REBALANCES rule 9: symmetric instructions and BOTH examples (Arabic for Arabic
questions, English for English questions), and it is developed on BOTH languages
of the v5 dev split before being frozen and judged on the NEW v6 holdout
(46 unseen cases, per log 296: "judged on new unseen cases (v6 holdout)").

Nothing frozen is edited: ``grounded-v4-lang`` = the frozen grounded-v2 messages
with rule 9 replaced AT RUNTIME (module attribute swap inside this process);
every keyless request carries a seed (b4 method) so no provider cache is reused.

Pre-registration (committed before any run):
docs/audit/M8/logs/raw/r169/v6/lang_pilot/PREREGISTRATION_V6.md

    python3 scripts/r169_lang_pilot_v6.py run --split dev --arms v4      # development (v5 dev)
    python3 scripts/r169_lang_pilot_v6.py run --split dev --arms v2,v4   # development, paired
    python3 scripts/r169_lang_pilot_v6.py run --split holdout             # judgment (v6, interleaved 3 arms)
    python3 scripts/r169_lang_pilot_v6.py score                            # after the holdout run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import r169_run_v5 as v5run  # noqa: E402  (version-agnostic runner + score_record)
import r169_run_b4 as b4  # noqa: E402  (seeded keyless post + CALLS context)
from r169_language_analysis import mcnemar_exact  # noqa: E402

OUT = ROOT / "docs/audit/M8/logs/raw/r169/v6/lang_pilot"
PREREG = OUT / "PREREGISTRATION_V6.md"
PREREG_V8 = ROOT / "docs/audit/M8/logs/raw/r169/v8/lang_pilot/PREREGISTRATION_V8.md"
OUT_V8 = PREREG_V8.parent
PREREG_V9 = ROOT / "docs/audit/M8/logs/raw/r169/v9/lang_pilot/PREREGISTRATION_V9.md"
OUT_V9 = PREREG_V9.parent
PREREG_V10 = ROOT / "docs/audit/M8/logs/raw/r169/v10/lang_pilot/PREREGISTRATION_V10.md"
OUT_V10 = PREREG_V10.parent


def _out(dataset: str) -> Path:
    """مسار المخرجات لكل حزمة — تشغيل/تقييم v8/v9/v10 يكتب في دليله حصرًا،
    فلا يمكنه استبدال أدلة الأحكام التاريخية في دليل v6 (مراجعة مستقلة)."""
    if dataset == "v8":
        return OUT_V8
    if dataset == "v9":
        return OUT_V9
    if dataset == "v10":
        return OUT_V10
    return OUT


def _prereg_for(dataset: str) -> Path:
    return {"v8": PREREG_V8, "v9": PREREG_V9, "v10": PREREG_V10}.get(dataset, PREREG)


ATTEMPT_TIMEOUT_S = 600
ARMS = ("grounded-v2", "grounded-v4-lang", "baseline")
RULE9_V2_PREFIX = "9. Write the ENTIRE reply in the language of the question"

# v4 — rebalanced rule 9 (symmetric instructions AND both examples; the v3
# failure mode was an Arabic-only example plus an Arabic-focused prohibition).
# REVISION 1 (after the dev inspection, prereg §3 allows ONE revision): the
# first v4 draft leaked the ENGLISH example into Arabic replies (4/26 dev
# Arabic mismatches — the model copied "The available sources do not mention…"
# verbatim for Arabic questions). This revision forces example selection:
# "If the question is X … use ONLY this example … never the other language's".
RULE9_V4 = (
    "9. Write the ENTIRE reply in the language of the question: Arabic if the question is Arabic, English "
    "if the question is English. Never mix the two languages in one reply. The INSUFFICIENT_EVIDENCE "
    "token stays in English in every reply. If the question is Arabic, everything after the token MUST "
    "be Arabic — use ONLY this example: \"INSUFFICIENT_EVIDENCE — المصادر المتاحة لا تذكر هذه المعلومة.\" "
    "If the question is English, everything after the token MUST be English — use ONLY this example: "
    "\"INSUFFICIENT_EVIDENCE — The available sources do not mention this fact.\" Never use the example "
    "of the other language.\n"
)


def v4_system(v2_system: str) -> str:
    start = v2_system.index(RULE9_V2_PREFIX)
    end = v2_system.index("\n", start) + 1
    return v2_system[:start] + RULE9_V4 + v2_system[end:]


def _bench_for(split: str, dataset: str = "v7"):
    """dev uses the v5 bench (the tuning surface); holdout uses the judgment
    bench of the batch: v7 = دفعة 14؛ v8 = دفعة 16 (انحراف ترتيب موثق —
    PREREGISTRATION_V9 §0، لا تُستخدم للحكم)؛ v9 = حزمة الحكم المطابقة
    (PREREGISTRATION_V9 §4). v10 = حزمة الحكم المُكرَّرة بقوة كافية
    (PREREGISTRATION_V10 §2/§3 — دفعة 18)."""
    if split == "dev":
        from agent_civilization.eval import r169_bench_v5 as b
        return b
    if split == "holdout":
        import importlib
        mod = {"v7": "r169_bench_v7", "v8": "r169_bench_v8",
               "v9": "r169_bench_v9", "v10": "r169_bench_v10"}[dataset]
        return importlib.import_module(f"agent_civilization.eval.{mod}")
    raise SystemExit(f"refusing: split {split!r} is not dev or holdout")


def _prereg_committed() -> bool:
    """The prereg must be COMMITTED (its current content must match HEAD) —
    merely being tracked is not enough (advisor review: `git ls-files` only
    proves tracking, not that the on-disk content is what was frozen).

    All four preregistrations are guarded: V6 (the original pilot prereg),
    V8 (kept with its documented order deviation — never used for judgment)
    and V9 (the compliant judgment package — PREREGISTRATION_V9 governs the
    v9 judgment). A run must not start if ANY file was edited after commit."""
    ok = True
    for prereg in (PREREG, PREREG_V8, PREREG_V9, PREREG_V10):
        if not prereg.exists():
            ok = False
            continue
        r = subprocess.run(["git", "ls-files", "--error-unmatch", str(prereg)],
                           cwd=ROOT, capture_output=True)
        if r.returncode:
            ok = False
            continue
        head = subprocess.run(["git", "show", f"HEAD:{prereg.relative_to(ROOT)}"],
                               cwd=ROOT, capture_output=True)
        if head.returncode or head.stdout != prereg.read_bytes():
            ok = False
    return ok


async def _one_arm_case(bench, arm: str, case: dict) -> dict:
    """Run ONE case for ONE arm with the arm's system prompt swapped at runtime.

    CRITICAL: the swap MUST happen on ``r169_bench_v5`` — the module that
    ``r169_run_v5._one`` actually imports and reads at call time — regardless
    of which bench loaded the cases (v5 for dev, v6/v7 for the holdout). The
    advisor review caught that swapping the *case-loading* bench (e.g. v6) has
    no effect on the prompt that ``_one`` builds.
    """
    from agent_civilization.eval import r169_bench_v5 as exec_bench
    from agent_civilization.core import llm as _llm_policy
    original = exec_bench.GROUNDED_V2_SYSTEM
    # v4r (دفعة 16): نفس موجّه v4-revised + سياسة «إعادة محدودة عند اختلاف اللغة»
    # في مسار الاستدعاء الفعلي (contextvar لكل استدعاء — asyncio-safe). الأذرع
    # الأخرى تعطّل السياسة صراحةً (لا اعتماد على البيئة) حتى يكون الفرق الوحيد
    # بين v4 وv4r هو السياسة نفسها (ذراع الضبط — prereg v8 §3).
    retry_token = _llm_policy.set_llm_lang_retry(arm == "grounded-v4r-lang")
    # حاوية الالتقاط قبل wait_for (إصلاح 3.11 — السجل 317): بدونها تضيع
    # metadata الإعادة عند حد المهمة الذي ينشئه wait_for في Python 3.11.
    capture_token = _llm_policy.begin_lang_retry_capture()
    try:
        if arm in ("grounded-v4-lang", "grounded-v4r-lang"):
            exec_bench.GROUNDED_V2_SYSTEM = v4_system(original)
        rec, history = None, []
        b4.CALLS.clear()
        ctx = b4._case.set(case["id"])
        try:
            for attempt in range(3):
                # metadata المحاولة الأخيرة فقط: تفريغ حاوية الالتقاط قبل كل محاولة
                _llm_policy.get_last_lang_retry()
                try:
                    rec = await asyncio.wait_for(
                        v5run._one(case, "baseline" if arm == "baseline" else "grounded-v2"),
                        timeout=ATTEMPT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    rec = {"id": case["id"], "reply": "",
                           "error": f"attempt timeout > {ATTEMPT_TIMEOUT_S}s"}
                if not rec.get("error"):
                    break
                history.append(rec.get("error"))
                await asyncio.sleep(30 * (attempt + 1))
        finally:
            b4._case.reset(ctx)
        rec["config"], rec["calls"], rec["retry_errors"] = arm, list(b4.CALLS), history
        if arm == "grounded-v4r-lang":
            # دمج لا استبدال: metadata الطبقة الدنيا (attempts/first_reply_sha256/
            # reason عند انطلاق الإعادة فعلاً) تُحفظ في الصف — وجود policy/target
            # وحدهما لا يثبت أن بيانات الإعادة الكاملة تُحفظ (مراجعة مستقلة).
            from agent_civilization.core import lang_policy
            rec["lang_retry"] = {"policy": "bounded-retry-v1",
                                  "target": lang_policy.target_language(case.get("question", "")),
                                  **(_llm_policy.get_last_lang_retry() or {})}
        return rec
    finally:
        exec_bench.GROUNDED_V2_SYSTEM = original
        _llm_policy.end_lang_retry_capture(capture_token)
        _llm_policy.reset_llm_lang_retry(retry_token)


# أذرع الحكم لكل حزمة (interleaved — نفس الحالات): v7 = دفعة 14 (3 أذرع مع
# baseline)؛ v8 = دفعة 16 (v2 + v4 ضبط + v4r المرشح — prereg v8 §3).
JUDGMENT_ARMS = {"v7": ("grounded-v2", "grounded-v4-lang", "baseline"),
                 "v8": ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang"),
                 "v9": ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang"),
                 "v10": ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang")}


async def run(split: str, arms: list[str], tag: str = "",
              dataset: str = "v7") -> list[Path]:
    from agent_civilization.core import llm as _llm
    bench = _bench_for(split, dataset)
    if not _prereg_committed():
        raise SystemExit("refusing: PREREGISTRATION_V6.md is not committed (run nothing before the prereg)")
    if split == "holdout" and set(arms) != set(JUDGMENT_ARMS[dataset]):
        raise SystemExit(f"refusing: the {dataset} holdout judgment needs ALL of "
                         f"{JUDGMENT_ARMS[dataset]} interleaved (got {arms})")
    if os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("refusing: a paid key is set; this pilot uses the free keyless provider only")
    os.environ["AGOS_LLM_KEYLESS"] = "1"
    cases = bench.load_split(split)
    # interleaved, preregistered order: for case i, all arms before case i+1 —
    # separates arm identity from time drift (prereg §5).
    paths, handles = {}, {}
    for arm in arms:
        suffix = f"_{tag}" if tag else ""
        p = _out(dataset) / f"{split}_{arm}{suffix}.jsonl"
        if p.exists():
            raise SystemExit(f"refusing to overwrite {p}")
        paths[arm] = p
        handles[arm] = p.open("w", encoding="utf-8")
        handles[arm].write(json.dumps(
            {"kind": "meta", "split": split, "config": arm, "cases": len(cases),
             "code_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                        capture_output=True, text=True).stdout.strip(),
             "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "system_prompt_sha256": hashlib.sha256(
                 (v4_system(bench.GROUNDED_V2_SYSTEM) if arm in ("grounded-v4-lang", "grounded-v4r-lang")
                  else bench.GROUNDED_V2_SYSTEM).encode()).hexdigest(),
             "prereg": str(_prereg_for(dataset).relative_to(ROOT))}) + "\n")
    _llm._keyless_post = b4.make_seeded_post(f"lang-pilot-v6|{split}")
    consent = _llm.set_public_llm_consent(True)  # synthetic benchmark fixtures only (as b4)
    try:
        for i, case in enumerate(cases, 1):
            for arm in arms:
                rec = await _one_arm_case(bench, arm, case)
                handles[arm].write(json.dumps(rec, ensure_ascii=False) + "\n")
                handles[arm].flush()
                hits = sum(1 for x in rec["calls"] if (x.get("x_cache") or "").upper() == "HIT")
                print(f"[{i}/{len(cases)}] {arm.split('-')[-1]} {case['id']} "
                      f"calls={len(rec['calls'])} hits={hits} err={'y' if rec.get('error') else 'n'}",
                      flush=True)
                await asyncio.sleep(6)  # provider politeness (b4 spacing)
    finally:
        for h in handles.values():
            h.close()
        _llm.reset_public_llm_consent(consent)
    return [paths[a] for a in arms]


def _scores(split: str, arm: str, tag: str = "", dataset: str = "v7") -> dict:
    """Score one arm's run. REFUSES: a run whose row ids are not EXACTLY the
    frozen split's id set (duplicates, missing cases, forged metadata counts
    and foreign ids all fail — advisor review)."""
    bench = _bench_for(split, dataset)
    suffix = f"_{tag}" if tag else ""
    p = _out(dataset) / f"{split}_{arm}{suffix}.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    n_declared = meta["cases"]
    rows = [json.loads(x) for x in lines[1:] if x.strip()]
    if len(rows) != n_declared:
        raise SystemExit(f"refusing: {p.name} has {len(rows)}/{n_declared} cases — incomplete run")
    row_ids = [r["id"] for r in rows]
    if len(row_ids) != len(set(row_ids)):
        dupes = sorted({x for x in row_ids if row_ids.count(x) > 1})
        raise SystemExit(f"refusing: {p.name} has duplicate case ids: {dupes}")
    cases = {c["id"]: c for c in bench.load_split(split)}
    frozen_ids = set(cases)
    if set(row_ids) != frozen_ids:
        missing = sorted(frozen_ids - set(row_ids))
        foreign = sorted(set(row_ids) - frozen_ids)
        raise SystemExit(f"refusing: {p.name} ids do not match the frozen split — "
                         f"missing={missing[:3]}… foreign={foreign[:3]}…")
    return {r["id"]: v5run.score_record(cases[r["id"]], r) for r in rows}


def score(tag: str = "", dataset: str = "v7") -> dict:
    """Paired comparison on the judgment holdout (v7: prereg v6 §6+§5.1;
    v8: PREREGISTRATION_V8 §6 — المقارنة الأساسية v4r مقابل v4)."""
    bench = _bench_for("holdout", dataset)
    cases = {c["id"]: c for c in bench.load_split("holdout")}
    runs = {arm: _scores("holdout", arm, tag, dataset)
            for arm in JUDGMENT_ARMS[dataset]}

    def paired(a, c, key, sub=lambda k: True):
        ids = sorted(set(a) & set(c))
        both = [k for k in ids if sub(k) and not a[k].get("provider_error") and not c[k].get("provider_error")
                and a[k].get(key) is not None and c[k].get(key) is not None]
        a_only = sum(1 for k in both if a[k][key] and not c[k][key])
        c_only = sum(1 for k in both if c[k][key] and not a[k][key])
        return {"n": len(both), "a": sum(1 for k in both if a[k][key]), "b": sum(1 for k in both if c[k][key]),
                "a_only": a_only, "b_only": c_only, "mcnemar_exact_p": round(mcnemar_exact(a_only, c_only), 5)}

    res = {"runs": {arm: {"n": len(r), "provider_errors": sum(1 for v in r.values() if v.get("provider_error")),
                          "language_match_none": sum(1 for v in r.values() if v.get("language_match") is None)}
                    for arm, r in runs.items()}, "comparisons": {}}
    if dataset == "v7":
        pairs = [("grounded-v2", "grounded-v4-lang"), ("baseline", "grounded-v4-lang"),
                 ("baseline", "grounded-v2")]
    else:
        pairs = [("grounded-v2", "grounded-v4-lang"), ("grounded-v4-lang", "grounded-v4r-lang"),
                 ("grounded-v2", "grounded-v4r-lang")]
    for a_cfg, c_cfg in pairs:
        a, c = runs[a_cfg], runs[c_cfg]
        res["comparisons"][f"{a_cfg} (a) vs {c_cfg} (b)"] = {
            "language_match_ar": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "ar"),
            "language_match_en": paired(a, c, "language_match", lambda k: cases[k]["lang"] == "en"),
            "pass": paired(a, c, "pass"),
            "pass_ar": paired(a, c, "pass", lambda k: cases[k]["lang"] == "ar"),
            "pass_en": paired(a, c, "pass", lambda k: cases[k]["lang"] == "en")}
    v = res["comparisons"]["grounded-v2 (a) vs grounded-v4-lang (b)"]
    ar, en, ps = v["language_match_ar"], v["language_match_en"], v["pass"]
    n_err = sum(res["runs"][x]["provider_errors"] for x in ("grounded-v2", "grounded-v4-lang"))
    n_cases = len(cases)
    if dataset == "v7":
        res["criteria"] = {"A1": ar["b"] > ar["a"] and ar["b_only"] - ar["a_only"] >= 3 and ar["a_only"] <= 1,
                          "A2": en["a_only"] - en["b_only"] <= 1,
                          "A3": ps["a_only"] - ps["b_only"] <= 2,
                          "provider_error_share": round(n_err / max(2 * n_cases, 1), 4),
                          "inconclusive_by_errors": n_err > 0.10 * 2 * n_cases}
    else:
        # v8 (PREREGISTRATION_V8 §6 + تصحيح مؤرخ 2026-10-28 قبل أي نتيجة v8):
        # المقارنة الأساسية v4r مقابل v4 (ذراع الضبط)، مع v2 مرجعًا تاريخيًا
        # متزامنًا. شرط التاريخ الأصلي في التسجيل (v2_only − v4r_only ≥ 2) كان
        # متناقضًا رياضيًا مع v4r > v2 (لا يجتمعان) — صُحح الاتجاه إلى
        # v4r_only − v2_only ≥ 2 (تفوق v4r بهامش حالتين) دون تغيير الهوامش،
        # والتصحيح موثق مؤرخًا في التسجيل §6 مع حفظ النص الأصلي.
        c = res["comparisons"]["grounded-v4-lang (a) vs grounded-v4r-lang (b)"]
        car, cen, cps = c["language_match_ar"], c["language_match_en"], c["pass"]
        h = res["comparisons"]["grounded-v2 (a) vs grounded-v4r-lang (b)"]
        har, hps = h["language_match_ar"], h["pass"]
        n_err8 = sum(res["runs"][x]["provider_errors"] for x in
                     ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang"))
        res["criteria"] = {
            "B1": (car["b"] > car["a"] and car["b_only"] - car["a_only"] >= 3
                   and car["a_only"] <= 1 and har["b"] > har["a"]
                   and har["b_only"] - har["a_only"] >= 2),
            "B2": cen["a_only"] - cen["b_only"] <= 1,
            "B3": cps["a_only"] - cps["b_only"] <= 2 and hps["a_only"] - hps["b_only"] <= 2,
            "provider_error_share": round(n_err8 / max(3 * n_cases, 1), 4),
            # الشرط المسجل: «أخطاء مزود تتجاوز 10% في أي ذراع» — لكل ذراع على
            # حدة لا متوسط الأذرع (تصحيح المراجعة المستقلة الثانية).
            "per_arm_provider_errors": {x: res["runs"][x]["provider_errors"] for x in
                                        ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang")},
            "inconclusive_by_errors": any(
                res["runs"][x]["provider_errors"] > 0.10 * n_cases
                for x in ("grounded-v2", "grounded-v4-lang", "grounded-v4r-lang"))}
        # قرار ثلاثي الحالات — عدم الحسم له الأولوية (مراجعة مستقلة):
        # أخطاء المزود > 10% ⇒ «غير حاسم» لا «لم يثبت».
        cr = res["criteria"]
        res["criteria"]["decision"] = ("inconclusive" if cr["inconclusive_by_errors"] else
                                 ("candidate promising" if (cr["B1"] and cr["B2"] and cr["B3"])
                                  else "not proven"))
        if dataset == "v10":
            # PREREGISTRATION_V10 §4 — قاعدة التغطية الدنيا (مراجعة مستقلة على
            # صلاحية مقامات v9): B1/B2 لا يُحسمان إلا على مقام مزدوج كافٍ.
            # استبعاد token-only معتمد على الناتج وقد يستبعد استبعادًا اختياريًا
            # لصالح ذراع، فالحد الأدنى يمنع الحكم على مقامات واهية.
            def _paired_class(a_cfg, c_cfg, lang_pred):
                a, c = runs[a_cfg], runs[c_cfg]
                return sum(1 for k in a if k in c and lang_pred(k)
                           and not a[k].get("provider_error") and not c[k].get("provider_error")
                           and a[k].get("language_match") is not None
                           and c[k].get("language_match") is not None)
            _is_ar = lambda k: cases[k]["lang"] == "ar"  # noqa: E731
            _is_en = lambda k: cases[k]["lang"] == "en"  # noqa: E731
            ar_cov = min(_paired_class("grounded-v4-lang", "grounded-v4r-lang", _is_ar),
                         _paired_class("grounded-v2", "grounded-v4r-lang", _is_ar))
            en_cov = _paired_class("grounded-v4-lang", "grounded-v4r-lang", _is_en)

            def _arm_exclusive_tokenonly(arm, other):
                r, o = runs[arm], runs[other]
                return sum(1 for k in r if k in o and _is_ar(k)
                           and r[k].get("language_match") is None
                           and o[k].get("language_match") is not None)
            cr["coverage"] = {
                "ar_paired_classifiable_min": ar_cov, "ar_minimum": 50,
                "en_paired_classifiable": en_cov, "en_minimum": 45,
                "tokenonly_by_arm_lang": {
                    arm: {"ar": sum(1 for k in r if _is_ar(k) and r[k].get("language_match") is None),
                          "en": sum(1 for k in r if _is_en(k) and r[k].get("language_match") is None)}
                    for arm, r in runs.items()},
                "ar_tokenonly_exclusive_vs_v4r": {
                    "v4": _arm_exclusive_tokenonly("grounded-v4-lang", "grounded-v4r-lang"),
                    "v4r": _arm_exclusive_tokenonly("grounded-v4r-lang", "grounded-v4-lang"),
                    "v2": _arm_exclusive_tokenonly("grounded-v2", "grounded-v4r-lang")}}
            cr["coverage_insufficient"] = ar_cov < 50 or en_cov < 45
            # سلم القرار المسجل §5: أخطاء ⇒ غير حاسم؛ تغطية دون الحد ⇒ غير
            # حاسم (تغطية)؛ وإلا ثلاثي الحالات كما هو.
            if not cr["inconclusive_by_errors"] and cr["coverage_insufficient"]:
                cr["decision"] = "inconclusive (coverage)"
    (_out(dataset) / f"holdout_score{'_' + tag if tag else ''}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", required=True, choices=["dev", "holdout"])
    r.add_argument("--arms", default="v2,v4,baseline")
    r.add_argument("--tag", default="")
    r.add_argument("--dataset", default="v7", choices=["v7", "v8", "v9", "v10"])
    sc = sub.add_parser("score")  # noqa: F841  (kept for symmetry with run)
    sc.add_argument("--tag", default="")
    sc.add_argument("--dataset", default="v7", choices=["v7", "v8", "v9", "v10"])
    a = ap.parse_args(argv)
    if a.cmd == "run":
        arms = {"v2": "grounded-v2", "v4": "grounded-v4-lang",
               "baseline": "baseline", "v4r": "grounded-v4r-lang"}
        chosen = [arms[x] for x in a.arms.split(",") if x.strip()]
        for p in asyncio.run(run(a.split, chosen, a.tag, a.dataset)):
            print(p)
    else:
        print(json.dumps(score(a.tag, a.dataset), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
