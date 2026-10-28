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


def test_v8_questions_differ_from_v6_and_v7():
    q6, q7, q8 = _load("v6"), _load("v7"), _load("v8")
    same6 = [k for k in q8 if k in q6 and q8[k]["question"] == q6[k]["question"]]
    same7 = [k for k in q8 if k in q7 and q8[k]["question"] == q7[k]["question"]]
    assert not same6, f"v8 reused v6 question text: {same6}"
    assert not same7, f"v8 reused v7 question text: {same7}"


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
