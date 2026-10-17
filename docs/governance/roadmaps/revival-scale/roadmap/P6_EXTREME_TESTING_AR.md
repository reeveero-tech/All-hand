# P6 — ضغط كل خدمة حتى نقطة الانكسار بأمان (R-176 إلى R-181)

## R-176 — أدوات قياس الحمل
اختر إطار الحمل بعد مراجعة المتطلبات: k6 لاختبارات HTTP/open-model arrival rate، وLocust لسيناريوهات مستخدمين/مهام Python إذا أفادت. ثبّت نسخ الأدوات. workload generator على أجهزة/عقد منفصلة عن الخدمة. CI يعمل smoke/load صغيرًا؛ الحمل الكبير يدوي/مجدوَل ببيئة مخصصة.
**القبول:** config/version/workload profile/raw results/SHA؛ لا يختلط ضغط generator مع ضغط SUT.

## R-177 — ضغط كل عنصر
لكل خدمة وعامل وملف تنفيذي حساس: baseline, normal load, stress, spike, soak, failure & recovery. تشمل: API, auth, task queue, worker pools, orchestrator, memory, storage, LLM provider adapter, each tool family, SSE, frontend API paths. يحدد مالك الاختبار SLO وstop limits قبل التشغيل.
**القبول:** سجل لكل نوع خدمة؛ نجاح/فشل/تخطي مصنف؛ نقطة التشبع وحدودها مفهومة.

## R-178 — ضغط 10,000 وكيل
سلالم عددية: 100→500→1k→2.5k→5k→10k. فصل creation benchmark عن execution benchmark. احسب success/error, queue wait, dispatch p95/p99, memory/CPU, worker fairness, duplicate work, specialization imbalance. جرب `real` و`specialists` كلًا على حدة ثم التكامل المعتمد.
**القبول:** 10k creation وexecution claims مدعومة بسجلات؛ إذا لم تتحقق السعة فوثق limit وأصلحها قبل رفع المستوى؛ لا أرقام وهمية.

## R-179 — ضغط مليون مهمة
سلالم 10k→100k→250k→500k→1m. ابدأ على fake provider وsynthetic tools، ثم integration slice محدود. قِس accepted/queued/claimed/executing/completed separately. كرر benchmark مع worker death/restart وreplay وtimeouts.
**القبول:** لا فقد مهمة صامت، state transitions متسقة، recovery موثق، SLO وcost/resource report؛ لا تدعي مليون execution حي إذا كان الاختبار مليون queued records.

## R-180 — ضغط ذاكرة/كتالوج/مزود
اختبر memory growth/index, concurrent reads/writes, tool registry 10k lookup, provider rate-limit/429/timeouts, invalid results, huge payload limits, queue floods, cold starts. خفّض بيانات logging لتمنع ضغط التخزين بسبب telemetry.
**القبول:** resource caps, graceful reject/fallback, no leaks; measured results and stop conditions.

## R-181 — chaos and soak
عدة سيناريوهات: قتل worker، DB disconnect، queue restart، slow LLM، corrupted tool output، DNS/HTTP failure، cancellation أثناء queue، retry storm، ضغط بين مستأجرين، وتشغيل مستمر لعدة ساعات في بيئة staging آمنة. قس leak من الفرق بين بدايات ونهايات الذاكرة وlatency/errors.
**القبول:** recovery SLO/RTO/RPO المعلن متحقق أو الفشل موثق ويمنع `accepted`; لا اختبارات مدمرة على الإنتاج العام.
