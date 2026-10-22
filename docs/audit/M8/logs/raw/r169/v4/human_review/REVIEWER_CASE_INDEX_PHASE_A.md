# فهرس حالات Q-028 — R-169 v4 b1 (مرحلة أ فقط)

> هذا ملف مساعد محايد للمرحلة الأولى من [إجراء المراجعة العمياء ذات المرحلتين](BLIND_REVIEW_PROTOCOL.md).
> يعرض معرف الحالة والتشغيل وموضع الرد وبصمته فقط. لا يعرض فئة اختيار الحالة أو وسم الوكيل أو حكم المصحح أو نتيجة القياس.
> ترتيب العرض مخلوط ترتيبًا حتميًا حسب `run::case_id`، دون تغيير ترتيب أي رد أو ملف قياس.
> **لا تفتح الفهرس الأصلي `REVIEWER_CASE_INDEX.md` قبل تثبيت المرحلة أ**؛ يحتوي ذلك الملف على أسباب المصحح للمرحلة ب.

## شروط استقلال المراجع

- يجب أن تكون إنسانًا لم يطوّر المصحح v1–v4 ولم يضبطه، ولم يطّلع مسبقًا على test أو أحكامها.
- يجب ألا تكون الوكيل الذي أنتج الوسوم أو أي نموذج اصطناعي.
- استخدم [نموذج المرحلة أ](reviewer_blind_form.json)، وسجل حكمك وتعليلك وثقتك قبل رؤية أي نتيجة سابقة.

## قائمة الحالات المحايدة

| # | معرف الحالة | التشغيل | الورقة:السطر | بصمة الرد (SHA-256 أول 12) |
|---|---|---|---|---|
| 1 | `r169-v4-abstain_missing-02-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:349`](../blind/test_baseline_b1.sheet.md#L349) | `3079422aec5e` |
| 2 | `r169-v4-abstain_missing-12-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:574`](../blind/test_baseline_b1.sheet.md#L574) | `19b40e5ada0f` |
| 3 | `r169-v4-tool_poisoning-13-ar` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:1625`](../blind/test_grounded-v2_b1.sheet.md#L1625) | `0947cb19ad91` |
| 4 | `r169-v4-code_task-14-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:2293`](../blind/test_baseline_b1.sheet.md#L2293) | `8798d38f7891` |
| 5 | `r169-v4-abstain_missing-07-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:455`](../blind/test_baseline_b1.sheet.md#L455) | `3529d818ef25` |
| 6 | `r169-v4-abstain_missing-07-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:472`](../blind/test_baseline_b1.sheet.md#L472) | `6cdc5059e371` |
| 7 | `r169-v4-abstain_missing-12-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:557`](../blind/test_baseline_b1.sheet.md#L557) | `ec5a61797056` |
| 8 | `r169-v4-closed_book-03-ar` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:2265`](../blind/test_grounded-v2_b1.sheet.md#L2265) | `8a6b10089bed` |
| 9 | `r169-v4-closed_book-02-en` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:2220`](../blind/test_grounded-v2_b1.sheet.md#L2220) | `84589d2b8ab6` |
| 10 | `r169-v4-abstain_missing-08-ar` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:502`](../blind/test_grounded-v2_b1.sheet.md#L502) | `9effba649484` |
| 11 | `r169-v4-abstain_missing-08-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:506`](../blind/test_baseline_b1.sheet.md#L506) | `be5be9b276d5` |
| 12 | `r169-v4-abstain_missing-03-ar` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:400`](../blind/test_grounded-v2_b1.sheet.md#L400) | `1d1828447240` |
| 13 | `r169-v4-abstain_missing-08-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:489`](../blind/test_baseline_b1.sheet.md#L489) | `060357db96b2` |
| 14 | `r169-v4-tool_poisoning-03-en` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:1404`](../blind/test_grounded-v2_b1.sheet.md#L1404) | `e8929783a2d9` |
| 15 | `r169-v4-stale_sources-03-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:1018`](../blind/test_baseline_b1.sheet.md#L1018) | `012f15e9255b` |
| 16 | `r169-v4-tool_poisoning-03-ar` | test_grounded-v2_b1 | [`test_grounded-v2_b1.sheet.md:1421`](../blind/test_grounded-v2_b1.sheet.md#L1421) | `f7b677ce9e2c` |
| 17 | `r169-v4-conflicting_sources-07-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:937`](../blind/test_baseline_b1.sheet.md#L937) | `1334633131b9` |
| 18 | `r169-v4-abstain_missing-09-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:540`](../blind/test_baseline_b1.sheet.md#L540) | `e8efa63a77cb` |
| 19 | `r169-v4-abstain_missing-09-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:523`](../blind/test_baseline_b1.sheet.md#L523) | `22d17da71bbb` |
| 20 | `r169-v4-abstain_missing-04-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:421`](../blind/test_baseline_b1.sheet.md#L421) | `af600be7046b` |
| 21 | `r169-v4-abstain_missing-04-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:438`](../blind/test_baseline_b1.sheet.md#L438) | `92c88788c135` |
| 22 | `r169-v4-code_task-12-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:2095`](../blind/test_baseline_b1.sheet.md#L2095) | `ac26dcd4eada` |
| 23 | `r169-v4-conflicting_sources-04-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:899`](../blind/test_baseline_b1.sheet.md#L899) | `c65980a74ef8` |
| 24 | `r169-v4-conflicting_sources-03-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:841`](../blind/test_baseline_b1.sheet.md#L841) | `f873941140be` |
| 25 | `r169-v4-conflicting_sources-03-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:861`](../blind/test_baseline_b1.sheet.md#L861) | `000acdac21e5` |
| 26 | `r169-v4-abstain_missing-03-en` | test_baseline_b1 | [`test_baseline_b1.sheet.md:383`](../blind/test_baseline_b1.sheet.md#L383) | `1aaecc886b5c` |
| 27 | `r169-v4-abstain_missing-02-ar` | test_baseline_b1 | [`test_baseline_b1.sheet.md:366`](../blind/test_baseline_b1.sheet.md#L366) | `492e0ad26794` |
