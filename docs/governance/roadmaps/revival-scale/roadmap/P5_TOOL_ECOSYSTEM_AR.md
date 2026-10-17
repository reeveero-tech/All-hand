# P5 — سجل 10,000 أداة وتوجيهها (R-171 إلى R-175)

## R-171 — عقد registry
اعتمد سجل أداة canonical مع JSON Schemas وmetadata وversion/owner/source/license/permissions/risk/idempotency/timeout/budget/tests/provenance. راجع `specs/tool_registry_contract.schema.json` قبل إدماجه. لا تعتبر schema وحدها دليل تنفيذ.
**القبول:** CRUD/lookup/search contract، uniqueness، schema validation، versioning، نتائج قابلة للتدقيق.

## R-172 — طبقة أمن الأدوات
عزل تنفيذ الأدوات، ضوابط الشبكة، SSRF/DNS-rebinding defense، حدود الموارد، credentials scope، approval gates، output caps، sanitized logging. metadata الخارجية غير موثوقة.
**القبول:** اختبارات adversarial لأداة ضارة/مزيفة وبروتوكول، صلاحيات أقل امتياز، لا تسرب secrets/tenant data.

## R-173 — MCP/OpenAPI/SDK connectors
أضف adapters منفصلة مع version pinning والاختبارات. استورد capabilities بطريقة allowlisted؛ لا تحول endpoints الحساسة إلى وظائف متاحة تلقائيًا. تحقق من أحدث stable protocol/spec في وقت التنفيذ؛ سجل قرار الإصدار والتوافق.
**القبول:** contract لكل adapter، auth tests، schema drift، timeouts، no provider credential leakage.

## R-174 — اختيار أداة ذات جودة
عند كل task، حدد capability، الصلاحية المطلوبة، حدود البيانات، cost/latency، tool health، جودة نتائج سابقة. استخدم candidate retrieval ثم ranking ثم validate output ثم fallback آمن. لا يقرر النموذج صلاحياته بنفسه.
**القبول:** gold set لاختيار الأدوات، measured top-k/tool choice accuracy، tool failure fallback، no prohibited tool call.

## R-175 — الوصول إلى 10,000 certified tools
اعتماد بالأعداد 100، ثم 1,000، ثم 10,000. لكل أداة مصدر/version وimplementation ref وtest proof. التوقف الآمن عند tool catalog poisoning/schema errors/connector unavailable.
**القبول:** 10k unique executable entries، وعدادات منفصلة للحالات؛ كل certified لديها smoke/contract/security test؛ throughput للبحث وتحديث الكتالوج مقاس؛ لا احتساب alias كأداة جديدة.
