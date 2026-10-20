#!/usr/bin/env python3
"""R-169: render the metric tables of docs/audit/M8/R169_REPORT.md from the
committed run summaries (numbers are never typed by hand), and draw the
seeded manual-review sample.

    python scripts/r169_report.py tables RUN_A RUN_B [...]     # markdown to stdout
    python scripts/r169_report.py sample RUN [--per-type 2]   # review sample ids
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169"
METRICS = [
    ("pass", "النجاح الإجمالي"),
    ("factual_accuracy", "دقة الوقائع (حالات answer)"),
    ("appropriate_abstention", "الامتناع/رفض الافتراض المناسب"),
    ("hallucination_on_unanswerable", "هلوسة على غير القابل للإجابة (أقل أفضل)"),
    ("over_abstention", "إفراط في الامتناع (أقل أفضل)"),
    ("citation_coverage", "استشهاد صحيح بمصدر داعم"),
    ("fabricated_citation", "استشهاد مختلق بين الإجابات المستشهدة (أقل أفضل)"),
    ("unsupported_number_claims", "أرقام غير مسنودة (أقل أفضل)"),
    ("injection_resistance", "مقاومة الحقن/تسميم الأدوات"),
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
    out = ["### المقاييس الإجمالية (k/n، النسبة، فاصل Wilson 95%)\n", hdr]
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
    out.append("\n### أخطاء المزود (محسوبة فشلاً)\n\n" + hdr)
    out.append("| provider_errors | " + " | ".join(str(r["metrics"]["provider_errors"]) for r in runs) + " |\n")
    out.append("\n### الحالات الفاشلة\n\n")
    for n, r in zip(names, runs):
        out.append(f"- `{n}` ({len(r['metrics']['failures'])}): "
                   + "؛ ".join(f"`{f['id'].replace('r169-v1-', '')}` ({', '.join(f['reasons'])})"
                              for f in r["metrics"]["failures"]) + "\n")
    out.append("\n### بيانات التشغيل\n\n| الحقل | " + " | ".join(f"`{n}`" for n in names) + " |\n|---|"
               + "---|" * len(names) + "\n")
    for k in ("code_sha", "tree_dirty", "dataset_sha256", "scorer_sha256", "started_utc", "provider_url",
              "requested_model", "cases"):
        out.append(f"| {k} | " + " | ".join(f"`{r['meta'].get(k)}`" for r in runs) + " |\n")
    out.append("| models_reported | " + " | ".join(", ".join(f"`{m}`" for m in r["models_reported"]) for r in runs)
               + " |\n")
    out.append("| prompt_fingerprint | " + " | ".join(
        "<br>".join(f"{k}={v}" for k, v in sorted(r["meta"]["prompt_fingerprint"].items())) for r in runs) + " |\n")
    return "".join(out)


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
    s = sub.add_parser("sample")
    s.add_argument("run")
    s.add_argument("--per-type", type=int, default=2)
    a = ap.parse_args(argv)
    if a.cmd == "tables":
        sys.stdout.write(tables(a.runs))
    else:
        print("\n".join(sample(a.run, a.per_type)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
