# P2 — الرصد وعقود الخدمات (R-159 إلى R-161)

## R-159 — telemetry آمنة
أضف traces/metrics structured logs لـHTTP request وtask وworkflow stage وagent وtool وmemory وLLM provider. اربطها بـtrace/task IDs، مع tenant pseudonymized أو منخفض الحساسية. لا تسجل prompts/results/credentials افتراضيًا.
**اختبار:** trace كامل من API إلى stage إلى tool/model إلى storage؛ redaction assertions؛ قياس overhead؛ لا cardinality explosion من task IDs في metrics.

## R-160 — عقود قابلة للتحقق
وثق schemas لسلوك API/SSE/task result/tool/memory/queue/storage وAPI versions/compat. اختبر contract بين الواجهة وAPI والمخزن والـworker.
**اختبار:** valid, invalid, unknown fields, malformed outputs, version drift, cancelled request, duplicate request.

## R-161 — تكاملات حقيقية في staging
جهز jobs آمنة قابلة للتشغيل الاختياري لاتصال PostgreSQL/PostgREST/Supabase، مزود LLM، MCP servers وأدوات خارجية. سجل mock vs local integration vs live integration.
**القبول:** secrets مؤقتة وآمنة؛ لا أسرار في artifact؛ فشل المزود لا يعلن success؛ اختبارات unit لا تُحسب live.
