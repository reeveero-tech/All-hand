# سجل كل تشغيلات R-169 وتصنيفها (2026-10-25)

> مولَّد آليًا وغير متصل: `python3 scripts/r169_run_ledger.py` ⇒ `RUN_LEDGER.json` (البيانات الكاملة لكل تشغيل).
> **لا استدعاء لأي مزود، ولا تقييم جديد.** الاختبار `tests/test_r169_run_ledger.py` يفشل إن تغيرت ملفات التشغيل دون إعادة توليد السجل.
> R-169 تبقى `done` بنطاقها الأصلي؛ تراجع اللغة مفتوح وغير محلول.

## تحديث بعد تشغيل التحقق r3 (2026-10-25، بموافقة المالك)
- أُضيف تشغيلان، **كلاهما صالح** (46/46، استدعى المزود، 0 أخطاء، شجرة نظيفة على main ‏e9732837): `validation_grounded-v2_r3` و`validation_grounded-v3-lang_r3`. المجموع الآن **37 تشغيلًا، 23 صالحًا**.
- استقلال r3 عن r1 المتوقف: 1/33 رد مطابق فقط؛ 12 حالة فيها استدعاء من ذاكرة المزود (`X-Cache: HIT`) ولا رد نهائي منها مطابق.
- **النتيجة وفق §5 المسجل مسبقًا: A1 نجح، A2 فشل، A3 نجح ⇒ إصلاح اللغة «لم يثبت».** التفاصيل في السجل `296`. التشغيل الوصفي `baseline` لم يكتمل عند كتابة هذا التحديث (غير مُلتزم، لا يدخل في القرار).
- الأقسام أدناه كما كُتبت قبل r3 (لم تُعدَّل بأثر رجعي).

## كيف صُنّف كل تشغيل (قواعد آلية، لا اجتهاد)
- **استدعى المزود؟** لكل سجل: رد غير فارغ بلا خطأ ⇒ استُدعي وأجاب؛ خطأ إعداد («not configured»/موافقة) ⇒ **لم يُستدعَ**؛ أي خطأ آخر ⇒ استُدعي وفشل.
- **مكتمل؟** عدد الحالات الفريدة مقابل حجم القسم في `benchmarks/r169/vN/` (ومع مرشح اللغة إن وُجد).
- **مستقل؟** مقارنة بصمة كل رد مع أي تشغيل **سابق** لنفس (النسخة، القسم، الإعداد)؛ تطابق ≥95% على ≥10 حالات ⇒ «مكرر/غير مستقل» (ذاكرة المزود المؤقتة).
- **صالح للتحليل** = مكتمل ∧ مستدعى فعلًا ∧ مستقل ∧ غير مستبدَل ∧ أخطاء المزود ≤10% ∧ لا معرّفات مكررة.
  «صالح» هنا يعني أن **مجموعة الردود** سليمة؛ لا يعني أن المصحح المستخدم وقتها ما زال المعتمد (المصححات تغيرت بين v1 وv5).
- القسم: dev = تطوير، validation = تحقق، test = اختبار محجوز. التشغيلات على شجرة غير نظيفة صالحة للتطوير فقط.

## الأرقام
- **35 تشغيلًا** (كل ما في المستودع، بما فيه الفاشل والمتوقف والمكرر والمستبدَل) — 2094 سجلًا: 2064 ردًا فعليًا، 24 خطأ مزود، 6 بلا استدعاء.
- استدعى المزود فعلًا: **34/35**. الوحيد الذي لم يستدعِه: `v5/lang_pilot/dev_grounded-v3-lang_ar.jsonl` (6/6 «OPENROUTER_API_KEY is not configured»).
- صالح للتحليل: **21** (test 10، dev 9، validation 2 — كلاهما v1 على شجرة غير نظيفة).
- غير صالح: 5 مكرر/غير مستقل، 6 ناقص/متوقف، 1 أخطاء مزود >10% (`dev_grounded-v2_t3`: 4/30 مهلة)، 1 مستبدَل، 1 بلا استدعاء.

## ما يعنيه ذلك لتجربة إصلاح اللغة (الأهم)
- **لا يوجد أي تشغيل validation صالح على v5.** تشغيلا التحقق الوحيدان (`validation_grounded-v2_r1` ‏33/46، `_r2` ‏11/46) متوقفان، ولم يُشغَّل `grounded-v3-lang` على validation إطلاقًا.
  ⇒ **إصلاح اللغة غير متحقق منه.** المرحلة 1 (dev، 26 سؤالًا عربيًا، `dev_grounded-v3-lang_ar_r1`) مكتملة لكنها تطوير وليست قبولًا.
- دليل التراجع نفسه قائم على test (b2r2 وb4s صالحان ومستقلان عن بعضهما: تطابق الردود 7/118 فقط بفضل seed لكل طلب).
- b3 (كلا الإعدادين) غير مستقل: 118/118 و117/117 ردًا مطابقًا لـb2 بايتًا. كذلك v2 ‏f3/f4 مطابقة لما قبلها (المزود كان يعيد ردودًا مخزنة منذ v2).
- تناقض موثّق ولم يُصحَّح بأثر رجعي: §8 في التسجيل المسبق يقول `validation_grounded-v2_r1` ‏«32/46» بينما الملف يحوي **33** سجلًا. لا أثر على أي نتيجة (التشغيل لا يُقيَّم).
- حماية أُضيفت: `scripts/r169_lang_pilot.py score` يرفض الآن الوسمين `r1`/`r2` (الافتراضي صار `r3`) كي لا يُقيَّم تشغيل متوقف خطأً.

## الجاهزية للتحقق (عند موافقة المالك فقط — لم يُشغَّل شيء)
- البروتوكول مسجل مسبقًا وملتزم قبل أي درجة: `v5/lang_pilot/PREREGISTRATION.md` §5 (A1–A3) و§8 (r3).
  يقيس **مطابقة اللغة وجودة الإجابة (`pass`) معًا** على الحالات نفسها، مزدوجًا (McNemar)، مع baseline وصفيًا.
- المطلوب: 3 تشغيلات validation × 46 = 138 طلبًا عبر المزود المجاني بلا مفتاح `text.pollinations.ai` (لا مفتاح مدفوع؛ المشغّل يرفض وجود مفتاح مدفوع).
  الزمن الوسيط في تشغيلات الإصلاح الأخيرة ≈ 65 ث/طلب ⇒ تقدير ≈ 2.5 ساعة متتابعة (تقدير، غير متحقق منه). مهلة 600 ث لكل محاولة.
- البيانات المرسلة للمزود: أسئلة ومصادر اصطناعية من المعيار فقط، بلا أسرار أو بيانات مستخدمين.
- حتى مع نجاح A1–A3 النتيجة «واعدة» فقط؛ التأكيد يحتاج holdout جديدًا (v6) لم يُبنَ بعد. n صغير (23 عربيًا) ⇒ لا ادعاء دلالة.

## الجدول الكامل
| # | التشغيل | النسخة | القسم | الإعداد | السجلات/المتوقع | استدعى المزود | أخطاء المزود | الحالة | السبب |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `dev_baseline_t1.jsonl` | v1 | dev | baseline | 30/30 | نعم | 1 | صالح | — |
| 2 | `dev_grounded-v2_t1.jsonl` | v1 | dev | grounded-v2 | 30/30 | نعم | 0 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 3 | `dev_grounded-v2_t2.jsonl` | v1 | dev | grounded-v2 | 30/30 | نعم | 0 | صالح | — |
| 4 | `validation_baseline_t1.jsonl` | v1 | validation | baseline | 28/28 | نعم | 1 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 5 | `validation_grounded-v2_t1.jsonl` | v1 | validation | grounded-v2 | 28/28 | نعم | 2 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 6 | `dev_grounded-v2_t3.jsonl` | v1 | dev | grounded-v2 | 30/30 | نعم | 4 | أخطاء مزود >10% | provider errors 4/30 > 10% |
| 7 | `test_baseline_f1.jsonl` | v1 | test | baseline | 84/84 | نعم | 3 | صالح | — |
| 8 | `test_grounded-v2_f1.jsonl` | v1 | test | grounded-v2 | 84/84 | نعم | 0 | صالح | — |
| 9 | `v2/dev_baseline_d1.jsonl` | v2 | dev | baseline | 30/30 | نعم | 0 | صالح | — |
| 10 | `v2/superseded_test_baseline_f2_partial.jsonl` | v2 | test | baseline | 21/84 | نعم | 0 | ناقص/متوقف | incomplete 21/84؛ marked superseded (pre-audit scorer) |
| 11 | `v2/superseded_test_baseline_f3.jsonl` | v2 | test | baseline | 84/84 | نعم | 3 | مكرر/غير مستقل | replies byte-identical to earlier run v2/superseded_test_baseline_f2_partial.jsonl — not an independent sample؛ marked superseded (pre-audit scorer) |
| 12 | `v2/superseded_test_grounded-v2_f3.jsonl` | v2 | test | grounded-v2 | 84/84 | نعم | 0 | مستبدَل | marked superseded (pre-audit scorer) |
| 13 | `v2/superseded_test_baseline_f4.jsonl` | v2 | test | baseline | 84/84 | نعم | 3 | مكرر/غير مستقل | replies byte-identical to earlier run v2/superseded_test_baseline_f2_partial.jsonl — not an independent sample؛ marked superseded (pre-audit scorer) |
| 14 | `v2/superseded_test_grounded-v2_f4.jsonl` | v2 | test | grounded-v2 | 84/84 | نعم | 0 | مكرر/غير مستقل | replies byte-identical to earlier run v2/superseded_test_grounded-v2_f3.jsonl — not an independent sample؛ marked superseded (pre-audit scorer) |
| 15 | `v3/dev_baseline_d1.jsonl` | v3 | dev | baseline | 30/30 | نعم | 0 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 16 | `v3/dev_grounded-v2_d1.jsonl` | v3 | dev | grounded-v2 | 30/30 | نعم | 0 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 17 | `v3/test_baseline_a1.jsonl` | v3 | test | baseline | 84/84 | نعم | 0 | صالح | — |
| 18 | `v3/test_grounded-v2_a1.jsonl` | v3 | test | grounded-v2 | 84/84 | نعم | 1 | صالح | — |
| 19 | `v4/dev_baseline_d1.jsonl` | v4 | dev | baseline | 52/52 | نعم | 0 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 20 | `v4/dev_grounded-v2_d1.jsonl` | v4 | dev | grounded-v2 | 52/52 | نعم | 0 | صالح | شجرة غير نظيفة (تطوير فقط) |
| 21 | `v4/test_baseline_b1.jsonl` | v4 | test | baseline | 118/118 | نعم | 1 | صالح | — |
| 22 | `v4/test_grounded-v2_b1.jsonl` | v4 | test | grounded-v2 | 118/118 | نعم | 0 | صالح | — |
| 23 | `v5/test_baseline_b2.jsonl` | v5 | test | baseline | 118/118 | نعم | 1 | صالح | — |
| 24 | `v5/test_grounded-v2_b2.jsonl` | v5 | test | grounded-v2 | 6/118 | نعم | 0 | ناقص/متوقف | incomplete 6/118 |
| 25 | `v5/test_grounded-v2_b2r2.jsonl` | v5 | test | grounded-v2 | 118/118 | نعم | 0 | صالح | — |
| 26 | `v5/test_grounded-v2_b3.jsonl` | v5 | test | grounded-v2 | 118/118 | نعم | 0 | مكرر/غير مستقل | replies byte-identical to earlier run v5/test_grounded-v2_b2r2.jsonl — not an independent sample |
| 27 | `v5/test_baseline_b3.jsonl` | v5 | test | baseline | 118/118 | نعم | 1 | مكرر/غير مستقل | replies byte-identical to earlier run v5/test_baseline_b2.jsonl — not an independent sample |
| 28 | `v5/b4/test_grounded-v2_b4.jsonl` | v5 | test | grounded-v2 | 3/118 | نعم | 2 | ناقص/متوقف | incomplete 3/118؛ provider errors 2/3 > 10% |
| 29 | `v5/b4/test_baseline_b4.jsonl` | v5 | test | baseline | 2/118 | نعم | 1 | ناقص/متوقف | incomplete 2/118؛ provider errors 1/2 > 10% |
| 30 | `v5/b4/test_grounded-v2_b4s.jsonl` | v5 | test | grounded-v2 | 118/118 | نعم | 0 | صالح | — |
| 31 | `v5/b4/test_baseline_b4s.jsonl` | v5 | test | baseline | 118/118 | نعم | 0 | صالح | — |
| 32 | `v5/lang_pilot/dev_grounded-v3-lang_ar.jsonl` | v5 | dev | grounded-v3-lang | 6/26 | لا | 0 | بلا استدعاء | provider never called (configuration error on every record)؛ incomplete 6/26 |
| 33 | `v5/lang_pilot/dev_grounded-v3-lang_ar_r1.jsonl` | v5 | dev | grounded-v3-lang | 26/26 | نعم | 0 | صالح | — |
| 34 | `v5/lang_pilot/validation_grounded-v2_r1.jsonl` | v5 | validation | grounded-v2 | 33/46 | نعم | 0 | ناقص/متوقف | incomplete 33/46 |
| 35 | `v5/lang_pilot/validation_grounded-v2_r2.jsonl` | v5 | validation | grounded-v2 | 11/46 | نعم | 0 | ناقص/متوقف | incomplete 11/46 |
| 36 | `v5/lang_pilot/validation_grounded-v2_r3.jsonl` | v5 | validation | grounded-v2 | 46/46 | نعم | 0 | صالح | — |
| 37 | `v5/lang_pilot/validation_grounded-v3-lang_r3.jsonl` | v5 | validation | grounded-v3-lang | 46/46 | نعم | 0 | صالح | — |
