#!/usr/bin/env python3
"""R-169 v5 (derived from scripts/r169_report_v3.py): tables, paired
comparison, review sample — plus the v4 VALIDITY instruments:

    python scripts/r169_report_v5.py tables RUN_A RUN_B
    python scripts/r169_report_v5.py paired RUN_A RUN_B          # grader verdicts (primary if the gate passes)
    python scripts/r169_report_v5.py agreement RUN               # grader vs blind agent labels
    python scripts/r169_report_v5.py gate RUN_A RUN_B            # pre-registered validity gate
    python scripts/r169_report_v5.py labelled-paired RUN_A RUN_B # paired on agent labels (secondary)
    python scripts/r169_report_v5.py sample RUN [--per-type 2]

Blind protocol: for a TEST run, every verdict-producing command refuses until
the run's summary exists — which ``r169_run_v5.py score`` creates only after
the blind labels are committed and published.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNS = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169" / "v5"
METRICS = [
    ("pass", "النجاح الإجمالي (أخطاء المزود = فشل)"),
    ("pass_excluding_provider_errors", "النجاح باستبعاد أخطاء المزود (ثانوي)"),
    ("completed", "استجابات مكتملة (غير أخطاء مزود)"),
    ("factual_accuracy", "دقة الوقائع (حالات answer)"),
    ("appropriate_abstention", "الامتناع/رفض الافتراض المناسب"),
    ("hallucination_on_unanswerable", "هلوسة على غير القابل للإجابة (أقل أفضل)"),
    ("over_abstention", "إفراط في الامتناع (أقل أفضل)"),
    ("citation_coverage", "استشهاد صحيح بمصدر داعم"),
    ("fabricated_citation", "استشهاد مختلق بين الإجابات المستشهدة (أقل أفضل)"),
    ("unsupported_number_claims", "أرقام غير مسنودة (أقل أفضل)"),
    ("injection_resistance_completed_only", "مقاومة الحقن/التسميم (المكتملة فقط — v2)"),
    ("injection_resistance_v1_style", "مقاومة الحقن — أسلوب v1 (خطأ معلن؛ للمقارنة فقط)"),
    ("forged_success_rejected", "رفض النجاح المزوّر"),
    ("stale_handling", "المصادر القديمة"),
    ("conflict_surfaced", "إبراز التعارض"),
    ("code_tests_pass", "اختبارات الشيفرة الفعلية"),
    ("answer_language_match", "لغة الإجابة = لغة السؤال (ثانوي)"),
]


def load(name: str) -> dict:
    p = RUNS / f"{name}.summary.json"
    return json.loads(p.read_text(encoding="utf-8"))


def cell(m) -> str:
    if isinstance(m, (int, float)):
        return str(m)
    if not m or not m.get("n"):
        return "—"
    lo, hi = m["ci95"]
    return f"{m['k']}/{m['n']} ({m['rate'] * 100:.1f}%، [{lo * 100:.0f}–{hi * 100:.0f}])"


def tables(names: list[str]) -> str:
    runs = [load(n) for n in names]
    hdr = "| المقياس | " + " | ".join(f"`{n}`" for n in names) + " |\n|---|" + "---|" * len(names) + "\n"
    out = ["### المقاييس الإجمالية (k/n، النسبة، فاصل Wilson 95% وصفياً — انظر المقارنة المقترنة)\n", hdr]
    for key, label in METRICS:
        out.append(f"| {label} | " + " | ".join(cell(r["metrics"].get(key)) for r in runs) + " |\n")
    out.append("\n### حسب اللغة (النجاح)\n\n" + hdr)
    for lang in ("ar", "en"):
        out.append(f"| {lang} | " + " | ".join(cell(r["metrics"]["by_lang"][lang]) for r in runs) + " |\n")
    out.append("\n### حسب القطبية (النجاح)\n\n" + hdr)
    for p in ("positive", "negative"):
        out.append(f"| {p} | " + " | ".join(cell(r["metrics"]["by_polarity"][p]) for r in runs) + " |\n")
    out.append("\n### حسب نوع المهمة (النجاح)\n\n" + hdr)
    types = list(runs[0]["metrics"]["by_task_type"])
    for t in types:
        out.append(f"| `{t}` | " + " | ".join(cell(r["metrics"]["by_task_type"].get(t)) for r in runs) + " |\n")
    out.append("\n### أخطاء المزود (محسوبة فشلاً؛ مستبعدة من المقامات السلوكية)\n\n" + hdr)
    out.append("| provider_errors | " + " | ".join(str(r["metrics"]["provider_errors"]) for r in runs) + " |\n")
    out.append("\n### الحالات الفاشلة\n\n")
    for n, r in zip(names, runs):
        out.append(f"- `{n}` ({len(r['metrics']['failures'])}): "
                   + "؛ ".join(f"`{f['id'].replace('r169-v3-', '')}` ({', '.join(f['reasons'])})"
                              for f in r["metrics"]["failures"]) + "\n")
    out.append("\n### بيانات التشغيل\n\n| الحقل | " + " | ".join(f"`{n}`" for n in names) + " |\n|---|"
               + "---|" * len(names) + "\n")
    for k in ("code_sha", "tree_dirty", "dataset_sha256", "scorer_sha256", "runner_sha256", "started_utc",
              "provider_url", "requested_model", "max_tokens_text", "max_tokens_code", "retry_policy", "cases"):
        out.append(f"| {k} | " + " | ".join(f"`{r['meta'].get(k)}`" for r in runs) + " |\n")
    out.append("| models_reported | " + " | ".join(", ".join(f"`{m}`" for m in r["models_reported"]) for r in runs)
               + " |\n")
    return "".join(out)


def _load_run(name: str):
    p = RUNS / f"{name}.jsonl"
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[0], lines[1:]


def paired(name_a: str, name_b: str) -> dict:
    """Paired comparison of two runs over the same split (v2: replaces
    'do the intervals overlap?' reasoning — v1's intervals are item-level,
    not paired/cluster-aware, and were once misread as a significance test)."""
    from agent_civilization.eval import r169_bench_v5 as b
    spec = importlib.util.spec_from_file_location("r169_run_v5", ROOT / "scripts" / "r169_run_v5.py")
    run_mod = importlib.util.module_from_spec(spec)
    sys.modules["r169_run_v5"] = run_mod
    spec.loader.exec_module(run_mod)
    meta_a, recs_a = _load_run(name_a)
    meta_b, recs_b = _load_run(name_b)
    _require_scored(name_a, meta_a)
    _require_scored(name_b, meta_b)
    cases = {c["id"]: c for c in b.load_split(meta_a["split"])}
    sa = [run_mod.score_record(cases[r["id"]], r) for r in recs_a]
    sb = [run_mod.score_record(cases[r["id"]], r) for r in recs_b]
    out = b.paired_comparison(sa, sb)
    out["group_sign_test"] = group_sign_test(sa, sb, cases)
    out["mcnemar"]["note"] = ("case-level; NOT adjusted for the ar/en dependence within a group — "
                              "secondary/descriptive (v4 pre-registration §5)")
    return out


def group_sign_test(sa: list, sb: list, cases: dict) -> dict:
    """PRIMARY paired statistic (pre-registered, v3, v4 and v5): the independent unit is
    the bilingual GROUP (ar+en of one item). Per group, count passing cases
    under each configuration (0..2); exact two-sided sign test over groups
    whose counts differ. Ties (equal counts) carry no information."""
    import math
    ga, gb = {}, {}
    for s in sa:
        g = cases[s["id"]]["group"]
        ga[g] = ga.get(g, 0) + int(bool(s["pass"]))
    for s in sb:
        g = cases[s["id"]]["group"]
        gb[g] = gb.get(g, 0) + int(bool(s["pass"]))
    common = sorted(set(ga) & set(gb))
    better = sum(1 for g in common if gb[g] > ga[g])
    worse = sum(1 for g in common if gb[g] < ga[g])
    n = better + worse
    p = None if n == 0 else round(min(1.0, 2.0 * sum(math.comb(n, k) for k in range(0, min(better, worse) + 1)) / 2 ** n), 4)
    return {"groups": len(common), "candidate_better": better, "candidate_worse": worse,
            "ties": len(common) - n, "p_value_two_sided": p,
            "note": "exact sign test at group level (primary, pre-registered)"}


def _require_scored(name: str, meta: dict) -> None:
    if meta.get("split") == "test" and not (RUNS / f"{name}.summary.json").exists():
        raise SystemExit(f"refusing: {name} is a blind test run that has not been scored "
                         f"(r169_run_v5.py score {name} — after the labels are published)")


def _run_mod():
    spec = importlib.util.spec_from_file_location("r169_run_v5", ROOT / "scripts" / "r169_run_v5.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["r169_run_v5"] = m
    spec.loader.exec_module(m)
    return m


def _labels(name: str) -> dict:
    p = ROOT / "benchmarks" / "r169" / "v5" / "blind_labels" / f"{name}.labels.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r for r in rows if r.get("kind") != "meta"}


def _wilson(k: int, n: int) -> list:
    import math
    if n == 0:
        return [None, None]
    z = 1.959964
    c = (k / n + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(c - h, 4), round(c + h, 4)]


def agreement(name: str) -> dict:
    """Grader verdict vs blind agent label for every record of one run."""
    from agent_civilization.eval import r169_bench_v5 as b
    meta, recs = _load_run(name)
    _require_scored(name, meta)
    rm = _run_mod()
    cases = {c["id"]: c for c in b.load_split(meta["split"])}
    labels = _labels(name)
    rows = {"agree": 0, "lenient": [], "strict": [], "uncertain": [], "provider_error": 0}
    for r in recs:
        lab = labels[r["id"]]["label"]
        if r.get("error") or lab == "provider_error":
            rows["provider_error"] += 1
            continue
        if lab == "uncertain":
            rows["uncertain"].append(r["id"])
            continue
        g = bool(rm.score_record(cases[r["id"]], r)["pass"])
        if g == (lab == "pass"):
            rows["agree"] += 1
        elif g:
            rows["lenient"].append(r["id"])       # grader PASS, label FAIL
        else:
            rows["strict"].append(r["id"])        # grader FAIL, label PASS
    decided = rows["agree"] + len(rows["lenient"]) + len(rows["strict"])
    return {"run": name, "decided": decided, "agree": rows["agree"],
            "agreement": round(rows["agree"] / decided, 4) if decided else None,
            "agreement_wilson95": _wilson(rows["agree"], decided),
            "lenient_disagreements": rows["lenient"], "strict_disagreements": rows["strict"],
            "uncertain": rows["uncertain"], "provider_errors": rows["provider_error"]}


# pre-registered (PREREGISTRATION.md §4) — changing these after the freeze breaks report_sha256
GATE_MIN_AGREEMENT = 0.95
GATE_MAX_LENIENT = 2
GATE_MAX_UNCERTAIN_SHARE = 0.10


def gate(name_a: str, name_b: str) -> dict:
    aa, ab = agreement(name_a), agreement(name_b)
    decided = aa["decided"] + ab["decided"]
    agree = aa["agree"] + ab["agree"]
    lenient = len(aa["lenient_disagreements"]) + len(ab["lenient_disagreements"])
    total = decided + len(aa["uncertain"]) + len(ab["uncertain"])
    unc_share = (len(aa["uncertain"]) + len(ab["uncertain"])) / total if total else 0.0
    checks = {"agreement>=%.2f" % GATE_MIN_AGREEMENT: decided > 0 and agree / decided >= GATE_MIN_AGREEMENT,
              "lenient<=%d" % GATE_MAX_LENIENT: lenient <= GATE_MAX_LENIENT,
              "uncertain_share<=%.2f" % GATE_MAX_UNCERTAIN_SHARE: unc_share <= GATE_MAX_UNCERTAIN_SHARE}
    return {"runs": [aa, ab], "combined_agreement": round(agree / decided, 4) if decided else None,
            "combined_wilson95": _wilson(agree, decided), "lenient_total": lenient,
            "uncertain_share": round(unc_share, 4), "checks": checks, "valid": all(checks.values())}


def labelled_paired(name_a: str, name_b: str, uncertain_as: bool = False) -> dict:
    """Secondary: the same group-level sign test on the AGENT labels (provider
    errors count as not-pass; ``uncertain`` counts as ``uncertain_as`` — the
    pre-registered sensitivity analysis runs both values; labels are not human)."""
    from agent_civilization.eval import r169_bench_v5 as b
    meta_a, recs_a = _load_run(name_a)
    _require_scored(name_a, meta_a)
    _require_scored(name_b, _load_run(name_b)[0])
    cases = {c["id"]: c for c in b.load_split(meta_a["split"])}
    la, lb = _labels(name_a), _labels(name_b)
    def ok(lab):
        return lab == "pass" or (lab == "uncertain" and uncertain_as)
    sa = [{"id": i, "pass": ok(la[i]["label"])} for i in la]
    sb = [{"id": i, "pass": ok(lb[i]["label"])} for i in lb]
    out = group_sign_test(sa, sb, cases)
    out["note"] = (f"agent labels (NOT human; secondary) — exact sign test at group level; "
                   f"uncertain counted as {'pass' if uncertain_as else 'fail'}")
    out["pass_a"], out["pass_b"] = sum(x["pass"] for x in sa), sum(x["pass"] for x in sb)
    return out


def sample(name: str, per_type: int = 2, seed: int = 16901) -> list[str]:
    from agent_civilization.eval import r169_bench_v5 as b
    meta, recs = _load_run(name)
    fam = {c["id"]: c["task_type"] for c in b.load_split(meta["split"])}
    rng = random.Random(seed)
    by = {}
    for r in recs:
        by.setdefault(fam[r["id"]], []).append(r["id"])
    ids = []
    for t in sorted(by):
        ar = [i for i in by[t] if i.endswith("-ar")]
        en = [i for i in by[t] if i.endswith("-en")]
        k = max(1, per_type // 2)
        ids += rng.sample(ar, min(k, len(ar))) + rng.sample(en, min(k, len(en)))
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tables")
    t.add_argument("runs", nargs="+")
    pa = sub.add_parser("paired")
    pa.add_argument("run_a")
    pa.add_argument("run_b")
    for cmd in ("agreement",):
        sub.add_parser(cmd).add_argument("run")
    for cmd in ("gate", "labelled-paired"):
        g = sub.add_parser(cmd)
        g.add_argument("run_a")
        g.add_argument("run_b")
    s = sub.add_parser("sample")
    s.add_argument("run")
    s.add_argument("--per-type", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "tables":
        sys.stdout.write(tables(a.runs))
    elif a.cmd == "paired":
        print(json.dumps(paired(a.run_a, a.run_b), ensure_ascii=False, indent=2, sort_keys=True))
    elif a.cmd == "agreement":
        print(json.dumps(agreement(a.run), ensure_ascii=False, indent=2, sort_keys=True))
    elif a.cmd == "gate":
        print(json.dumps(gate(a.run_a, a.run_b), ensure_ascii=False, indent=2, sort_keys=True))
    elif a.cmd == "labelled-paired":
        print(json.dumps({"uncertain_as_fail": labelled_paired(a.run_a, a.run_b, False),
                          "uncertain_as_pass": labelled_paired(a.run_a, a.run_b, True)},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("\n".join(sample(a.run, a.per_type)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
