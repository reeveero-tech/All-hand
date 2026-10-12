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

## جرد الدلائل الجذرية غير المرتبطة بمسار التشغيل (R-010 — قرار موثق)

39 دليلاً جذرياً (إلى جانب `agos-kernel/` المُحوَّل إلى R-012) غير مستوردة في أي كود حي (`server.py`, `agent_civilization/`, `agos_kernel/`, `tests/`, `run.py`). البحث أُجري في M2/R-010 وأعطى صفر استيراد من هذه الدلائل في المسارات الحية.

القرار الأول (دفعة M3/R-031 #1، 2026-10-12): **حُذف 13 دليلاً** صافي التوثيق (صفر ملفات py، صفر مستهلك في إعدادات حية) من القائمة، مع وسم أمان `pre-R031-docs-batch1` قبل الحذف. الأعمدة المشطوبة أدناه.

القرار الثاني (دفعة M3/R-031 #2، 2026-10-12): **حُذف 21 دليلاً** المتبقية (فيها كود py لكن بدون مستهلك حي: صفر استيراد دقيق في المسارات الحية، صفر استيراد بين الدلائل نفسها، صفر مرجع في إعدادات التشغيل)، مع وسم أمان `pre-R031-docs-batch2`. بهذا اكتملت جرد R-010/39 دليلاً. المرجع الأمني: الدلائل تعود بـ`git checkout pre-R031-docs-batch2 -- <دليل>`.

| الدليل | ملفات متتبعة | مستورد في كود حي؟ | القرار |
|---|---|---|---|
| ~~agos-canon~~ | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~agos-enterprise~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~agos-factory~~ | 2 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~agos-intelligence~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~aie-intelligence~~ | 5 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~ari-research~~ | 5 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~cgp-genome~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~civilization~~ | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~cloud~~ | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~cognition~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~cognitive~~ | 12 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| consolidation | 2 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| constitution | 3 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~domain~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~ecosystem~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~enterprise~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~evolution~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~execution~~ | 12 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~execution-program~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~fabric~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~foundation~~ | 12 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~governance~~ | 5 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~intelligence~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~knowledge-base~~ | 120 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~meta~~ | 8 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~observatory~~ | 3 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~orchestration~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~products~~ | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~repository-analysis-report~~ | 25 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~research~~ | 7 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~resource~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~rie~~ | 12 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~rie-sprint1~~ | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~rpp-pipeline~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| sdk | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~sdl-loop~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| seos | 6 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~spec-formal~~ | 2 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |
| ~~sprint-000~~ | 1 | لا | قرار مؤجل (M3: حذف/فصل/أرشفة) |

> **ملاحظة**: `src/` و`public/` و`web/` و`docs/` و`.github/` مستثناة لأنها مسارات حية أو معالجة في بنودها. `agos-kernel/` معالج في R-012.


## جرد ملفات `specialists/` (R-011 — قرار موثق)

- العدد: 1,000 ملف وكيل proxy + 21 `__init__.py` (إجمالي 1,021 ملفاً متتبعاً) تحت `agent_civilization/agents/specialists/`.
- المولّد: `generate_real_agents.py` (سكربت مستقل في الجذر، لا يستدعيه كود حي ولا CI).
- ما تفعله: كل ملف يعرّف صنفاً يرث `BaseAgent` ويستدعي `real_dispatcher.dispatch` — أي أن INTEGRITY-REPORT محق في كونها "real-dispatch proxy workers".
- ما لا تفعله: لا يستوردها أي كود حي. المسار الحي (`server.py` → `create_civilization()` → `RealSpecialistAgent`) لا يمر عبر `specialists/` إطلاقاً؛ `RealSpecialistAgent` لا يرث `BaseAgent`.
- البحث: صفر استيراد لـ`agents.specialists` في المسارات الحية (`server.py`, `run.py`, `real_agents.py`, `orchestrator.py`, `routing/`).

**القرار (D-015)**: لا يُحذف شيء في M2. القرار الفردي (حذف، أو توليد عند الحاجة، أو توصيل بمسار التشغيل) مؤجل إلى M3. أي حذف مستقبلي يتطلب تحديث هذا الجرد و`generate_real_agents.py` معاً.


## جرد ازدواج `agos-kernel/` مقابل `agos_kernel/` (R-012 — قرار موثق)

حقيقتان مختلفتان تماماً، لا ازدواج أكيد:

| الجانب | `agos_kernel/` (حية) | `agos-kernel/` (غير حية) |
|---|---|---|
| الملفات المتتبعة | 9 ملفات Python | 523 مدخلاً (469 ملفاً) |
| المحتوى | `infrastructure/` + `agent_production/` | 146 مدخلاً: `core/`, `engine/`, `domains/`, `events/`, `governance/`, ... واختبارات خاصة |
| من يستورده | `server.py` (مسار التشغيل الفعلي) | لا كود حي (فحص الاستيراد: صفر) |
| CI | (كانت) هدف `pytest agos-kernel/` — اختباراته فاشلة تاريخياً (4 أخطاء جمع) | — |
| pyproject | — | يعرّف حزمة `agos-kernel` مع `[tool.setuptools.packages.find] include = ["agos_kernel*"]` لكن **لا يوجد دليل `agos_kernel` داخل `agos-kernel/`** — الحزمة غير قابلة للبناء عملياً |

**القرار (D-016)**: `agos_kernel/` هي الحية ويجب أن تبقى. `agos-kernel/` غير مستوردة في أي كود حي، واختباراتها فاشلة تاريخياً (4 أخطاء جمع: `cannot import name 'AGOSKernel' from 'core'`، `No module named 'platform.runtime'`)، وبناءها كحزمة PyPI غير صالح (لا حزمة مطابقة داخلها). قرار الحذف/الفصل/الأرشفة مؤجل إلى M3. **إزالة `agos-kernel/` من هدف CI تمت في R-002** (دفعة M1/2) — البوابة الآن تستهدف `tests/` الحية.
