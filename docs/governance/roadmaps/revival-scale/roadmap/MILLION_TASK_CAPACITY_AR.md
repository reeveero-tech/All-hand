# تعريف وقياس سعة مليون مهمة

## منع الالتباس في كلمة «متزامنة»

يجب إبلاغ المستخدم بالأرقام منفصلة، ولا يجوز استخدام كلمة `concurrent` بلا تعريف:

1. **Submitted/accepted:** مهام قُبلت API وأُسند لها ID.
2. **Queued:** مهام تنتظر موارد تنفيذ.
3. **In-flight:** مهام في دورة حياة نشطة تشمل waiting/retry/claimed.
4. **Claimed:** مهمة أخذها عامل بlease.
5. **Executing:** كود محلي/أداة/نموذج يعمل فعليًا الآن.
6. **External provider calls:** عمليات خارجية متزامنة ومحدودة حسب حصص المزود.
7. **Completed/failed/cancelled/DLQ:** حالات نهائية.

الهدف الأول المعلن في هذه الخطة هو مليون سجل مهمة متين و/أو مهمة مقبولة/مجدولة داخل بنية موزعة مع إدارة حالة سليمة. يجب كتابة القياس الفعلي بدقة: كم منها كانت queued وكم كانت running في اللحظة نفسها. لا تنسب مليون execution نشط إلى اختبار مليون سجل.

## بنية ينبغي اختبارها

- مدخل API محدود الاتصالات/المعدل ولا يحتفظ بحمولة مهمة كاملة في RAM بلا حد.
- مخزن حالة/metadata دائم، وطابور موزع durable، وعمال منفصلون، وسياسة lease/visibility timeout، وDLQ، وإعادة تشغيل واستعادة.
- `task_id`, tenant scope، idempotency key، retry count، creation/deadline timestamps، schema/version، status transitions، cancellation state، trace ID.
- عدم وضع كل العمل في قاموس عملية واحدة أو مهمة `asyncio` واحدة لكل سجل بلا حد معروف.
- rate limits مستقلة للمزود/الأداة، resource pools bounded، backpressure واضح عند الامتلاء؛ رفض/تأجيل الطلب مع status مناسب أفضل من قبول يكذب على العميل.
- عزل بين المستأجرين عند قراءة المهام والذاكرة والنتائج والسجلات.

## سلّم اختبار مقترح

| المستوى | الهدف | التحقق الأساسي |
|---|---:|---|
| L0 | 100 مهمة | correctness، الحالات، IDs، idempotency |
| L1 | 1,000 | baseline latency/throughput، memory |
| L2 | 10,000 | persistent queue، workers، bounded memory |
| L3 | 100,000 | restart recovery، DB connections، backlog draining |
| L4 | 250,000 | partial failures، retry storm، tenant fairness |
| L5 | 500,000 | durability/metadata indexing/capacity |
| L6 | 1,000,000 | distributed acceptance + completion/backlog/recovery reports |

تكرّر المستويات مع أنواع مهام مختلفة: قصيرة CPU، IO، LLM simulated، tools slow/failure، workflow متعدد المراحل، وطلبات cancel/retry. ابدأ باختبار replay/fake provider، ثم نسبة صغيرة من التكاملات الحية.

## ما الذي يُقاس؟

- قبول/إنجاز: accepted/sec، completed/sec، queue drain time، completion ratio، duplicate side effects، lost tasks.
- زمن: p50/p95/p99 قبول API، زمن انتظار الطابور، زمن تنفيذ العامل، end-to-end latency، مزود/tool latency.
- الموارد: CPU/RAM لكل API/worker/queue/DB، عدد connections، pool saturation، event-loop lag، garbage collection، open file descriptors.
- صحة البيانات: status reconciliation، عدم وجود مهمة معلقة بلا lease صالح، consistency بين API/DB/events، tenant isolation.
- التعافي: مدة استعادة queue بعد قتل worker، RTO/RPO المقاسان، عدد retries، DLQ contents، استئناف/إعادة تشغيل.
- تكلفة: provider calls، token usage، tool bills، CPU-hour، storage/egress، تكلفة لكل مهمة مكتملة.

## إيقاف آمن

أوقف تلقائيًا إن ظهرت مؤشرات تسرّب/تجاوز صلاحيات، أو فقد بيانات، أو انهيار متكرر، أو تجاوز نسبة أخطاء/زمن متفق عليها، أو استهلاك موارد/تكلفة يتجاوز حد الاختبار. لا يكون الاختبار على الإنتاج العام؛ استخدم بيئة منفصلة ببيانات وهمية ومفاتيح وأدوات sandbox.

## نموذج إبلاغ النتائج

اكتب دائمًا: commit SHA؛ مواصفات load generators؛ الموارد/عدد العقد؛ إصدار runtime وqueue/database؛ workload mix؛ ramp rate؛ total accepted; max queued; max executing; completed/failed/cancelled; p95/p99؛ RAM/CPU maxima؛ recovery result؛ cost estimate؛ violations؛ الملفات الخام وروابطها. أرفق run config والـraw logs والتقرير في الدفعة نفسها.
