# منطق العمل (DOMAIN)

## المفاهيم

### الوكيل (Agent)
كيان برمجي متخصص. الأنواع في الكود:
- **داخلي**: `RealSpecialistAgent` — يُنشأ في الذاكرة (`create_civilization`, 20 تخصصاً × 50).
- **خارجي**: `ExternalAgentAdapter` — يُسجل عبر API بـ `name, url, model, api_key`.
- **OSS (سجل)**: `OSSAgent` dataclass ثابت (~320 مشروعاً) — يُعرض في `/civilization/registry`.
- **proxies (specialists/)**: 1,000 ملف وكيل مولّد يصرّف إلى `real_dispatcher` — **موصولة اختيارياً بـ`AGOS_AGENT_IMPL=specialists` منذ R-032/D-037** (الافتراضي `real` لا يبنيها). سجل تاريخي: قبل R-032 لم يُكتشف مستهلك يُنشئها (Q-004 — مُغلقة).

### التخصص (Specialization)
فئة مهارة (20 تخصصاً: analyst, architect, builder, code_generator, communicator, data_processor, db_manager, designer, educator, monitor, ...).

### المهمة (Task)
وحدة عمل: `queued` → `running` → `completed/failed`. تُخزن في `TASKS` dict (سقف 500) + Supabase إن أُعد.

### الحدث (Event)
سجل نشاط (task_created, task_completed, chat_completed, ...) — `TASK_EVENTS` (asyncio.Queue) + جدول Supabase `events`.

### الخطة (Plan)
ناتج `route()` في `agent_civilization/routing/auto_router.py` (الاسم الأصلي للدالة `route`؛ `civilization_routes.py` يستوردها تحت الاسم المستورد `auto_route`): تخصصات مرشحة لتنفيذ رسالة.

## التدفقات (مفصولة — لا يُعمم أي منها على الآخر)

### `POST /api/tasks`
```
create_task → TASKS dict + storage.insert_task (Supabase؛ يفشل بصمت)
  → _run_task (asyncio.create_task)
  → agent.execute → storage.update_task + upsert_agent_stats + insert_event + TASK_EVENTS
```

### `POST /api/chat`
```
chat → اختيار التخصص (LLM إن توفر) → agent.execute داخل الدالة
  → TASKS dict + storage.insert_task + update_task + upsert_agent_stats + insert_event
```

### `POST /civilization/execute`
```
execute → route() (توجيه محلي بالفعل — لا LLM ولا OpenRouter؛ الوثيقة القديمة كانت تكتب `auto_route` وهو الاسم المستورد في `civilization_routes.py`، الاسم الأصلي للدالة `route`) → tasks_store dict (فقط؛ لا استدعاء Supabase في هذا المسار)
  → execute_plan → dispatch
```

## القواعد المرصودة

1. في `/civilization/*` المستخدم لا يختار التخصص (الحضارة تقرر)؛ في `/api/tasks` يحدده صراحة.
2. المصادقة على المسارات الحساسة مُفرضة منذ R-004 (راجع SECURITY)؛ الوضع المفتوح الافتراضي هو قرار تشغيلي، لا غياب فرض. (مُصالحة R-119: كانت تقول "غير مُفرضة فعلياً" — ادعاء قديم من M0 قبل R-004.)
3. التعامل مع غياب Supabase مختلف لكل مسار: `create_task` يكتب للمحاولة ثم يعوض بالذاكرة؛ `/civilization/execute` لا يستدعي التخزين أصلاً؛ القراءة (`list_tasks`) تعود للذاكرة إذا فشل الاستعلام.
## مسؤوليات الحزم (R-104c2)

- `agent_civilization/communication/` — الواجهة العامة للمراسلة بين الوكلاء: `CommunicationHub`/`DeliveryResult` (التنفيذ في `core/agents/communication_hub.py`)؛ يستهلكها `specialist_runtime.py`.
- `agent_civilization/runtime/` — مساعدات الموعد النهائي للمسارات الحية: `make_deadline()`/`remaining()`؛ يستهلكها `_researcher` (ميزانية R-107c).
- `agent_civilization/utils/` — تجميع حمولة LLM: `build_llm_payload(prompt, data)`؛ يستهلكها `_data_processor` (إرسال البيانات مع التعليمة).
## التخصصات المولَّدة (R-105)

- `agent_civilization/agents/specialists/_templates/` — **صنف أساس حقيقي واحد لكل تخصص** (20 أصلاً) + `_spec_layout.py` (تخطيط الأسماء/الافتراضات داخل الحزمة) + `_factory.py` (توليد الأصناف الخمسين لكل تخصص بـ`type()` — لا `exec`).
- الملفات التاريخية الـ1,000 وحدات توافق رقيقة (استيراد + `create()`) — لا منطق مكرر.
