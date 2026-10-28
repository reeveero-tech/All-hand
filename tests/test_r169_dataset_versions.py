"""R-169 v8: فحص آلي لاستقلال محتوى حزمة الحكم (توجيه المراجعة المستقلة).

«اختلاف البذرة أو المعرفات لا يثبت استقلال الحالات» — لذا يقارن هذا الفحص
**نص الأسئلة الفعلي** (لا المعرفات ولا وسوم النسخة) بين v8 وكل من v6 وv7،
إضافة إلى الكاناريات ومهام الشيفرة وحقيقة closed_book. عائلة tool_poisoning
النمط الثاني لها نص سؤال ثابت في v6/v7 — أعيدت صياغته في v8 خصيصًا لهذا الفحص.
"""
from __future__ import annotations

import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "r169"


def _load(ver: str) -> dict[str, dict]:
    rows = [json.loads(x) for x in (BENCH / ver / "holdout.jsonl").read_text().splitlines() if x.strip()]
    return {c["id"].replace(f"r169-{ver}-", ""): c for c in rows}


def test_v8_questions_differ_from_all_previous_versions():
    """استقلال المحتوى الفعلي مقابل v1–v7 كلها (توجيه المراجعة المستقلة):
    مقارنة مجموعات الأسئلة عبر جميع المعرفات وجميع الشرائح — لا عند المعرف
    البنيوي المطابق فقط — إضافة إلى مهام الشيفرة والحقائق والكاناريات عالميًا."""
    q8 = _load("v8")
    v8_questions = {c["question"] for c in q8.values()}
    v8_code = {c["question"] for k, c in q8.items() if k.startswith("code_task")}
    v8_cb = {c["question"] for k, c in q8.items() if k.startswith("closed_book")}
    v8_canaries = {x for c in q8.values()
                   for x in c["reference"].get("canaries", ())
                   if x.startswith(("CANARY-", "SYNTH-TOKEN"))}
    for ver in ("v1", "v2", "v3", "v4", "v5", "v6", "v7"):
        d = BENCH / ver
        for f in d.glob("*.jsonl"):
            if f.name in ("grader_corpus_v3.jsonl",):
                continue
            rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
            rows = [r for r in rows if isinstance(r, dict) and "question" in r]
            old_questions = {r["question"] for r in rows}
            assert not (v8_questions & old_questions), \
                f"v8 reused question text from {ver}/{f.name}"
            old_code = {r["question"] for r in rows
                       if str(r.get("task_type", "")).startswith("code")
                       or str(r.get("expected_behavior")) == "pass_tests"}
            assert not (v8_code & old_code), f"v8 reused a code task from {ver}/{f.name}"
            old_cb = {r["question"] for r in rows if "closed_book" in str(r.get("group", ""))}
            assert not (v8_cb & old_cb), f"v8 reused the closed_book fact from {ver}/{f.name}"
            old_can = {x for r in rows for x in (r.get("reference") or {}).get("canaries", ())
                       if x.startswith(("CANARY-", "SYNTH-TOKEN"))}
            assert not (v8_canaries & old_can), f"v8 reused a canary from {ver}/{f.name}"


def test_v8_canaries_are_new():
    q7, q8 = _load("v7"), _load("v8")
    for k, c in q8.items():
        if c["reference"].get("canaries") and k in q7:
            old = {x for x in q7[k]["reference"].get("canaries", ()) if x.startswith(("CANARY-", "SYNTH-TOKEN"))}
            new = {x for x in c["reference"]["canaries"] if x.startswith(("CANARY-", "SYNTH-TOKEN"))}
            assert not (old & new), f"canary reuse at {k}"


def test_v8_code_tasks_and_closed_book_are_new():
    q7, q8 = _load("v7"), _load("v8")
    code7 = {q7[k]["question"] for k in q7 if k.startswith("code_task")}
    code8 = {q8[k]["question"] for k in q8 if k.startswith("code_task")}
    assert code8 and not (code7 & code8), "v8 reused v7 code tasks"
    cb7 = {q7[k]["question"] for k in q7 if k.startswith("closed_book")}
    cb8 = {q8[k]["question"] for k in q8 if k.startswith("closed_book")}
    assert cb8 and not (cb7 & cb8), "v8 reused v7 closed_book fact"


def test_v8_composition_and_manifest():
    q8 = _load("v8")
    fams = {k.rsplit("-", 2)[0] for k in q8}
    assert len(q8) == 46
    assert fams == {"grounded_qa", "abstain_missing", "false_premise", "conflicting_sources",
                   "stale_sources", "prompt_injection", "adversarial_user", "tool_poisoning",
                   "code_task", "closed_book"}
    man = json.loads((BENCH / "v8" / "manifest.json").read_text())
    assert man["dataset_sha256"] and man["dataset_version"] == "r169-v8"
    assert man["generator"] == "scripts/r169_build_dataset_v8.py"
