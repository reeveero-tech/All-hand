# نموذج البيانات (DATA_MODEL)

> **آخر تحديث**: 2026-10-12 — M0
> **ملاحظة منهجية**: لا توجد migrations أو ملفات SQL في المستودع. ما يلي مستخرج من قراءة الكود (الحمولات الفعلية والـ dataclasses)، وهو "مرصود من الاستخدام" لا مخطط معلن. الأعمدة الدقيقة والقيود والفهارس **غير معلومة**.

## الكيانات المرصودة من الاستخدام

### Task (مهمة)
- **in-memory** (`server.py`): `TASKS: Dict[task_id, dict]` بحد أقصى 500 مهمة (`MAX_TASKS_KEPT`)، الحقول: `id`, `specialization`, `prompt[:500]`, `status`, `created_at`, `result`.
- **Supabase** (`storage/supabase_store.py`): جدول `tasks` — إدراج POST بـ `task_id, specialization, prompt[:500], status`؛ وتحديث PATCH بحقول اختيارية: `status, agent_name, result (كائن JSON — أُصلح في R-042؛ كان نصاً مرمزاً مرتين), elapsed_s, finished_at="now()"`.
- **سلوك عند غياب الإعداد**: `insert_task` تعيد `False` بصمت؛ `get_tasks` تعيد `[]`.

### Event (حدث)
- **in-memory**: `TASK_EVENTS: asyncio.Queue`.
- **Supabase**: جدول `events`؛ الحقول المُرسلة: `event_type`, `event_data` (كائن JSON منذ R-042 — كان نصاً عبر `json.dumps` فيُخزَّن نصاً داخل `jsonb`).

### AgentStat (إحصاءات وكيل)
- **Supabase**: جدول `agent_stats` — الحمولة المرسلة فعلياً (`upsert_agent_stats`، on_conflict=`agent_name`): `agent_name, specialization, tasks_completed, tasks_failed, total_time_s, last_active_at, updated_at`. تُستدعى من `_run_task` و`/api/chat` في `server.py`. (الأنواع في `docs/sql/001_init.sql` — مُتحققة حياً في R-042.)

### Civilization (حضارة الوكلاء)
- **in-memory فقط**: `CIVILIZATION: Dict[specialization, List[RealSpecialistAgent]]` — 20 تخصصاً × 50 = 1,000 وكيل. لا تخزين دائم.

### OSSAgent (سجل OSS)
- **dataclass ثابت** (`registry/oss_agents.py`): `id, name, source_url, category, capabilities: List[str], license, adapter, description` — ~320 مشروعاً.

### ExternalAgentAdapter
- **in-memory**: `EXTERNAL_AGENTS: List`؛ الطلب: `name, url, model, api_key`.

### agents.yaml (السجل الثابت للسكربتات)
- يُقرأ فقط من `agent_manager.py` و`auto_fetch_agents.py`. الحقول المرصودة: `name, source, branch, specialization`.

## سلوك التخزين (مُتحقق من الكود)

- `get_storage()` تُعيد `SupabaseStorage` دائماً — لا توجد طبقة تخزين بديلة داخل الوحدة.
- كل عملية تخزين تعيد `False`/`[]` عند غياب الإعداد أو فشل الطلب؛ منذ R-042 يُسجَّل رفض HTTP (الحالة + أول 300 حرف من الجسم) كتحذير في سجل `storage`.
- "الرجوع للذاكرة" منطق صريح في `server.py` لكل مسار على حدة (غير متجانس).

## الفجوات

1. لا migrations ولا SQL — R-022.
2. الفهارس والقيود على الجداول غير معلومة.
3. أنواع أعمدة `agent_stats` وقيودها في قاعدة البيانات الفعلية غير معلومة (الحقول المرسلة معروفة).
4. مخطط Supabase يُدار خارج المستودع دون توثيق مرجعي.

## التحقق الحي (R-042، M7/6 — D-038)

- `docs/sql/001_init.sql` طُبق كما هو على PostgreSQL 18 (محلياً) و17 (CI)، وخُدم عبر PostgREST 12.2.3 بنموذج أدوار Supabase.
- الأنواع المُتحقق منها: `tasks` (text/jsonb/double precision/timestamptz)، `agent_stats` (text/integer/double precision/timestamptz)، `events` (bigint identity/text/jsonb/timestamptz) — مطابقة لما يرسله الكود ويقرؤه؛ **لا تعديل لازم** على المخطط (D-019).
- upsert على `agent_stats` (`on_conflict=agent_name` + `resolution=merge-duplicates`) يدمج في صف واحد (فحص فعلي).
- `"now()"` كنص في `finished_at`/`last_active_at`/`updated_at`: تقبله PostgreSQL ويُقيَّم وقت التنفيذ (فحص فعلي).
- الأدلة: `docs/audit/M7/logs/13` (قبل: 18/22) و`15` (بعد: 22/22 + 3/3).
