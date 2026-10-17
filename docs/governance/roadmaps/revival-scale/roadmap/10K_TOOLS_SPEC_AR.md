# مواصفات هدف 10,000 أداة فعلية

## تعريف «أداة»

الأداة هي قدرة قابلة للاستدعاء بعقد واضح وتنفيذ محدد واختبارات وصلاحيات. لا تعدّ الأسماء البديلة، أو نفس الدالة بإصدارات شكلية، أو سجلات discovery غير القابلة للتنفيذ أدوات certified.

حالات العدد تفصل: `discovered`, `registered`, `validated`, `certified`, `disabled`, `deprecated-compatible`, `failed`. يعلن النظام كل رقم منفصلًا. الرقم المستهدف 10,000 في سجل الأداة لا يُعلن منجزًا حتى تثبت uniqueness والجودة وقابلية التنفيذ.

## مصادر الأدوات

- أدوات داخلية AGOS: العمليات الحسابية، تحليل البيانات، الملفات في sandbox، التحقق، البناء، الاختبار، المراقبة، والجدولة.
- MCP servers: اكتشاف واعٍ بإصدار البروتوكول، وallowlist للمصادر، مصادقة وتفويض، validation للمدخلات/المخرجات.
- OpenAPI connectors: توليد adapter من schema ثم مراجعة كل endpoint وmethod والصلاحيات؛ لا تحول جميع endpoints إلى صلاحيات غير مقيدة.
- مزودون خارجيون/SDK/CLI: adapters ذات نسخ pinned ومفاتيح قصيرة الأجل وlimits.
- مجالات متخصصة تُضاف تدريجيًا حسب الاستخدام الفعلي وتقييم الجودة/المخاطر.

## سياسة الإصدارات والتحديثات

- افحص أحدث إصدار stable موثوق وقت العمل، changelog، advisories، التوافق، الترخيص، وsupport status.
- لا تثبت التبعيات من `latest` داخل الإنتاج، ولا تحديث auto-merge للتبعيات دون اختبارات؛ قفل الإصدارات والتجزئات حيث يدعم النظام ذلك.
- اختبر compatibility على runtime المعلن في المشروع؛ لا توسع matrix غير المختبرة.
- سجّل source URL وversion وhash/provenance، وتاريخ آخر فحص أمان، ومالك التحديث.
- MCP/metadata/annotations القادمة من server خارجي لا تُعامل كتعليمات موثوقة؛ حقق input/output schemas وافرض سياسة محلية.

## حقل سجل الأداة

مطلوب: `tool_id`, `name`, `version`, `provider/source`, `description`, `capabilities`, `input_schema`, `output_schema`, `auth_scope`, `tenant_scope`, `risk_class`, `side_effects`, `read_only`, `idempotent`, `requires_approval`, `timeout_ms`, `rate_limit`, `resource_budget`, `network_allowlist`, `data_classification`, `health_status`, `test_status`, `last_verified_at`, `license`, `docs_uri`, `implementation_ref`, `fallback_policy`, `telemetry_name`.

## أمن التنفيذ

- منع الوصول الافتراضي إلى الأدوات الخطرة؛ least privilege.
- الأدوات ذات الكتابة/المال/النشر/الحذف/الإرسال الخارجي تتطلب سياسة صريحة وموافقة بشرية عند الحاجة.
- تنفيذ subprocess في sandbox مع CPU/RAM/time/output caps؛ لا تعطي أداة test runner أو shell أسرار العملية أو صلاحية host كاملة.
- عزل credentials حسب tenant/tool scope، مع redaction في logs والتتبع.
- منع SSRF/DNS rebinding/proxy bypass؛ DNS/redirect validation عند اتصال الأدوات، ومحددات وجهة الشبكة.
- validate schemas قبل وبعد التنفيذ، وحد حجم المخرجات، ومعاملة النص الخارجي كمحتوى غير موثوق لا كتعليمات.
- retries فقط للأفعال idempotent أو محمية بمفتاح idempotency؛ لا تعيد فعلًا ماليًا/خارجيًا بشكل أعمى.

## التوجيه حسب المهمة

1. استخرج capability requirements من المهمة ومراحلها.
2. صفِّ الأدوات غير المتاحة، غير المعتمدة، خارج tenant scope، أو ذات الصلاحية الزائدة.
3. رتّب الباقي بجودة مثبتة، التوفر، الزمن، التكلفة، التوافق، الخصوصية ومعدل الخطأ.
4. ابدأ بأدوات منخفضة المخاطر؛ اطلب موافقة عند الفعل الحساس.
5. تحقق من النتيجة بشكل مستقل (schema، validator، test، أو source citation).
6. سجل tool chosen/reason، بدائل مرفوضة بسبب الصلاحية/التوفر، زمن ومخرجات منقحة، ونجاح/فشل.

## مراحل بلوغ 10k

- **100 أدوات certified:** أدوات النواة عالية الاستخدام؛ كل واحدة بعقد واختبارات أمن وتكامل.
- **1,000:** التوسع في connector packs؛ benchmark البحث واختيار الأداة ودقة المطابقة.
- **10,000:** اختبار فهرسة/بحث وتوجيه وتحديثات ودوران مفاتيح وتوقف موفر واحد؛ لا تسمح كل الأدوات لكل المستأجرين تلقائيًا.

يجب أن تكون هناك أدوات كثيرة مسجلة لا تجعل prompt النموذج يضم تعريفات 10,000 أداة كل مرة. استخدم retrieval/candidate narrowing ومراحل اختيار مع ميزانية tokens، ثم validation قبل التنفيذ. اختبر حالات tool poisoning، descriptions مضللة، schema drift، tool name collisions، malformed output، slow/hung tool، rate limiting، وprovider compromise.
