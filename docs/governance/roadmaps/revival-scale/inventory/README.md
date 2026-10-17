# جرد الملفات المتعقبة (R-155، D-067)

> هذا الدليل **إضافة** داخل مجلد الحزمة؛ ملفات الحزمة الأصلية (المدرجة في `../MANIFEST.json`) لم تُعدَّل.

## ما هنا

| الملف | المحتوى | يُحرَّر يدوياً؟ |
|---|---|---|
| [`FILE_INVENTORY_f8b2d48.csv`](FILE_INVENTORY_f8b2d48.csv) | صف لكل ملف متعقب في اللقطة `f8b2d4828ee5aba3dc097665199f1fdf6450c2eb` — 1867 صفاً بأعمدة [`../templates/file_inventory_template.csv`](../templates/file_inventory_template.csv) الـ24 بالترتيب نفسه | لا — مولّد |
| [`INVENTORY_SUMMARY_f8b2d48.md`](INVENTORY_SUMMARY_f8b2d48.md) | الملخص: الإجمالي، المحسوم، غير المحسوم بأسمائه وأسبابه وخطواته، التوزيعات | لا — مولّد |
| [`INVENTORY_SUMMARY_f8b2d48.json`](INVENTORY_SUMMARY_f8b2d48.json) | الملخص نفسه بصيغة آلية | لا — مولّد |
| `README.md` (هذا الملف) | الشرح ومرفقات الدفعة | نعم |

اسم الملف يحمل الـSHA المختصر للقطة التي حُصرت، فلا يشير الجرد إلى نفسه ولا إلى ملفات أُضيفت بعد اللقطة.

## إعادة الإنتاج

```
git worktree add --detach /tmp/snap f8b2d4828ee5aba3dc097665199f1fdf6450c2eb   # اختياري: للتحقق المستقل
python3 scripts/build_file_inventory.py . f8b2d4828ee5aba3dc097665199f1fdf6450c2eb \
  docs/governance/roadmaps/revival-scale/inventory/FILE_INVENTORY_f8b2d48.csv \
  --coverage docs/audit/M8/logs/raw/053_r155_coverage_files.json --review-date 2026-10-17 \
  --summary docs/governance/roadmaps/revival-scale/inventory/INVENTORY_SUMMARY_f8b2d48.json \
  --summary-md docs/governance/roadmaps/revival-scale/inventory/INVENTORY_SUMMARY_f8b2d48.md
```

المولّد يقرأ من كائنات git عند الـSHA (لا من شجرة العمل)، فالنتيجة متطابقة بايتاً ببايت على أي نسخة (sha256 متطابق ×3، منها تشغيل على worktree نظيف — `docs/audit/M8/logs/raw/054`). بيانات التغطية ناتجة من pytest على worktree نظيف عند الـSHA نفسه (`053`).

## كيف تُقرأ الحالات (D-067)

- `active`: قابل للوصول من نقطة دخول نشر أو بناء مستخرجة فعلاً (`uvicorn server:app` في Dockerfile/render.yaml، بناء `web/`)، أو يستهلكه CI/Docker/Render.
- `shared`: أداة CLI أو تحقق (scripts) خارج رسم الخادم المنشور؛ الحقل `gaps` يوضح إن كانت يدوية لا يشغلها CI.
- `legacy-compatible`: shim توافق معلن (1000 ملف `specialists/<spec>/<spec>_NNNN.py` — D-049)؛ مخاطرها منخفضة لأن التنفيذ في `_templates/<spec>_base.py`.
- `generated`: سجلات وأدلة ولوكفايلات. `test-only`: الاختبارات وسكربتات التحقق التاريخية. `docs-only`: الوثائق.
- `unresolved`: لا دليل يحسم الدور — مع السبب والخطوة التالية في الصف وفي الملخص.

«حُسم دوره» يعني حُسم **بدليل آلي** (AST، git، CI، تغطية)، لا قراءة بشرية لكل سطر، ولا إثبات لصحة سلوك الملف. ذكر مسار ملف داخل اختبار يُسجَّل منفصلاً ولا يُحتسب اختباراً له.

## ما لم يُحسم (17 صفاً)

كل ملفات `src/` (واجهة الجذر TanStack Start) غير `README.md`: تُبنى وتُفحص في CI لكن Dockerfile/render.yaml يبنيان `web/` فقط، و`vite.config.ts` يستهدف nitro/cloudflare و`.lovable/` موجود — لا يمكن من المستودع إثبات نشرها خارجياً أو نفيه. السؤال مفتوح للمالك في **Q-002**؛ الخطوة: إجابة المالك ثم قرار R-158. القائمة الكاملة بالأسماء في [`INVENTORY_SUMMARY_f8b2d48.md`](INVENTORY_SUMMARY_f8b2d48.md#الملفات-غير-المحسومة-كاملة). لذلك **لا يُعلن فهم كامل للمستودع**.

## حدود صريحة

- لا حذف ولا نقل ولا إعادة تسمية ولا تنظيف للمكررات ولا إعادة هيكلة في R-155؛ التداخلات (واجهتان، `agos_kernel` بجانب `agent_civilization`، shims متطابقة) مسجلة في `known_duplicate_or_overlap` وتُعالج في R-157/R-158.
- الجرد لا يقيس أي هدف سعة (10,000 وكيل، 1,000,000 مهمة، 10,000 أداة) ولا يثبت جاهزية إنتاجية.
- الأدوار المستنتجة آلياً قد تفوتها أنماط تحميل تُبنى وقت التشغيل من مصادر خارجية؛ R-156 يتحقق تشغيلياً (RISK-032).

## مرفقات الدفعة (ليست ضمن لقطة الأساس)

أُضيفت في دفعة `m8/r155-file-inventory` **بعد** `f8b2d48`، ولذلك ليست صفوفاً في الجرد (يتحقق `054` أن عددها داخل الجرد = 0):

- `scripts/build_file_inventory.py` — المولّد.
- `tests/test_r155_file_inventory.py` — اختباراته (17).
- `docs/governance/roadmaps/revival-scale/inventory/` — `FILE_INVENTORY_f8b2d48.csv`، `INVENTORY_SUMMARY_f8b2d48.md`، `INVENTORY_SUMMARY_f8b2d48.json`، `README.md`.
- `docs/audit/M8/logs/raw/050`..`062` — قبول R-154 (`050`، `051`) وأدلة R-155 (`052`..`062`، ومنها `053_r155_coverage_files.json`).
- ملفات معدلة (موجودة في اللقطة، وصفوفها تصف نسخة اللقطة لا النسخة المعدلة): `docs/governance/ROADMAP.md`، `PROJECT_STATE.md`، `CHANGELOG.md`، `DECISIONS.md`، `OPEN_QUESTIONS.md`، `RISKS.md`، `QUALITY.md`، `README.md`، `docs/audit/M8/EVIDENCE.md`.
