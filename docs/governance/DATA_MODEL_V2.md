# نموذج البيانات v2 (R-202، D-151)

> **المصدر**: [`docs/sql/v2/001_core.sql`](../sql/v2/001_core.sql) (المخطط) و`agos_v2/contracts.py` (العقود). هذا الملف يشرحهما ولا يضيف إليهما. القرار المعماري في [`ARCHITECTURE_V2.md`](ARCHITECTURE_V2.md).
> **الحالة**: مختبر على قاعدة اختبار فقط (PostgreSQL 18 محليًا، 17 + pgvector في CI). **لم يُطبق على Supabase الحي**؛ يُطبق في R-204.
> نموذج v1 (الجداول `public.tasks` و`events` و`agent_stats`) موصوف في [`DATA_MODEL.md`](DATA_MODEL.md) ويبقى يعمل حتى R-224.

## المبادئ

1. **مخطط منفصل `agos`** بجانب v1 في `public`.
2. **كل صف يتبع مساحة عمل واحدة** (`workspace_id`). الصفوف الفرعية مربوطة بأبيها وبنفس المساحة عبر مفاتيح أجنبية مركبة `(parent_id, workspace_id)`، فلا يمكن لخطوة أو ناتج أو رسالة أن تشير إلى مساحة أخرى.
3. **RLS على كل الجداول العشرة**: المستخدم (`authenticated`) يرى ويكتب صفوف مساحاته فقط (`agos.is_member`). `anon` لا يملك أي صلاحية. `service_role` (الخادم والعامل) يتجاوز RLS.
4. **قواعد الصدق في القاعدة نفسها** (وفي العقود بالقواعد نفسها):
   - مهمة `completed` تحتاج `verified_at`؛ خطوة `succeeded` تحتاج `verification.passed = true`.
   - `failed` يحتاج `error` فيه `code` و`message` غير فارغة.
   - الحالات النهائية لها `finished_at`، وغير النهائية بلا `finished_at`.
   - `attempts <= max_attempts` (تصحيح ذاتي محدود).
   - `task_events` إضافة فقط (التعديل مرفوض بمشغّل).
5. **درس مسجل**: في PostgreSQL قيد CHECK نتيجته NULL يُعتبر ناجحًا. قيد «failed يحتاج خطأ» الأول قبل صفًا بلا خطأ لأن `error ? 'code'` على NULL = NULL. الاختبار الحي اكتشفه (أحمر 2)، والإصلاح `error is not null and ...` صريحًا.

## الجداول

| الجدول | ما هو | أهم الأعمدة | العقد |
|---|---|---|---|
| `workspaces` | مساحة عمل (مستأجر) | `id`، `name` | — |
| `workspace_members` | عضوية مستخدم ودوره | `(workspace_id, user_id)`، `role` ∈ owner/admin/member | — |
| `agent_specs` | مواصفة وكيل بإصدار | `slug`، `version`، `status` ∈ draft/active/retired، `spec` (jsonb = AgentSpec)؛ `workspace_id` NULL = مواصفة منصة يراها الجميع | `agent_spec` (الجسم) |
| `conversations` | محادثة | `title`، `created_by` | — |
| `messages` | رسالة في محادثة | `seq` فريد في المحادثة، `role` ∈ user/assistant/system/tool، `task_id` اختياري | `message` |
| `tasks` | هدف المستخدم بمعايير قبول | `goal`، `acceptance_criteria`، `status` (9 حالات)، `budget`، أعمدة الطابور (`claim_token`، `lease_expires_at`، `attempts`)، `verified_at`، `error`، التكلفة والرموز | `task` |
| `steps` | عقدة في خطة المهمة | `kind` ∈ plan/act/verify/fix/review/approval/final، `depends_on`، `agent_spec_id`، `verification`، `attempt`/`max_attempts`، النموذج والتكلفة | `step` |
| `artifacts` | ناتج مهمة | `kind`، `mime_type`، إما `inline_content` أو `storage_path` (واحد فقط)، `size_bytes`، `sha256` | `artifact` |
| `task_events` | سجل دائم لكل ما يحدث للمهمة (إضافة فقط) | `type`، `data` | — |
| `memory_items` | ذاكرة مهمة/مستخدم/مساحة | `scope`، `content`، `embedding` (vector بلا بعد ثابت)، `embedding_model` | — (R-215) |

## حالات المهمة والخطوة

- **المهمة**: `queued` → `claimed` → `planning` → `running` ⇄ `waiting_approval` → `verifying` → `completed` | `failed` | `cancelled`.
- **الخطوة**: `pending` → `ready` → `running` ⇄ `waiting_approval` → `succeeded` | `failed` | `skipped` | `cancelled`.
- انتقالات الحالة (من أين إلى أين) يفرضها المنسق في R-210 ويختبرها؛ القاعدة تفرض شروط الحالات النهائية فقط.

## ما ليس في هذا الترحيل (يأتي في بنده)

| الموضوع | البند |
|---|---|
| مفاتيح المزودين للمستخدم (BYOK) | R-205 |
| مفاتيح المطورين والحصص والاستخدام | R-207 |
| فهارس البحث الدلالي لكل نموذج تضمين، وقاعدة المعرفة والمستندات | R-216، R-217 |
| الموصلات وحساباتها | R-228 |
| صلاحيات الأدوار التفصيلية والدعوات | R-204 |

## كيف يُختبر

`tests/test_r202_v2_schema.py`:
- **دائمًا**: الأمثلة في `contracts/v2/examples/` تمر على العقود؛ ملفات JSON Schema مطابقة للكود؛ 16 حالة خاطئة يرفضها العقد؛ أعمدة SQL = حقول العقود؛ RLS وسياسة على كل جدول.
- **حيًا** (`V2_SCHEMA_ADMIN_URI`، في CI job ‏`storage` على `pgvector/pgvector:pg17`): قاعدة جديدة، تطبيق المخطط، إدراج السيناريو من JSON العقود (`jsonb_populate_record`) وقراءته عائدًا إلى العقود، رفض القاعدة للحالات الـ16 نفسها، عزل المساحات بـRLS، منع `anon`، سجل الأحداث إضافة فقط، ورفض إكمال مهمة بلا تحقق عند التحديث.
