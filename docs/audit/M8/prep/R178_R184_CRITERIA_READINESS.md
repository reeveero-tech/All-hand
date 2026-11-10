# تحليل جاهزية معايير القبول R-178..R-184 (تجهيز، D-146 — لا قرار فيه)

> **تجهيز للمالك، لا اعتماد لمعايير ولا تشغيل اختبارات كبيرة.** كل المعايير الـ42 في
> [`benchmarks/p6/acceptance_criteria.json`](../../../../benchmarks/p6/acceptance_criteria.json)
> حالتها `proposed`، ولا يُعتمد أي معيار ولا يُغيَّر أي حد إلا بقرار المالك (سلسلة R-178 محجوبة
> خلف R-177 وQ-023، والاختبارات الكبيرة لا تبدأ قبل اعتماد المعايير والبيئة). هذا الملف يحصر
> جاهزية أداة القياس لكل معيار ويصنف ما يمكن بناؤه الآن بأمان.

## 1. الخلاصة

- **42 معيارًا**: ‏R-178 ‏(12)، R-179 ‏(8)، R-180 ‏(8)، R-181 ‏(8)، R-182 ‏(2)، R-183 ‏(3)، R-184 ‏(1).
- **19 معيارًا له أداة قياس منفذة ومختبرة**: سلّم R-178 ‏(`benchmarks/r178/ladder.py`، 8 معايير)،
  حمل k6 لكل خدمة ‏(`scripts/r177_per_service.py`، 6)، صحة L0 ‏(`benchmarks/p6/l0_task_correctness.py`، 3)،
  نمط قياس fan-out ‏SSE ‏(1)، ومقعد كتالوج الأدوات ‏(1).
- **23 معيارًا أداة قياسها غير منفذة** (`tool=not_implemented`). تصنيفها:
  - **(أ) 5 معايير قابلة للبناء والاختبار محليًا الآن** بلا بيئة كبيرة وبلا قرار مالك — كلها على
    المزود الوهمي المعلن: `r180.retry_after_respected`، `r180.breaker_failures_before_open`،
    `r180.hanging_tasks`، `r180.oversize_not_rejected`، `r180.log_bytes_per_request`.
  - **(ب) 14 معيارًا يمكن بناء أدواتها الآن لكن تشغيلها المعتمد محجوب** خلف بيئة Q-023 أو حمل
    كبير: ‏`r179.*` ‏(4)، `r180.rss_growth_pct_per_hour`، `r180.oom_events`، `r181.*` ‏(8).
  - **(ج) 4 معايير تتطلب قرار المالك وبيئة/مفاتيح**: ‏`r182.*` ‏(staging + مفاتيح مزود حقيقية)،
    `r183.*` ‏(تمارين rollback/استعادة على بيئة)، و`r184` ‏(قرار الجاهزية نفسه GO/CONDITIONAL/NO-GO).
- **بناء الأداة لا يعني اعتماد المعيار ولا تشغيل الاختبار المعتمد** — كل أداة تُبنى في فرع مستقل
  باختبارات أحمر/أخضر، وتوثق كتجهيز، ويبقى تشغيلها فوق L0 محجوبًا بحرس Q-023.

## 2. ما يحتاج قرار المالك (مركز)

1. **اعتماد المعايير الـ42 أو تعديلها** — كلها `proposed`؛ بدء أي اختبار كبير مشروط باعتمادها.
2. **بيئة Q-023 والميزانية** — الخيارات والتكلفة في
   [`Q023_DECISION_REPORT.md`](Q023_DECISION_REPORT.md) (لا بديل مجاني بلا بطاقة يحقق المواصفة).
3. **قبول نطاق R-177 المخفض أو تجهيز بيئة Q-023** — شرط فك حجب R-178 بعد اكتمال R-177.
4. **R-182**: بيئة staging ومفاتيح مزود حقيقية وميزانية معتمدة.
5. **R-184**: قرار الجاهزية النهائي لا يُتخذ إلا على الأدلة المكتملة.

## 3. الجدول التفصيلي (42)

| المعيار | الوصف | الحد | الأداة | الجاهزية |
|---|---|---|---|---|
| `r178.creation_time_s` | زمن إنشاء 10k وكيل | <= 30 s | benchmarks/r178/ladder.py | منفذة |
| `r178.creation_peak_rss_bytes` | ذروة RSS لـSUT عند 10k وكيل | <= 2147483648 bytes | benchmarks/r178/ladder.py | منفذة |
| `r178.rss_per_agent_bytes` | RSS لكل وكيل (ميل خطي) | >= 0 bytes/agent | benchmarks/r178/ladder.py | منفذة |
| `r178.dispatch_p95_ms` | dispatch p95 | <= 500 ms | scripts/r177_per_service.py (k6) | منفذة |
| `r178.dispatch_p99_ms` | dispatch p99 | <= 1000 ms | scripts/r177_per_service.py (k6) | منفذة |
| `r178.queue_wait_p95_ms` | انتظار الطابور p95 | <= 2000 ms | benchmarks/r177/sse_fanout_bench.py (نمط القياس) | منفذة |
| `r178.error_rate` | نسبة الخطأ (0 أو 5xx) | < 0.01 ratio | scripts/r177_per_service.py | منفذة |
| `r178.worker_fairness_jain` | عدالة العمال (Jain) | >= 0.9 index | benchmarks/r178/ladder.py | منفذة |
| `r178.duplicate_side_effects` | آثار جانبية مكررة | == 0 count | benchmarks/r178/ladder.py | منفذة |
| `r178.specialization_imbalance` | اختلال التخصص max/min | <= 2.0 ratio | benchmarks/r178/ladder.py | منفذة |
| `r178.unstructured_refusals` | رفض غير منظم عند تجاوز الحدود | == 0 count | benchmarks/r178/ladder.py | منفذة |
| `r178.agents_lost_after_kill` | وكلاء مفقودون بعد قتل SUT (live-local) | == 0 count | benchmarks/r178/ladder.py | منفذة |
| `r179.silent_loss` | فقد صامت | == 0 count | benchmarks/p6/l0_task_correctness.py (L0 صحة فقط — ليس درجة من السلّم) | منفذة |
| `r179.duplicate_side_effects` | آثار جانبية مكررة | == 0 count | benchmarks/p6/l0_task_correctness.py (L0 صحة فقط — ليس درجة من السلّم) | منفذة |
| `r179.reconciliation_ratio` | مصالحة API = DB = أحداث | == 1.0 ratio | benchmarks/p6/l0_task_correctness.py (L0 صحة فقط — ليس درجة من السلّم) | منفذة |
| `r179.accept_p95_ms` | قبول API p95 | <= 500 ms | scripts/r177_per_service.py (k6) | منفذة |
| `r179.unstructured_refusals` | رفض كاذب/غير منظم بعد الامتلاء | == 0 count | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r179.rpo_lost_accepted` | RPO: مقبولة مفقودة بعد القتل (وضع متين) | == 0 count | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r179.rto_s` | RTO بعد قتل SUT | <= 120 s | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r179.orphan_resume_excess_s` | استئناف اليتيمة: الزمن ناقص TTL الـlease | <= 60 s | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r180.rss_growth_pct_per_hour` | نمو RSS بعد الإحماء | <= 5 %/h | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r180.catalog_over_budget` | بحث 10k أداة خارج الميزانية تحت الحمل | == 0 count | agent_civilization/core/tool_catalog_bench.py | منفذة |
| `r180.retry_after_respected` | احترام Retry-After من المزود الوهمي | == 1.0 ratio | not_implemented | أ (بناء محلي ممكن) |
| `r180.breaker_failures_before_open` | إخفاقات قبل فتح القاطع | <= 5 count | not_implemented | أ (بناء محلي ممكن) |
| `r180.hanging_tasks` | مهام معلقة بلا حالة بعد 429/timeout | == 0 count | not_implemented | أ (بناء محلي ممكن) |
| `r180.oversize_not_rejected` | حمولات فوق الحد لم تُرفض بـ413/422 | == 0 count | not_implemented | أ (بناء محلي ممكن) |
| `r180.oom_events` | أحداث OOM | == 0 count | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r180.log_bytes_per_request` | حجم السجل لكل طلب | <= 1024 bytes | not_implemented | أ (بناء محلي ممكن) |
| `r181.accepted_lost` | مهام مقبولة مفقودة (لكل سيناريو) | == 0 count | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r181.isolation_leaks` | تسرب بين المستأجرين | == 0 count | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r181.rto_s` | RTO لكل سيناريو | <= 120 s | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r181.soak_p95_drift_pct` | انجراف p95 الساعة الأخيرة مقابل الأولى | <= 20 % | scripts/r177_per_service.py (k6) | منفذة |
| `r181.soak_rss_growth_pct` | نمو RSS خلال soak | <= 10 % | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r181.soak_error_rate` | نسبة الخطأ خلال soak | < 0.01 ratio | scripts/r177_per_service.py | منفذة |
| `r181.soak_fd_monotonic_fraction` | fd: نسبة الخطوات المتزايدة | <= 0.6 ratio | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r181.soak_fd_growth_pct` | نمو fd خلال soak | <= 10 % | not_implemented | ب (بناء ممكن، تشغيل محجوب) |
| `r182.live_checks_failed` | فحوص حية فاشلة على staging | == 0 count | not_implemented | ج (قرار مالك + بيئة) |
| `r182.secrets_in_logs` | أسرار في السجلات المرفقة | == 0 count | not_implemented | ج (قرار مالك + بيئة) |
| `r183.rollback_time_s` | زمن تمرين rollback | <= 900 s | not_implemented | ج (قرار مالك + بيئة) |
| `r183.restore_rpo_s` | RPO مقيس لتمرين الاستعادة | >= 0 s | not_implemented | ج (قرار مالك + بيئة) |
| `r183.runbooks_missing` | runbooks ناقصة | == 0 count | not_implemented | ج (قرار مالك + بيئة) |
| `r184.critical_high_open` | مخاطر critical/high مفتوحة | == 0 count | not_implemented | ج (قرار مالك + بيئة) |
## 4. ملاحظات دقيقة (من فحص الأدوات الموجودة)

- **سلّم R-178** (`benchmarks/r178/ladder.py`) محروس بـQ-023: يرفض أي درجة فوق L0 (صحة) بلا
  تجاوز مزدوج من المالك، ومختبر على L0 ×3 (السجلات 382/383 و`r178_l0/`). جاهز للاستخدام عند فك الحجب.
- **قياسات R-177 الحالية** (خطوط الأساس والأحمال والsoak والأعطال) تغطي أدوات 6 معايير من R-178
  لكن على بيئة مخفضة — لا تصلح دليلًا معتمدًا لسعة 10k وكيل (نفس قاعدة «بيئة مخفضة معلنة»).
- **معايير (أ) الخمسة** سياسات مزود LLM (احترام Retry-After، القاطع، المهام المعلقة، رفض
  الحمولات الزائدة، حجم السجل): كلها قابلة للبناء على المزود الوهمي المعلن باختبارات أحمر/أخضر
  محلية خفيفة، بلا أي تأثير على الإنتاج.
- **معايير (ب)**: أدوات مثل مسبار RTO واستئناف المهام اليتيمة ومقاييس نمو RSS/fd خلال soak يمكن
  بناؤها وتشغيلها على بيئة مخفضة كتجهيز، لكن نتيجتها لا تُقدَّم دليلًا معتمدًا للمعيار.
- **لا معيار واحد تغير حدُّه أو اعتُمد في هذا التجهيز.**

## 5. الخطوة المقترحة بعد قرار المالك

بناء أدوات الفئة (أ) الخمس كدفعة أولى (فرع مستقل، أحمر/أخضر، بلا تشغيل معتمد)، ثم أدوات الفئة (ب)
كمرحلة ثانية، مع بقاء الفئة (ج) مرتبطة بالكامل بقرارات البيئة والمفاتيح والميزانية.
