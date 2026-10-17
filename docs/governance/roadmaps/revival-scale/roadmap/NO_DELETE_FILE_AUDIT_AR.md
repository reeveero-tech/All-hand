# إحياء المستودع وجرد كل ملف — دون حذف

## المبدأ

لا يوجد حد زمني يقول إن ملفًا لم يتغير ثلاثة أشهر فهو سليم أو تالف. يعتمد الحكم على الدليل: كيف يستورد ويُستدعى ويُبنى ويُختبر، وما العقد الذي يحافظ عليه.

## نطاق الجرد

يجب تغطية كل ملف Git-tracked، بما فيها: Python/JS/TS، HTML/CSS، schemas، workflows، Docker/Render، lockfiles، migrations/SQL، tests، scripts، docs، configs، وأي ملفات مولدة أو بيانات/عينات. لا تكتفِ بتعداد ملفات المصدر.

## حقول إلزامية لكل ملف

استخدم `templates/file_inventory_template.csv` وسجل:

- المسار، النوع، آخر تعديل، الحجم وSHA.
- نقاط الدخول أو الاستيرادات/الاستدعاءات (مع الإشارة إلى الاستيرادات الديناميكية والـentry points).
- الخدمة/المسار الذي يملك الملف، الوظيفة والعقد العام.
- الاختبارات الحالية والأدلة الأخيرة ونسخة Python/Node والأدوات ذات الصلة.
- حالة الجرد: `active`, `shared`, `legacy-compatible`, `generated`, `test-only`, `docs-only`, `unresolved`.
- القرار المسموح: `KEEP`, `UPGRADE`, `INTEGRATE`, `ADAPTER`, `DOCUMENT`; `DELETE` ليس خيارًا متاحًا في هذه الخطة.
- مستوى الثقة والمخاطر والاختبار الناقص وخطة التحقق.

## طريقة المراجعة

1. خذ baseline على SHA واحد واحفظ `git ls-files` و`git diff --name-status` وملفات التبعيات.
2. اجمع AST/import graph، dynamic imports، routes/entrypoints، workflow references، build/deploy references، tests discovery، والـruntime feature flags.
3. قارِن الملفات التي لا يظهر لها استدعاء؛ هذا **لا يثبت** أنها ميتة لأن التسجيل أو الاستيراد قد يكون ديناميكيًا.
4. اختبر كل module: import/compile/type/schema/unit/contract/integration بحسب وظيفته. لا تفرض نوع اختبار غير مناسب؛ وثّق المبرر إذا لم ينطبق.
5. ابحث عن implementations متداخلة أو مكررة. وحّد خلف façade/adapter مع اختبارات parity بين الواجهات القديمة والجديدة، ثم أبقِ القديم.
6. لكل خطأ، أضف اختبارًا يثبت العطل قبل الإصلاح أو fixture تمييزية، ثم أصلحه في الدفعة نفسها.
7. ابحث عن أمن ملفات الإعداد: secrets، insecure defaults، unpinned packages، CORS، logging، migrations، auth/tenancy، tool permissions.
8. أكمل كل صف أو اشرح لماذا لا يمكن التحقق منه. لا تضع جردًا 100% إذا بقي أي ملف `unresolved`؛ اذكر النسبة والأسماء وعدد unresolved صراحةً.

## قواعد الترقية مع التوافق

- يُسمح بترقية داخلية وإضافة wrapper/adapter من دون حذف واجهة قديمة.
- للواجهة القديمة، أضف اختبار توافق/contract يثبت أنها ما زالت تتصرف كما هو متوقع.
- إذا تغيرت صيغة البيانات، قدّم migration/additive path واختبار upgrade/rollback؛ لا تعدل ملفات بيانات المستخدم بصمت.
- لأي وظيفة مكررة، عيّن canonical implementation واحدة، لكن أبق legacy façade واختبارات equivalence حتى يصدر المالك إذنًا منفصلًا لإزالة أي شيء.
- حافظ على imports العامة والـAPI contracts إلا إذا أضيف إصدار متوافق واضح. لا تغيّر المسارات فقط لترتيب المجلدات.

## بوابة القبول

- كل ملف tracked له صف جرد.
- صفر ملف حذف/rename/move غير مصرح به.
- الملفات عالية الخطورة موثقة بالاختبارات والأدلة.
- كل ملف unresolved لديه إجراء تالٍ محدد، مالك ومسألة مانعة واضحة.
- `git diff --check`, lint، tests، build، security scans، governance نجحت.
- تقرير يذكر coverage الخاص بالجرد وليس فقط test coverage.
