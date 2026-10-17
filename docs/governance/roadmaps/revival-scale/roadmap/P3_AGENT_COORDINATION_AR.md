# P3 — أساس تنسيق 10,000 وكيل ومليون مهمة (R-162 إلى R-166)

## R-162 — سجل الوكلاء وعامل التنفيذ
افصل agent identity عن worker process وpool. أضف lifecycle states, heartbeat, capability, health, capacity, lease, drain/shutdown. اجعل تعريف 10,000 وكيل lightweight ولا تشغّل 10,000 OS threads أو عمليات بلا قياس.
**القبول:** إنشاء 10k منطقي؛ تسجيل فريد؛ حذف/توقف العامل من الجدول لا يترك مهام عالقة؛ memory/CPU measured.

## R-163 — durable queue وstate machine
اضبط حالات واضحة مع انتقالات مسموحة: accepted/queued/claimed/running/waiting/retrying/completed/failed/cancelled/dead-letter. امنع transitions متناقضة بين RAM/DB/events؛ leases تنتهي ويتم استرداد المهام بأمان.
**القبول:** restart وworker death وduplicate delivery وdatabase errors وcancellation؛ لا فقد صامت ولا cross-tenant reads.

## R-164 — backpressure وحصص الموارد
حدود لكل pool وخدمة؛ queue cap؛ fairness؛ provider concurrency/rate limits؛ circuit breaker؛ bounded retry with jitter؛ timeout/cancel propagation؛ Retry-After.
**القبول:** تشبع pool web لا يعطل local/net/storage؛ full queue يرفض أو يؤجل بوضوح؛ استعادة بعد زوال التشبع.

## R-165 — orchestration DAG
أضف dependencies ومخرجات typed artifacts/schema validation؛ retries على مستوى المرحلة؛ deadlines؛ budgets؛ resumable workflow. لا يعتمد stage على نص ملخص مبتور بدل artifact بنيوي.
**القبول:** workflow ناجح، stage failure، invalid artifact، cycle rejected، deadline/cancel، replay safe، report شامل.

## R-166 — التنسيق الجماعي
اختبر توزيع المهام على مختلف الاختصاصات، gather/reduce، رسائل idempotent ومحدودة، conflict/failover، duplicate/out-of-order/late response، loop limits وdelegation depth. استخدم محاكاة message layer ثم endpoints واقعية.
**القبول:** كشف فقد الرسائل، no hidden success، fairness، عدم تكرار side effects، وتقرير مقاييس على 100/1k/10k participants.
