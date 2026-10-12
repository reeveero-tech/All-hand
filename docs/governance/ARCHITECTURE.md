# المعمارية (ARCHITECTURE)

> **آخر تحديث**: 2026-10-12 — M0

## نظرة عامة

```
All-hand/ (الجذر — 55 دليلاً، 1,981 ملفاً متتبعاً)
├── server.py                    ← الخادم الرئيسي (FastAPI) — 17 مساراً صريحاً + 3 ديناميكية
├── run.py                       ← مُشغّل uvicorn
├── agent_civilization/          ← 1,050 ملفاً متتبعاً (29 خارج specialists + 1,021 داخلها)
│   ├── agents/
│   │   ├── real/                ← إنشاء 1,000 وكيل في الذاكرة (مثبت في سلسلة الاستيراد من server.py)
│   │   └── specialists/          ← 1,021 ملفاً (1,000 وكيل proxy + 21 __init__.py) —
│   │                               يصرّفون إلى real_dispatcher لكن لم يُكتشف مستهلك يُنشئهم
│   ├── core/agents/             ← BaseAgent, CommunicationHub, real_dispatcher
│   ├── routing/auto_router.py   ← auto_route()
│   ├── pipeline/orchestrator.py ← execute_plan()
│   ├── registry/oss_agents.py   ← سجل ~320 وكيل OSS (dataclass ثابت)
│   ├── storage/supabase_store.py ← طبقة Supabase REST
│   └── civilization_routes.py   ← 3 مسارات /civilization/* (تسجيل ديناميكي)
├── agos_kernel/                 ← البنية التحتية المستوردة من server.py (9 ملفات)
│   └── infrastructure/          ← circuit_breaker, metrics, health, redis_queue
├── web/                         ← واجهة React/Vite — بناء ناجح، تُخدم عبر Docker
├── tests/                       ← 61 اختباراً في 3 ملفات
├── agos-kernel/ (dash)          ← 523 ملفاً متتبعاً — غير مرتبط بمسار التشغيل المُتحقق منه
├── src/ + package.json + vite.config.ts + tsconfig.json ← TanStack TS — فشل البناء
└── ~50 دليلاً جذرياً آخر (من أصل 55 دليلاً) ← لم يُتحقق من ارتباطها بمسار التشغيل
```

## المسار الحي (المُتحقق منه)

### 1. `server.py` (الخادم)
- **Framework**: FastAPI + uvicorn.
- **المسارات**: 17 تعريف مسار صريحاً (`@app.get|post|delete`) + 3 مسارات `/civilization/*` مسجلة ديناميكياً = **20 تعريف مسار**. **تنبيه**: تعريف catch-all ومسارات الواجهة مشروط بوجود `web/dist` (راجع `server.py:635-637`) — عدد المسارات المسجلة فعلياً وقت التشغيل يعتمد على وجود الواجهة المبنية.
- **الجداول**: `TASKS` (dict in-memory، سقف 500) + Supabase REST.
- **المصادقة**: `APIKeyHeader("X-API-Key")`؛ `verify_api_key()` مُعرّف غير مُستدعى؛ `ENABLE_AUTH` افتراضياً False.
- **عيب مُتحقق**: `/health` يحسب `status_code` لكن يعيد القاموس دون تمريره → HTTP 200 دائماً.

### 2. `agent_civilization/` (نواة الوكلاء)
- **29 ملفاً خارج `specialists/`** (~2,670 LOC). سلسلة الاستيراد المثبتة من `server.py` تمر عبر `agents/real/` و`core/` و`routing/` و`pipeline/` و`storage/`؛ الملفات الـ29 ليست كلها مثبتة الاستخدام وقت التشغيل — المثبت هو سلسلة الاستيراد فقط.
- ملفات `specialists/` (1,000 وكيل proxy) تستدعي `real_dispatcher.dispatch` فعلاً (تأكيداً لتقرير INTEGRITY) لكن لم يُكتشف كود يُنشئها أو يستوردها وقت التشغيل.
- **الوكلاء**: `create_civilization(agents_per_spec=50)` ينشئ 1,000 `RealSpecialistAgent` (20 تخصصاً × 50).
- **التوجيه**: `auto_route(message)` يختار التخصص (بـ OpenRouter LLM إن توفر مفتاح، وإلا توجيه محلي).
- **التنفيذ**: `execute_plan(plan, civilization, data)`.
- **التخزين**: `SupabaseStorage` عبر REST على جداول `tasks`، `events`، `agent_stats`؛ العمليات تفشل بصمت عند غياب الإعداد، و`server.py` يعوض بمنطق الذاكرة لكل مسار على حدة.

### 3. `agos_kernel/` (البنية التحتية)
- `CircuitBreaker` (dataclass يقبل `name` + `config` فقط — سبب 6 إخفاقات اختبار)، `metrics.py` (عيب مُتحقق في `export_prometheus`: 243)، `health.py`، `redis_queue.py` (عيب مُتحقق في `task_priority`: 406).

### 4. `web/` (الواجهة)
- Vite 5 + React 18 (JavaScript). البناء ناجح. تُخدم عبر `Dockerfile` (`COPY --from=frontend-build /app/web/dist`).

## مسارات غير مرتبطة بمسار التشغيل المُتحقق منه

| المسار | الحجم (ملفات متتبعة) | ملاحظات |
|---|---|---|
| `agos-kernel/` (dash) | 523 | له `pyproject.toml` و`main.py` واختبارات مستقلة؛ CI الحالي يستهدفه. الاستخدام الفعلي غير محسوم — Q-001 |
| `src/` + root TS | 15 | فشل البناء (`lovable-error-reporting` مفقود). Dockerfile لا يستهدفه — Q-002 |
| `agent_civilization/agents/specialists/` | 1,021 | 1,000 stub + 21 `__init__.py`. لا استيراد مُكتشف — Q-004 |
| ~50 دليلاً جذرياً (من أصل 55) | ~300+ ملفاً متتبعاً | (foundation, rie, meta, knowledge-base, cloud, ...) — Q-003 |
| `agents.yaml` | ملف واحد (136KB) | يُقرأ من سكربتات مستقلة فقط — Q-006 |

## التدفقات المُتحققة (منفصلة)

### `POST /api/tasks`
```
POST /api/tasks → TASKS dict (in-memory) + storage.insert_task() (يفشل بصمت إن لم يُعد Supabase)
  → _run_task (asyncio.create_task) → agent.execute → storage.update_task / upsert_agent_stats / insert_event + TASK_EVENTS
```

### `POST /api/chat`
```
POST /api/chat → (LLM لاختيار التخصص إن توفر) → agent.execute مباشرة داخل الدالة
  → TASKS dict + storage.insert_task/update_task/upsert_agent_stats/insert_event
```

### `POST /civilization/execute`
```
POST /civilization/execute → auto_route → tasks_store (dict فقط؛ لا استدعاء Supabase في هذا المسار)
  → execute_plan → dispatch
```

**تنبيه**: لا يُعمم أي تدفق على الآخر — كل مسار يتعامل مع التخزين بطريقته.

## نقاط الالتباس المعمارية

1. **`agos-kernel` (dash) مقابل `agos_kernel` (underscore)**: حزمتان باسم متشابه؛ `underscore` مستوردة من `server.py` و`tests/`؛ `dash` مستهدفة من CI فقط. الاستخدام النهائي غير محسوم (Q-001).
2. **واجهتان أماميتان**: `web/` (تُبنى وتُخدم) و`src/` (لا تُبنى). النية غير موثقة (Q-002).
3. **مصدران لعدد الوكلاء**: `agents.yaml` (سكربتات مستقلة) مقابل `create_civilization()` (وقت التشغيل).
