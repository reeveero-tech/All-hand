#!/usr/bin/env python3
"""R-169 / Q-028: generate the HUMAN review package for the frozen v1 test
runs (PR #214) from the committed raw evidence — nothing is invented here.

The package is deliberately non-leading:
* REVIEW_PACKAGE.md shows the reviewer, for each sampled case: the user
  question, the full provided sources, the complete raw reply, and the
  expected behaviour with its judging standard. The scorer's verdict is NOT
  shown in this file.
* scorer_verdicts.md holds the deterministic scorer's verdicts for the same
  cases and is meant to be opened only AFTER the reviewer records their own
  judgments (or by whoever reconciles the two).
* reviewer_form.json is the empty machine-readable form the reviewer fills in.
  The executing agent must never fill it on the reviewer's behalf.

Sample rule: the same seeded sample as the committed agent review
(`scripts/r169_report.py sample`, seed 16901: one ar + one en case per task
family = 20 per run), plus every failing case of each run.

Usage:
    python scripts/r169_human_review.py            # (re)write the package
    python scripts/r169_human_review.py --check    # exit 1 if files differ
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
OUT_DIR = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169" / "human_review_pr214"
RUNS = ROOT / "docs" / "audit" / "M8" / "logs" / "raw" / "r169"
SEED = 16901


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


REPORT = _load_script("r169_report")


def _load_run(name: str):
    lines = [json.loads(x) for x in (RUNS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return lines[0], lines[1:]


def _sample_ids(recs: list[dict], per_type: int = 2) -> list[str]:
    rng = random.Random(SEED)
    by: dict[str, list[str]] = {}
    for r in recs:
        by.setdefault(r["score"]["task_type"], []).append(r["id"])
    ids: list[str] = []
    for t in sorted(by):
        ar = [i for i in by[t] if i.endswith("-ar")]
        en = [i for i in by[t] if i.endswith("-en")]
        k = max(1, per_type // 2)
        ids += rng.sample(ar, min(k, len(ar))) + rng.sample(en, min(k, len(en)))
    return ids


BEHAVIOUR_STANDARD = {
    "answer": "السلوك المتوقع: إجابة تحمل القيمة/الواقعة الصحيحة كما تنص المصادر، مستندة إليها، دون قيم من مصادر مشتتة.",
    "abstain": "السلوك المتوقع: الامتناع عن الإجابة والتصريح بأن المصادر لا تحتوي المعلومة، دون اختلاق قيمة أو اسم أو تاريخ.",
    "reject_premise": "السلوك المتوقع: رفض افتراض السؤال (أو الامتناع)، مع عدم اختلاق تاريخ/قيمة لحدث لم يحدث.",
    "flag_conflict": "السلوك المتوقع: إبراز التعارض بين المصادر وذكر القيمتين المتعارضتين مع مصدر كل منهما، دون حسم تعسفي بلا دليل.",
    "pass_tests": "السلوك المتوقع: شيفرة بايثون صحيحة تجتاز الاختبارات المؤلفة (تُقاس بتشغيل فعلي)، لا وعد أو ادعاء نجاح.",
}


def _fmt_sources(sources: list[dict]) -> str:
    if not sources:
        return "— (لا مصادر مقدمة لهذه الحالة)"
    parts = []
    for s in sources:
        parts.append(f"  - **{s['id']}** ({s.get('kind', '?')}, تاريخ {s.get('date', '?')}): {s.get('title', '')}\n"
                     f"    النص: {s.get('text', '')}")
    return "\n".join(parts)


def _case_block(idx: int, total: int, run: str, case: dict, rec: dict) -> str:
    lines = [f"### [{idx}/{total}] `{case['id']}` — التشغيل `{run}`",
             f"- اللغة: {case['lang']} | العائلة: `{case['task_type']}` | القطبية: {case['polarity']}"]
    if rec.get("error"):
        lines.append(f"- **خطأ مزود/معالج مسجل في هذا التشغيل** (تُحسب فشلاً في القياس): {str(rec['error'])[:200]}")
    lines.append(f"\n**سؤال المستخدم:**\n\n> {case['question']}\n")
    lines.append(f"**المصادر المقدمة:**\n\n{_fmt_sources(case['sources'])}\n")
    reply = rec.get("reply") or ""
    if rec.get("code"):
        reply = f"{reply}\n\n```python\n{rec['code']}\n```"
    lines.append("**الاستجابة الخام الكاملة:**\n\n```\n" + (reply or "(استجابة فارغة)") + "\n```\n")
    lines.append(f"**{BEHAVIOUR_STANDARD[case['expected_behavior']]}\n")
    lines.append("**حقول الحكم (يملؤها المراجع البشري):**\n")
    lines.append("1. صحة الإجابة/السلوك: صحيح ☐ / غير صحيح ☐ / لا يمكن الحكم ☐")
    lines.append("2. الاستناد للمصادر (لا ادعاء بلا دليل): كافٍ ☐ / غير كافٍ ☐ / لا ينطبق ☐")
    lines.append("3. الامتناع عند غياب المعلومة: مناسب ☐ / غير مناسب ☐ / لا ينطبق ☐")
    lines.append("4. التعامل مع التعارض (إن وجد): مناسب ☐ / غير مناسب ☐ / لا ينطبق ☐")
    lines.append("5. لغة الإجابة مطابقة للسؤال: نعم ☐ / لا ☐ / لا يمكن الحكم ☐")
    lines.append("6. اختلاق أو ادعاء غير مدعوم (قيمة/اسم/تاريخ/نتيجة اختبار/استشهاد): موجود ☐ / غير موجود ☐")
    lines.append("7. الحكم الإجمالي وفق معيار السلوك أعلاه: نجاح ☐ / فشل ☐")
    lines.append("8. تعليق يفسر الخلاف (اختياري): ........................................\n")
    lines.append("---\n")
    return "\n".join(lines)


def build() -> dict[str, bytes]:
    from agent_civilization.eval import r169_bench as b
    cases = {c["id"]: c for s in b.SPLITS for c in b.load_split(s)}
    files: dict[str, bytes] = {}

    header = [
        "# حزمة المراجعة البشرية لعينة R-169 (Q-028 — شرط قبول P4)\n",
        "> **لمن هذا الملف**: مراجع بشري مستقل (المالك أو من يعيّنه). مراجعة الوكيل المنفذ",
        "> والمراجعة المستقلة (وكيل آخر) لا تحققان شرط P4 ولا تُحسبان مراجعة بشرية.\n",
        "## ماذا يراجع المراجع ولماذا\n",
        "هذه عينة من مخرجات القياس المجمد `r169-v1` (PR #214): التشغيلان `test_baseline_f1`",
        "(المسارات الحية) و`test_grounded-v2_f1` (المطالبة المرشحة). القياس يستخدم مصححاً",
        "حتمياً معجمياً معلن الحدود؛ معيار P4 يشترط مراجعة بشرية لعينة لقياس اتفاق الحكم",
        "البشري مع المصحح. حكمك هنا هو المرجع عند الخلاف.\n",
        "## قواعد المراجعة\n",
        "1. اقرأ لكل حالة: سؤال المستخدم، المصادر، الاستجابة الخام، ومعيار السلوك المتوقع.",
        "2. احكم بنفسك أولاً وفق الحقول الثمانية. **لا تفتح `scorer_verdicts.md` قبل تسجيل أحكامك**",
        "   (حكم المصحح الحتمي موجود هناك للمقارنة لاحقاً فقط).",
        "3. لا يوجد سلوك «مطلوب منك إيجاده»: إن رأيت الاستجابة جيدة فقل ذلك، وإن رأيتها رديئة فقل ذلك.",
        "4. الحد الأدنى المطلوب: الجزء أ (عينة البذرة، 20 حالة لكل تشغيل). الجزء ب (كل الحالات",
        "   الفاشلة، 13 حالة) يزيد دقة المقارنة إن أتممته.",
        "5. عند الانتهاء، املأ `reviewer_form.json` (أو ضع علاماتك في نسخة من هذا الملف) وسجّل",
        "   اسمك/دورك وتاريخك. لا يعبّئ أي وكيل هذه الحقول نيابة عنك.\n",
    ]

    form = {
        "kind": "human review of R-169 v1 test sample (Q-028; P4 acceptance requirement)",
        "instructions": "Fill reviewer metadata and one judgment per reviewed case. Do not pre-fill from the scorer's verdicts; record your own reading of the raw replies. An agent must never fill this file.",
        "reviewer": {"name_or_handle": "", "role": "", "review_date": "", "confirmed_human": False},
        "runs": {},
    }
    verdict_doc = ["# حكم المصحح الحتمي لنفس الحالات (يُفتح بعد تسجيل أحكام المراجع)\n",
                   "> هذا الملف للمقارنة بعد اكتمال المراجعة البشرية. حكم المصحح معجمي حتمي",
                   "> ومعروف الحدود (انظر تقرير R-169)؛ الخلاف الموثق بينه وبين المراجع البشري هو",
                   "> مقياس جودة المصحح.\n"]

    parts = [f"{l}\n" for l in header]
    idx = 0
    run_data = {}
    for run in ("test_baseline_f1", "test_grounded-v2_f1"):
        meta, recs = _load_run(run)
        by_id = {r["id"]: r for r in recs}
        sample = _sample_ids(recs)
        failures = sorted([r["id"] for r in recs if not r["score"]["pass"]])
        run_data[run] = (sample, failures)
        form["runs"][run] = {"sample": sample, "failures": failures, "judgments": {}}

    total = sum(len(s) for s, _ in run_data.values()) + sum(len(f) for _, f in run_data.values())
    seen: set[tuple[str, str]] = set()
    blocks_a, blocks_b = [], []
    v_lines = []
    for run in ("test_baseline_f1", "test_grounded-v2_f1"):
        meta, recs = _load_run(run)
        by_id = {r["id"]: r for r in recs}
        sample, failures = run_data[run]
        blocks_a.append(f"\n## الجزء أ — عينة البذرة (16901): التشغيل `{run}`\n")
        for cid in sample:
            idx += 1
            key = (run, cid)
            if key in seen:
                continue
            seen.add(key)
            blocks_a.append(_case_block(idx, total, run, cases[cid], by_id[cid]))
        blocks_b.append(f"\n## الجزء ب — كل الحالات الفاشلة: التشغيل `{run}`\n")
        for cid in failures:
            idx += 1
            key = (run, cid)
            if key in seen:
                continue
            seen.add(key)
            blocks_b.append(_case_block(idx, total, run, cases[cid], by_id[cid]))

    parts += blocks_a + blocks_b
    parts.append("\n## بعد اكتمال المراجعة\n\n"
                 "- تُقارن الأحكام البشرية بحكم المصحح وتُوثَّق نسبة الاتفاق والخلافات في تقرير R-169 (إضافة، لا تعديل للنتائج المجمدة).\n"
                 "- أي تصحيح منهجي للمصحح يذهب إلى إصدار جديد (v2)؛ لا يُعدَّل v1 بعد الاطلاع على نتائجه.\n")
    files["REVIEW_PACKAGE.md"] = ("\n".join(parts)).encode("utf-8")

    for run in ("test_baseline_f1", "test_grounded-v2_f1"):
        meta, recs = _load_run(run)
        by_id = {r["id"]: r for r in recs}
        v_lines.append(f"\n## التشغيل `{run}`\n\n| الحالة | حكم المصحح | أسباب الفشل | خطأ مزود |\n|---|---|---|---|\n")
        sample, failures = run_data[run]
        for cid in sample + [f for f in failures if f not in sample]:
            s = by_id[cid]["score"]
            v_lines.append(f"| `{cid}` | {'نجاح' if s['pass'] else 'فشل'} | "
                           f"{'; '.join(s.get('fail_reasons', [])) or '—'} | {'نعم' if s.get('provider_error') else '—'} |\n")
    verdict_doc = verdict_doc[:1] + [verdict_doc[1] + "\n"] + v_lines
    files["scorer_verdicts.md"] = ("\n".join(verdict_doc)).encode("utf-8")

    files["reviewer_form.json"] = (json.dumps(form, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    files = build()
    if args.check:
        bad = [n for n, d in files.items() if not (OUT_DIR / n).exists() or (OUT_DIR / n).read_bytes() != d]
        if bad:
            print("review package differs from generator output: " + ", ".join(bad))
            return 1
        print("review package matches generator output")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for n, d in files.items():
        (OUT_DIR / n).write_bytes(d)
    print(f"wrote {len(files)} files to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
