# ملاحظة روابط المادة المجمدة (LINKS_NOTE) — دفعة إصلاح الروابط المكسورة

> **آخر تحديث**: 2026-10-27 (لاحق 62 — دفعة إصلاح الروابط المكسورة في الأدلة)

## لماذا هذه الملاحظة

تقرير `raw_report_A.md` (ونسختا `reviewed_versions/*.reviewed.md`) **مصنفات مجمّدة**: بصماتها sha256 مسجلة في [`RECEIPT_LOG.md`](RECEIPT_LOG.md) (مثلًا تقرير A: `325f1e01…`، الأسطر 1–6 من الجدول)، وتعديل أي حرف فيها يكسر دليل الاستلام. لذلك **لم تُحرَّر** هذه الملفات، وعولجت روابطها المكسورة بهذه الملاحظة المرافقة بدلًا من التحرير.

## روابط `material/` في `raw_report_A.md` (87 رابطًا)

الروابط تشير إلى حزمة مجلس المراجعة في مساحة عمل خارج المستودع (`/home/user/workspace/council_q028/material/` — انظر [`INSTRUCTIONS.txt`](INSTRUCTIONS.txt))، فلا يمكن أن تُحلّ داخل المستودع أصلًا. النسخ داخل المستودع:

| الملف المُشار إليه | نسخة داخل المستودع مطابقة بالبايت (sha256 مقابل [`MATERIAL_FINGERPRINTS.txt`](MATERIAL_FINGERPRINTS.txt)) |
|---|---|
| `BLIND_REVIEW_CASES_PHASE_A.md` | نعم — [`../../M8/logs/raw/r169/v4/human_review/BLIND_REVIEW_CASES_PHASE_A.md`](../../M8/logs/raw/r169/v4/human_review/BLIND_REVIEW_CASES_PHASE_A.md) |
| `BLIND_REVIEW_PROTOCOL.md` | نعم — [`../../M8/logs/raw/r169/v4/human_review/BLIND_REVIEW_PROTOCOL.md`](../../M8/logs/raw/r169/v4/human_review/BLIND_REVIEW_PROTOCOL.md) |
| `REVIEWER_CASE_INDEX_PHASE_A.md` | نعم — [`../../M8/logs/raw/r169/v4/human_review/REVIEWER_CASE_INDEX_PHASE_A.md`](../../M8/logs/raw/r169/v4/human_review/REVIEWER_CASE_INDEX_PHASE_A.md) |
| `reviewer_blind_form.json` | نعم — [`../../M8/logs/raw/r169/v4/human_review/reviewer_blind_form.json`](../../M8/logs/raw/r169/v4/human_review/reviewer_blind_form.json) |
| `Q028_HANDOFF_INSTRUCTIONS.md` | **لا توجد نسخة مطابقة داخل المستودع** — الموجود في `docs/audit/M8/q028/Q028_HANDOFF_INSTRUCTIONS.md` يخالف البصمة المرجعية |
| `REVIEWER_OUTREACH.md` | **لا توجد نسخة مطابقة داخل المستودع** — الموجود في `docs/audit/M8/q028/REVIEWER_OUTREACH.md` يخالف البصمة المرجعية |

الأخيران ملفان مجمّدان خارج المستودع فقط؛ عدم وجود نسخة مطابقة داخل المستودع **حقيقة موثقة** لا نقصًا في هذه الدفعة — والمادة الأصلية خارج نطاق المستودع (لا يمكن نسخها إليه دون قرار المالك لأنها تخص عملية مراجعة بشرية حية).

## روابط `reviewed_versions/` (رابطان)

- `REVIEWER_OUTREACH.reviewed.md` يشير إلى `Q028_HANDOFF_INSTRUCTIONS.md` كأنه مجاور — المرجع الفعلي خارج المستودع كما أعلاه.
- `Q028_HANDOFF_INSTRUCTIONS.reviewed.md` يشير إلى `../logs/raw/259_q028_handoff_packet_verification.log` — السجل موجود داخل المستودع في [`../../M8/logs/raw/259_q028_handoff_packet_verification.log`](../../M8/logs/raw/259_q028_handoff_packet_verification.log)؛ النسخة المراجَعة نفسها مجمّدة ولا تُحرَّر.

## ما أُصلح في الوثائق الحية (نفس الدفعة)

- [`ROADMAP.md`](../../../governance/ROADMAP.md): رابط سجل 289 كان ينقصه `../` (مسار منسوب خطأً إلى `docs/governance/`).
- [`R169_REPORT_V2.md`](../../M8/R169_REPORT_V2.md): رابط `R169_BENCHMARK_V2.md` كان ينقصه `../`؛ رابطا ملفَي f4 أُعيد توجيههما إلى `superseded_test_baseline_f4.jsonl` و`superseded_test_grounded-v2_f4.jsonl` — **مطابقان بالبايت** للملفين الأصليين المرتبطَين (نفس الـgit blob؛ سمّيا superseded في الدفعة 780078f حرفيًا «كما رُكِض»).
- [`EVIDENCE.md`](../../M8/EVIDENCE.md): رابطا ملفَي f4 نفسهما أُعيد توجيههما إلى النسخ superseded_ المطابقة.

لا حذف ولا نقل ولا إعادة تسمية في هذه الدفعة؛ لا نشر ولا إنفاق.
