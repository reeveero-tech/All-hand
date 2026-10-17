# P4 — الذاكرة والهلوسة والجودة (R-167 إلى R-170)

## R-167 — واجهة ذاكرة واحدة
حدد memory interface موحدة للكتابة/البحث/الترتيب/الحذف/التصدير/الاستعادة، مع adapters للأنواع الحالية. قرر متى يستخدم process memory أو persistent DB أو vector index بناء على متطلبات/benchmarks. لا تفقد وظائف الواجهات القديمة.
**القبول:** contract tests لكل adapter، parity للحالات الأساسية، توثيق خيارات persistence.

## R-168 — durability وعزل الذاكرة
نفذ اختبارات إعادة تشغيل process حقيقية، corrupt/partial file, concurrent read/write, prune/index rebuild, tenant isolation, retention/forget, migration, storage unavailable. اختبر 10/1k/100k+ entries تدريجيًا بحسب backend.
**القبول:** لا تسرب بين المستأجرين؛ قراءة بعد restart موثقة؛ سلوك fail-open/fail-closed مضبوط؛ latency/memory reported.

## R-169 — benchmark الهلوسة
جهز versioned dataset عربي/إنجليزي مع golden answers/sources، abstention items، prompts عدائية، tool poisoning، outdated/conflicting sources، وcode tasks. ضع holdout set، deterministic checks، ومراجعة بشرية عينة.
**القبول:** تقارير metrics قبل/بعد مرتبطة بإصدارات model/prompts/tools/dataset؛ no test set leakage.

## R-170 — جودة المخرجات
أضف validators قابلة لإعادة الاستخدام: JSON/schema, code compile/tests, citations, factual evidence checks, stage completeness, tool result validation. النموذج لا يقيّم نفسه وحده. لا توجد `success=true` دون تحقق المطلوب.
**القبول:** حالات إيجابية وسلبية؛ false-success measurement؛ امتناع مناسب عندما لا تتوفر معلومات؛ عدم تراجع benchmark دون موافقة/تفسير.
