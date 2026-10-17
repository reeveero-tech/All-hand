# مصفوفة الاختبارات الإلزامية

يُضاف أو يُعدل كل اختبار ضمن نفس PR التي تعالج السلوك. لكل اختبار سجل في `templates/test_evidence_template.csv`.

| المجال | Unit/Contract | Integration/E2E | Load/Stress/Soak/Chaos | معيار القبول |
|---|---|---|---|---|
| الملفات والوحدات | import/compile/schema/static analysis | تشغيل entrypoints والواجهات العامة | قياس حجم الحزم وزمن startup | جرد كل الملفات وأدلة لكل نوع مناسب |
| الخدمات | عقود REST/JSON/SSE | ربط API/DB/queue/LLM/tool | RPS، p95/p99، أخطاء، backpressure | SLO محددة قبل القياس ونجاح ضمن البيئة المستهدفة |
| الوكلاء | لكل تخصص payloads ناجحة وفاشلة | تنفيذ عبر server/router/orchestrator | سلالم 100→10k agent، skew، موارد | نجاح النتائج + عدالة الجدولة + فشل واسترداد مضبوط |
| التنسيق | عقد DAG/agent messaging | workflow متعدد التخصصات | 100/1k/10k participants، الرسائل المتأخرة والمكررة | لا مراحل مفقودة، أثر التكرار محمي، نهائي قابل للتحقق |
| المهام | state machine/idempotency | enqueue→claim→run→persist→read | 10k→1m records/inflight، restart | لا فقد صامت؛ حالات نهائية متسقة؛ latency/error reported |
| الذاكرة | search/rank/prune/types | API reads/writes/tenant scope | ملايين/مئات آلاف entries حسب الموارد | لا cross-tenant leaks؛ استعادة العملية الفعلية مثبتة |
| التعلم | prior/update/replay | outcomes حقيقية عبر عدة مهام | تدهور جودة/انحياز مع التراكم | الأثر القابل للقياس موثق، ولا يخلط النجاح الحقيقي بالفشل المعوض |
| LLM/هلوسة | benchmark assertions | أدوات ومصادر حقيقية أو test doubles مصنفة | تغيرات نموذج/ضغط/timeout | groundedness/abstention/faithfulness أعلى من حدود مسجلة |
| الأدوات | schema, permissions, validation | call each adapter on sandbox/provider test | catalog 100/1k/10k; saturated endpoints | أداة certified لها دليل execution وأمن وversion |
| أمن | auth/SSRF/secrets/input validation | tenant isolation / malicious tool | fuzz/adversarial/chaos | صفر فشل حرج مفتوح؛ الرفض الآمن عند الشك |
| تخزين | serialize/deserialize/migrations | PostgreSQL/PostgREST/Supabase بحسب البيئة | failover/write bursts/reconnect | durability/consistency/RPO/RTO معلنة ومثبتة |
| الواجهة | JS unit/API contract | browser smoke وflows | مستخدمون/جلسات عند الحاجة | أخطاء API وتجربة الفشل موثقة |
| النشر | config validation/build | staging deploy & health/readiness | rollout/rollback/soak | نسخة SHA معروف، صحة وتكاملات وأدلة حية |

## أنواع الاختبارات والتصنيف

- `unit`: وظيفة بمعزل، غالبًا بلا شبكة.
- `contract`: تحقق من API/JSON/schema وسلوك واجهة قديمة.
- `integration`: أكثر من مكون حقيقي داخل الاختبار.
- `live`: خدمة/اعتماد خارجي حقيقي؛ تذكر البيئة ونوع credentials دون سر.
- `load`: حمولة ضمن SLO معلوم.
- `stress`: تصعيد لما بعد السعة المقبولة حتى نقطة الانكسار ثم توقف آمن.
- `spike`: تغير سريع في المعدل.
- `soak`: مدة طويلة لاكتشاف leak والتدهور.
- `chaos/recovery`: فشل متعمد ثم فحص الاسترداد.
- `eval/red-team`: جودة إجابات/هلوسة/سلوك أداة adversarial.

لا تدمج نتيجتين من نوعين مختلفين في ادعاء واحد. مثال: «1000 وكيل أُنشئ» لا يساوي «1000 وكيل نفذ عملًا ناجحًا»، و«1000 task تم قبولها» لا يساوي «1000 task اكتملت بنجاح».

## اختبارات خاصة بحدود الهدف

- الأحمال الكبيرة تُنفذ في بيئة staging/load منفصلة ومضبوطة، وبميزانية معدة مسبقًا.
- لا تستخدم أسرار الإنتاج ولا بيانات حقيقية للمستخدمين.
- يجب أن يتضمن كل سيناريو stop conditions: نسبة أخطاء، زمن استجابة، backlog، memory/CPU، تجاوز التكلفة، أو مؤشرات عدم سلامة.
- لا تُشغّل مليون استدعاء حقيقي لـLLM كاختبار افتراضي؛ استخدم fake provider أو replay أولًا، ثم live sample محدود ومعتمد.
