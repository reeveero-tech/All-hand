# P7 — القبول النهائي وجاهزية الإنتاج (R-182 إلى R-184)

## R-182 — قبول حي على staging
اختبار build SHA محدد بعد deploy: readiness/liveness، auth، tenant boundary، task creation/dispatch/retrieval بعد restart، persistent memory، storage، providers وtool catalog، workflows طويلة، SSE، UI/API contracts. أرفق response/status/time records بدون أسرار.
**القبول:** لا اعتماد على محاكاة في نقاط التكامل المصنفة live؛ الفشل في اعتماد أساسي يجعل الجاهزية conditional/no-go.

## R-183 — التشغيل والرجوع والتكلفة
وثائق runbook تشمل deploy/rollback، migrations forward/backward-safe، backups/restore، secret rotation، incidents، alerts/SLOs، capacity scaling، queues/DLQ، provider fallback، retention/privacy، cost per successful task. لا تنفذ موارد مدفوعة أو اختبارات خارج الميزانية.
**القبول:** تمرين rollback/recovery في staging؛ مفاتيح لا تسجل؛ خطة استعادة مجربة.

## R-184 — قرار الجاهزية
أنشئ تقريرًا موثقًا: GO / CONDITIONAL GO / NO-GO. يشمل pass/fail/skip، load level validated، عدد agents/tools, task concurrency definitions, latency/throughput, hallucination quality, storage durability, security findings, costs, unsupported configurations, open risks.
**GO** فقط إذا اجتازت البوابات الحرجة، لا يوجد critical/high risk غير معالج حسب السياسة، وهناك إثبات على بيئة محددة للهدف المطالب به. إذا تحقق مليون queued task لا تقل «مليون مهمة تُنفذ في اللحظة نفسها».
