#!/usr/bin/env python3
"""R-169 v2: render the metric tables of the v2 report from the committed run
summaries (numbers are never typed by hand), the seeded review sample, and —
new in v2 — the PAIRED comparison between two configurations that scored the
same cases (exact McNemar + group-level cluster bootstrap; the v1 report's
independent Wilson intervals were descriptive only and were once wrongly read
as a significance test).

    python scripts/r169_report_v2.py tables RUN_A RUN_B
    python scripts/r169_report_v2.py paired RUN_A RUN_B
    python scripts/r169_report_v2.py sample RUN [--per-type 2]
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
RUNS = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169"
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


def cell(m: dict) -> str:
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
                   + "؛ ".join(f"`{f['id'].replace('r169-v2-', '')}` ({', '.join(f['reasons'])})"
                              for f in r["metrics"]["failures"]) + "\n")
    out.append("\n### بيانات التشغيل\n\n| الحقل | " + " | ".join(f"`{n}`" for n in names) + " |\n|---|"
               + "---|" * len(names) + "\n")
    for k in ("code_sha", "tree_dirty", "dataset_sha256", "scorer_sha256", "runner_sha256", "started_utc",
              "provider_url", "requested_model", "max_tokens_text", "max_tokens_code", "retry_policy", "cases"):
        out.append(f"| {k} | " + " | ".join(f"`{r['meta'].get(k)}`" for r in runs) + " |\n")
    out.append("| models_reported | " + " | ".join(", ".join(f"`{m}`" for m in r["models_reported"]) for r in runs)
               + " |\n")
    return "".join(out)


def paired(name_a: str, name_b: str) -> dict:
    """Paired comparison of two runs over the same split (v2: replaces
    'do the intervals overlap?' reasoning — v1's intervals are item-level,
    not paired/cluster-aware, and were once misread as a significance test)."""
    from agent_civilization.eval import r169_bench_v2 as b
    meta_a, recs_a = _load_run(name_a)
    meta_b, recs_b = _load_run(name_b)
    cases = {c["id"]: c for c in b.load_split(meta_a["split"])}
    sa = [b.score_record(cases[r["id"]], r) for r in recs_a]
    sb = [b.score_record(cases[r["id"]], r) for r in recs_b]
    return b.paired_comparison(sa, sb)


def _load_run(name: str):
    p = RUNS / f"{name}.jsonl"
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[0], lines[1:]


def sample(name: str, per_type: int = 2, seed: int = 16901) -> list[str]:
    path = RUNS / f"{name}.jsonl"
    recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()[1:]]
    rng = random.Random(seed)
    by = {}
    for r in recs:
        by.setdefault(r["score"]["task_type"], []).append(r["id"])
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
    s = sub.add_parser("sample")
    s.add_argument("run")
    s.add_argument("--per-type", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "tables":
        sys.stdout.write(tables(a.runs))
    elif a.cmd == "paired":
        print(json.dumps(paired(a.run_a, a.run_b), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("\n".join(sample(a.run, a.per_type)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
