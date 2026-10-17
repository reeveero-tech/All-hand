# P1 — إحياء وجرد كل ملف ومكون (R-155 إلى R-158)

## R-155 — جرد شامل
املأ `templates/file_inventory_template.csv` أو ولّد نسخة داخل المستودع تشمل كل `git ls-files`. احسب العدد ومجموع `unresolved`; لا تعلن 100% إذا بقيت صفوف بلا قرار. لا يُعد تاريخ الملف حكمًا.
**القبول:** صف لكل ملف tracked؛ paths دقيقة؛ لا حذف؛ أسماء الـunresolved وإجراءاتها منشورة.

## R-156 — خريطة المسارات
حلل entry points/import graph/dynamic registration/routes/workflows/build/runtime flags/storage/LLM/tool adapters/memory. انشئ diagram وتقرير مسارات تشغيل `real` و`specialists` والواجهة والطابور.
**القبول:** كل entrypoint له smoke/integration test؛ ملف غير قابل للتحليل يسجل يدويًا بدليل؛ لا assume orphan=dead.

## R-157 — التكرار والتوافق
راجع الوحدات المتوازية والقديمة. حدّد canonical interface لكل capability وأضف adapters توافق مع اختبارات contract/parity. أبقِ ملفات implementations القديمة.
**القبول:** اختبار المسار القديم والجديد لنفس الحالات المرجعية، وتوثيق الاختلافات المقصودة؛ لا rename/delete.

## R-158 — خط أساس اختبارات الملفات
صمم أوامر ثابتة لكل مجموعة: import/compile/lint/type/schema/unit/contract/integration بحسب الملاءمة. افحص migrations وDocker/Render/CI والواجهة والسكريبتات.
**القبول:** baseline محفوظ بالـSHA والإصدارات والرموز؛ counts مفصلة pass/fail/skip/xfail/deselected؛ لا إخفاء فشل عبر skip.
