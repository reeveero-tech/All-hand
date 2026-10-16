# نموذج البيانات (DATA_MODEL)

> **آخر تحديث**: 2026-10-16 — R-143 (BUG-04): قراءة جديدة `get_task(task_id, scope=)` — صف واحد بالمفتاح الأساسي ضمن نطاق المستأجر (`task_id=eq.<id>&tenant_id=eq.<t>&limit=1`)؛ عدّاد `get_task` في `_STATS`. قبلها R-141 (BUG-02): صفوف `tasks` الناتجة عن `/api/chat` تحمل الحالة المشتقة من النتيجة (`completed`/`failed`) في الإدراج والتحديث معاً — كانت `completed` دائماً؛ و`event_data` لحدث `chat_completed` يضيف `status` بجانب `ok`. قبلها 2026-10-15 — R-119 (مصالحة ادعاءات قديمة: `agents.yaml` حُذف في R-031b/D-025؛ فجوات M0 حُلت أو وُضحت في R-022/R-042/R-042b؛ قبلها تحديث 2026-10-12 — M0)
> **ملاحظة منهجية**: ما يلي مستخرج من قراءة الكود (الحمولات الفعلية والـ dataclasses)، وهو "مرصود من الاستخدام" لا مخطط معلن؛ لكن الأنواع والقيود **مُتحققة حياً** في R-042/R-042b — راجع قسم "التحقق الحي" أدناه.

## الكيانات المرصودة من الاستخدام

### Task (مهمة)
- **in-memory** (`server.py`): `TASKS: Dict[task_id, dict]` بحد أقصى 500 مهمة (`MAX_TASKS_KEPT`)، الحقول: `id`, `specialization`, `prompt[:500]`, `status`, `created_at`, `result`.
- **Supabase** (`storage/supabase_store.py`): جدول `tasks` — إدراج POST بـ `task_id, specialization, prompt[:500], status`؛ وتحديث PATCH بحقول اختيارية: `status, agent_name, result (كائن JSON — أُصلح في R-042؛ كان نصاً مرمزاً مرتين), elapsed_s, finished_at="now()"`.
- **سلوك عند غياب الإعداد**: `insert_task` تعيد `False` بصمت؛ `get_tasks` تعيد `[]`.

### Event (حدث)
- **in-memory**: `TASK_EVENTS: asyncio.Queue`.
- **Supabase**: جدول `events`؛ الحقول المُرسلة: `event_type`, `event_data` (كائن JSON منذ R-042 — كان نصاً عبر `json.dumps` فيُخزَّن نصاً داخل `jsonb`).
- **حالة مهام `/api/chat` (R-141، BUG-02)**: حالة واحدة `chat_status = "completed" if outcome["ok"] else "failed"` تُكتب في `TASKS` وفي `insert_task` و`update_task` (قبلها: `completed` ثابتة في التخزين حتى عند الفشل). `event_data` لـ`chat_completed`: `{task_id, agent, specialization, ok, status}` — النوع `chat_completed` باقٍ (انتهاء المعالجة)، و`status` يصف نجاح التنفيذ. الصفوف المخزنة قبل R-141 قد تحمل `completed` لتنفيذ فاشل (لا ترحيل؛ `result.ok` فيها هو المرجع).

### AgentStat (إحصاءات وكيل)
- **Supabase**: جدول `agent_stats` — الحمولة المرسلة فعلياً (`upsert_agent_stats`، on_conflict=`agent_name`): `agent_name, specialization, tasks_completed, tasks_failed, total_time_s, last_active_at, updated_at`. تُستدعى من `_run_task` و`/api/chat` في `server.py`. (الأنواع في `docs/sql/001_init.sql` — مُتحققة حياً في R-042.)

### Civilization (حضارة الوكلاء)
- **in-memory فقط**: `CIVILIZATION: Dict[specialization, List[RealSpecialistAgent]]` — 20 تخصصاً × 50 = 1,000 وكيل. لا تخزين دائم.

### OSSAgent (سجل OSS)
- **dataclass ثابت** (`registry/oss_agents.py`): `id, name, source_url, category, capabilities: List[str], license, adapter, description` — ~320 مشروعاً.

### ExternalAgentAdapter
- **in-memory**: `EXTERNAL_AGENTS: List`؛ الطلب: `name, url, model, api_key`.

### agents.yaml (السجل الثابت للسكربتات) — **حُذف**
- ~~يُقرأ فقط من `agent_manager.py` و`auto_fetch_agents.py`~~ — **حُذف الملف ومستهلكاه معاً في R-031b (D-025)**؛ الحقول التاريخية: `name, source, branch, specialization`. هذا سجل تاريخي فقط.

## سلوك التخزين (مُتحقق من الكود)

- `get_storage()` تُعيد `SupabaseStorage` دائماً — لا توجد طبقة تخزين بديلة داخل الوحدة.
- كل عملية تخزين تعيد `False`/`[]` عند غياب الإعداد أو فشل الطلب؛ منذ R-042 يُسجَّل رفض HTTP (الحالة + أول 300 حرف من الجسم) كتحذير في سجل `storage`.
- "الرجوع للذاكرة" منطق صريح في `server.py` لكل مسار على حدة (غير متجانس).

## الفجوات

> مُصالحة R-119: البنود الأربعة أدناه كانت من M0 (2026-10-12). الحالة الحالية بعد القراءة الفعلية لـ`docs/sql/001_init.sql`: البنود 1 و2 و3 و4 حُلت كلها في R-022/R-042/R-042b (الفهارس محددة في المخطط — راجع البند 2 أدناه).

1. ~~لا migrations ولا SQL~~ — حُل في R-022 (`docs/sql/001_init.sql` موجود ومتحقق حياً).
2. ~~الفهارس غير محددة~~ — حُل في R-022: 3 فهارس محددة في `001_init.sql` (idx_tasks_created_at، idx_agent_stats_total_time، idx_events_created_at) + مفاتيح أساسية على كل جدول (task_id/agent_name/id). لا توجد قيود `CHECK` في المخطط.
3. ~~أنواع أعمدة `agent_stats` غير معلومة~~ — حُل في R-042 (text/integer/double precision/timestamptz — راجع قسم "التحقق الحي" أدناه).
4. ~~مخطط Supabase يُدار خارج المستودع~~ — حُل في R-022 (مخطط مرجعي في `docs/sql/001_init.sql`؛ التطبيق على مشروع Supabase الحي مسؤولية المالك — R-042b).

## التحقق الحي (R-042، M7/6 — D-038)

- `docs/sql/001_init.sql` طُبق كما هو على PostgreSQL 18 (محلياً) و17 (CI)، وخُدم عبر PostgREST 12.2.3 بنموذج أدوار Supabase.
- الأنواع المُتحقق منها: `tasks` (text/jsonb/double precision/timestamptz)، `agent_stats` (text/integer/double precision/timestamptz)، `events` (bigint identity/text/jsonb/timestamptz) — مطابقة لما يرسله الكود ويقرؤه؛ **لا تعديل لازم** على المخطط (D-019).
- upsert على `agent_stats` (`on_conflict=agent_name` + `resolution=merge-duplicates`) يدمج في صف واحد (فحص فعلي).
- `"now()"` كنص في `finished_at`/`last_active_at`/`updated_at`: تقبله PostgreSQL ويُقيَّم وقت التنفيذ (فحص فعلي).
- الأدلة: `docs/audit/M7/logs/13` (قبل: 18/22) و`15` (بعد: 22/22 + 3/3).

### Supabase المستضافة (R-042b، M7/7 — D-039)
- طُبق المخطط على مشروع تجريبي (PostgreSQL 17.11): الأنواع مطابقة، 22/22 + 3/3 (logs/22).
- **RLS**: مفعّل صراحة في `001_init.sql` على الجداول الثلاثة، **بلا سياسات**. الخادم يستخدم `SUPABASE_SERVICE_KEY` (يتجاوز RLS)؛ دور `anon` مرفوض كتابةً وقراءة (logs/20، وضابط سلبي في كل تشغيل CI).

## R-078 — تصنيف المفتاح في طبقة التخزين
- `supabase_store.py`: `SUPABASE_KEY_SOURCE` (أول متغير غير فارغ بالترتيب `SUPABASE_SERVICE_KEY` ← `SUPABASE_ANON_KEY` ← `VITE_SUPABASE_ANON_KEY` — نفس أولوية القراءة السابقة)، و`classify_key()`/`key_status()`/`log_key_status()`.
- التصنيف: `sb_secret_*` أو JWT `role=service_role` → `service`؛ `sb_publishable_*` أو JWT `role=anon` → `public`؛ صيغة غير معروفة من متغير `*_ANON_KEY` → `public`، ومن `SUPABASE_SERVICE_KEY` → `unknown`. الصيغة تتقدم على اسم المتغير. ادعاء JWT يُقرأ دون تحقق من التوقيع (تصنيف فقط).
- **مسار الكتابة بلا تغيير**: نفس المفتاح ونفس الطلبات؛ R-042 محلياً 22/22 + 3/3 بعد التغيير (logs/51).

## R-082 — تسجيل نتائج العمليات في طبقة التخزين
- `supabase_store.py`: `_STATS` على مستوى العملية — لكل عملية (`insert_task`، `update_task`، `get_tasks`، `get_task` (R-143)، `upsert_agent_stats`، `get_agent_stats`، `insert_event`، `get_events`) عدّاد ok/failed، و`last_ok`/`last_error`/`last_success_at`. يُسجَّل في `_accepted` (رمز HTTP) وفي معالجات الاستثناءات (نوع الاستثناء فقط).
- **لا يُحفظ**: أجسام الردود، العناوين، المفاتيح، الحمولات (اختبار صريح).
- عقد الطبقة بلا تغيير: نفس القيم المعادة (`True`/`False`/`[]`)، نفس الطلبات؛ R-042 محلياً 22/22 + 3/3 بعد التغيير.
- حد: `get_tasks`/`get_agent_stats`/`get_events` قد تُسجَّل نجاحاً ثم فشلاً إن نجح HTTP وفشل تحليل JSON — العدادان صحيحان كحدثين، و`last_ok` يعكس الأخير.

## R-149 — سجل الرفض التشخيصي لا يحتفظ بجسم الخلفية (Security QA، 2026-10-16)
- `_accepted` عند رفض HTTP تسجّل في السجل العملية والرمز فقط (`"<op> rejected: HTTP <status>"`) — لم يعد يُقرأ جسم الرد الخلفي ولا يُسجّل نصه. السبب: جسم الرفض نص غير موثوق من الخلفية (قد يردد مفتاح الخدمة)؛ التشخيص يبقى كافياً بالعملية والرمز، والإحصاءات (`storage_stats`) بلا تغيير (الرمز فقط). لا تغيير في الكيانات أو الحمولات أو عقود القيم المعادة.
- محوّل الوكيل الخارجي (`external_adapter.py`): خطأ الحالة غير 200 يرفع `RuntimeError(f"HTTP {status}")` — جسم رد الخلفية لم يعد يدخل نص الخطأ (كان يدخل أول 300 حرف)، لأن نص الخطأ يصبح نتيجة المهمة المرئية للمستأجر وسطر السجل؛ خلفية معادية كانت تستطيع رد المفتاح الإداري للمستأجر. أسباب رفض SEC-01/SEC-05 الثابتة بلا تغيير.

## R-138 — ملكية المستأجر للمهام والأحداث (SEC-02، D-056، 2026-10-16)
- **الترحيل**: `docs/sql/002_tenant_isolation.sql` (متكرر الأمان) — عمود `tenant_id text` (يقبل NULL) في `tasks` و`events`، وفهرسان `(tenant_id, created_at desc)`. يُطبَّق بعد `001_init.sql` (`scripts/r042/run_local_stack.sh` يطبقه في وظيفة CI `storage`). **يجب تطبيقه على قاعدة الإنتاج قبل نشر R-138**: بدونه يرفض PostgREST الإدراج (عمود مجهول) — تُسجَّل الإخفاقات في `storage_stats` ويستمر الخادم بالذاكرة.
- **الكتابة**: `insert_task(..., tenant_id=)`، `insert_event(..., tenant_id=)` — المالك من الهوية الموثقة عند الإدراج؛ `update_task` لا يلمس `tenant_id` (لا نقل ملكية).
- **القراءة بالمعرّف (R-143، BUG-04)**: `get_task(task_id, scope=)` ← صف `tasks` واحد أو `None`. الاستعلام `GET /rest/v1/tasks?task_id=eq.<id>&limit=1` ويضاف `tenant_id=eq.<t>` لنطاق مستأجر (لا يضاف لـ`ALL_TENANTS` — admin مع `all_tenants=true` فقط)؛ لا `order` ولا اعتماد على حد القائمة. بلا `scope` ← `None` دون نداء شبكي؛ نطاق غير صالح ← `ValueError` (`check_scope`)؛ معرّف خارج `[A-Za-z0-9_-]{1,64}` ← `None` دون إرساله مرشحاً. صفوف `tenant_id` NULL لا تطابق مرشح مستأجر. يستخدمها `GET /api/tasks/{task_id}` حين تغيب المهمة عن الذاكرة. الفهرس: المفتاح الأساسي `task_id`.
- **القراءة**: `get_tasks(limit, scope=)`، `get_events(limit, scope=)` — بلا `scope` تعيدان `[]` دون نداء شبكي (رفض افتراضي)؛ `scope` نص مستأجر يُتحقق منه (`[A-Za-z0-9_-]{1,64}`) فيصير `tenant_id=eq.<t>`؛ `ALL_TENANTS` (كائن حارس) بلا مرشح للمدير والعداد التشغيلي في `/api/system/status`. `eq.` لا يطابق NULL في PostgREST، فالصفوف القديمة لا تظهر لمستأجر.
- **الصفوف القديمة**: `tenant_id = NULL` = معزولة (لا مالك مثبت) — لا تُنسب تلقائياً؛ يراها المدير عبر `all_tenants=true`؛ النسبة اليدوية عند إثبات الملكية مثال في رأس ملف الترحيل.
- **الذاكرة** (`AGOS_MEMORY_FILE`): كل مدخل `metadata.tenant_id` + وسم `tenant:<t>`؛ المدخلات القديمة بلا `tenant_id` تبقى في الملف (لا حذف) ولا تُسترجع لأحد ولا تدخل التعلّم.
