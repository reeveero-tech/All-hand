# المعمارية (ARCHITECTURE)

> **آخر تحديث**: 2026-10-15 — R-119 (مصالحة ادعاءات قديمة مع الواقع — خلل وثائق مقابل واقع).
>
> **حدود الأقسام**: "المسار الحي (المُتحقق منه)"، "التدفقات المُتحققة"، "تحديث R-032"، و"قرار مصير `agos-kernel/`" أقسام حالية مرجعية. "مسارات غير مرتبطة بمسار التشغيل المُتحقق منه"، "جرد الدلائل الجذرية"، "جرد ملفات `specialists/`"، "جرد ازدواج `agos-kernel/`"، و"نقاط الالتباس المعمارية" سجل تاريخي لجرد M0 (2026-10-12) — الحالة الحالية موسومة داخل كل منها بـ"مصالحة R-119". لا يُقرأ أي وصف في سجل M0 كوصف حالي دون وسم مصالحة.
>
> **الادعاءات المُصالحة (9)**: `verify_api_key` مُستدعى فعلاً في 5 مسارات (server.py:520، 587، 633، 698، 729)؛ `/health` يعيد 503 عند `unhealthy` (server.py:345)؛ بناء `src/` ناجح منذ R-013 (D-017)؛ `agents.yaml` حُذف في R-031b (D-025)؛ `agos-kernel/` (dash) حُذف في R-033 (D-026)؛ 10 أدلة جذرية متتبعة / 1,546 ملفاً متتبعاً (كان 55/1,981)؛ 29 ملف اختبار `test_*.py` (كان 3)؛ 1,077 ملفاً في `agent_civilization/` (كان 1,052)؛ 33 خارج `specialists/` + 1,044 داخلها (كان 29/1,021)؛ 19 تعريف مسار صريح + catch-all + 3 ديناميكية = 23 (كان 17+3=20).

## نظرة عامة

```
All-hand/ (الجذر — 10 أدلة جذرية متتبعة، 1,546 ملفاً متتبعاً — قياس 2026-10-15، دليل R-119)
├── server.py                    ← الخادم الرئيسي (FastAPI) — 19 مساراً صريحاً + 1 catch-all + 3 ديناميكية = 23 تعريف مسار (مطابق لـ API.md) — قياس R-119
├── run.py                       ← مُشغّل uvicorn
├── agent_civilization/          ← 1,077 ملفاً متتبعاً (33 خارج specialists + 1,044 داخلها — قياس 2026-10-15)
│   ├── agents/
│   │   ├── real/                ← إنشاء 1,000 وكيل في الذاكرة (مثبت في سلسلة الاستيراد من server.py)
│   │   ├── executors/data_worker.py ← DataWorkerAgent + process_text() — LLM-backed لمدخل نصي فقط في _data_processor (R-104، D-047)
│   │   └── specialists/          ← 1,044 ملفاً (1,000 وحدة توافق رقيقة + 44 بنية/تهيئة — R-105/D-049)؛
│   │                               يصرّفون إلى real_dispatcher؛ موصولة اختيارياً بـAGOS_AGENT_IMPL=specialists (D-037)
│   ├── core/agents/             ← BaseAgent, CommunicationHub, real_dispatcher
│   │   └── craft.py             ← builder/architect/designer/modifier/مولّد المواصفات/تحليل النص — حتمي ومتحقق داخلياً (R-102، D-045)
│   ├── core/experience.py       ← الذاكرة والتعلم حول المعالج الحقيقي في المسار الحي (R-103، D-046) — يستخدم agos_kernel/agent_production
│   ├── core/safety.py           ← سياسة الأدوات الحقيقية (R-100، D-044): إذن تنفيذ الكود + العزل، حارس العناوين، SQLite
│   ├── routing/auto_router.py   ← route() (الاسم الأصلي للدالة `route`؛ `civilization_routes.py` يستوردها كـalias تحت الاسم `auto_route`: `from agent_civilization.routing import route as auto_route`)؛ توجيه محلي بالفعل (R-106)
│   ├── pipeline/orchestrator.py ← execute_plan()
│   ├── registry/oss_agents.py   ← سجل ~320 وكيل OSS (dataclass ثابت)
│   ├── storage/supabase_store.py ← طبقة Supabase REST
│   └── civilization_routes.py   ← 3 مسارات /civilization/* (تسجيل ديناميكي)
├── agos_kernel/                 ← البنية التحتية المستوردة من server.py (9 ملفات)
│   └── infrastructure/          ← circuit_breaker, metrics, health, redis_queue
├── web/                         ← واجهة React/Vite — بناء ناجح، تُخدم عبر Docker
├── src/                         ← واجهة الجذر (TanStack) — بناء ناجح منذ R-013 (M2/4، D-017)
├── scripts/                    ← سكربتات التشغيل والقياس (R-051، R-053، R-100، R-119…)
├── public/                      ← أصول ثابتة
├── docs/                        ← وثائق الحوكمة + الأدلة (governance/، audit/، sql/)
├── .github/                     ← سير عمل CI (ci.yml، governance-gate.yml)
├── .lovable/                    ← دليل متتبع في المستودع (محتواه بيانات بنية مشروع أصلية؛ لم يُجرَ فحص مستهلكين له في هذه الدفعة)
└── tests/                       ← 29 ملف اختبار `test_*.py` + conftest.py + __init__.py (435 ناجحاً + 3 متخطاة + 4 مستبعدة network — قياس 2026-10-15)
```

### الأدلة (R-119)

كل ادعاء في هذا القسم مُتحقق منه بأدلة من الشجرة الفعلية على SHA `6439e5a1bd68b9d198ca4bb0daca46001cd3a2fa` (فرع `m7/r119-architecture-reconcile`) — راجع `docs/audit/M7/logs/raw/124_r119_architecture_reconcile_evidence.log`.

## المسار الحي (المُتحقق منه)

### 1. `server.py` (الخادم)
- **Framework**: FastAPI + uvicorn.
- **المسارات**: **19 تعريف مسار صريحاً** (`@app.get|post|delete` — قياس R-119: 19 تعريفاً في `server.py` + `/{full_path:path}` catch-all في السطر 759 = 20) + 3 مسارات `/civilization/*` مسجلة ديناميكياً في `civilization_routes.py` = **23 تعريف مسار** (مطابق لـ API.md). **تنبيه**: تعريف catch-all ومسارات الواجهة مشروط بوجود `web/dist` — عدد المسارات المسجلة فعلياً وقت التشغيل يعتمد على وجود الواجهة المبنية.
- **الجداول**: `TASKS` (dict in-memory، سقف 500) + Supabase REST.
- **المصادقة**: `APIKeyHeader("X-API-Key")`؛ `verify_api_key()` **مُستدعى فعلاً** في 5 مسارات (`server.py:520, 587, 633, 698, 729`) وممرّر إلى مسارات `/civilization/*` عبر `server.py:747` — **(مصالحة R-119: الوثيقة كانت تقول "غير مُستدعى" وهذا ادعاء قديم من M0 قبل R-004)**. `ENABLE_AUTH` يتحكم في فعالية المفتاح (انظر R-004/M1/3).
- **`/health`**: يعيد 503 عند `unhealthy` (server.py:345) — **(مصالحة R-119: الوثيقة كانت تقول "HTTP 200 دائماً" وهذا ادعاء قديم من M0 قبل R-004؛ أُصلح في R-004 ووثق في R-068)**.

### 2. `agent_civilization/` (نواة الوكلاء)
- **33 ملفاً متتبعاً خارج `specialists/`** (قياس R-119 على SHA `6439e5a`؛ كان 29 في M0؛ `~2,670 LOC` ادعاء قديم من M0 غير مُعاد قياسه). سلسلة الاستيراد المثبتة من `server.py` تمر عبر `agents/real/` و`core/` و`routing/` و`pipeline/` و`storage/`؛ الملفات ليست كلها مثبتة الاستخدام وقت التشغيل — المثبت هو سلسلة الاستيراد فقط.
- ملفات `specialists/` (1,044 ملفاً متتبعاً — منها 1,000 وحدة توافق رقيقة تُصدر أصنافاً مولّدة من مصنع `type()` + 44 ملف بنية/تهيئة — R-105/D-049) تستدعي `real_dispatcher.dispatch` فعلاً. **منذ R-032 (D-037) موصولة اختيارياً**: `AGOS_AGENT_IMPL=specialists` يجعل `create_civilization` يبني الحضارة من أصنافها عبر `agents/real/specialist_runtime.py` (`SpecialistBackedAgent` يغلف نسخة حقيقية من الصنف ويعرض عقد `RealSpecialistAgent` نفسه)، مع `CommunicationHub` مشترك. الافتراضي (`real`) لم يتغير.
- **الوكلاء**: `server.py` يقرأ `AGENTS_PER_SPEC` (R-041؛ افتراضي 50) ويمرره إلى `create_civilization(agents_per_spec)` → 1,000 `RealSpecialistAgent` افتراضياً (20 تخصصاً × 50)؛ 60 وكيلاً عند ضبط 3. القيم غير الصالحة (صفر/سالب/نص) تُرفض برسالة بدء واضحة.
- **التوجيه**: `route()` في `agent_civilization/routing/auto_router.py` — توجيه محلي بالفعل؛ `civilization_routes.py` يستوردها تحت الاسم المستورد `auto_route` (`from agent_civilization.routing import route as auto_route` — الوثيقة القديمة كانت تسميها `auto_route` كاسم الدالة الأصلية وهو ليس الاسم الأصلي). يستخدمه `/civilization/*`. لـ`/api/chat`: يحاول اختيار التخصص عبر `get_llm()` عندما تكون `llm.available` صحيحة؛ وإذا لم يُنتج تخصصاً صالحاً، يستخدم `route_request()` المحلي (R-106).
- **التنفيذ**: `execute_plan(plan, civilization, data)`.
- **التخزين**: `SupabaseStorage` عبر REST على جداول `tasks`، `events`، `agent_stats`؛ العمليات تفشل بصمت عند غياب الإعداد، و`server.py` يعوض بمنطق الذاكرة لكل مسار على حدة.

### 3. `agos_kernel/` (البنية التحتية)
- `CircuitBreaker` (dataclass يقبل `name` + `config`)، `metrics.py`، `health.py`، `redis_queue.py`. **مصالحة R-119**: الوصف القديم "عيب مُتحقق في export_prometheus/task_priority/CircuitBreaker" كان ادعاءات M0 قبل إصلاحات R-003a-d — عولجت في M1 (راجع QUALITY.md § R-068).

### 4. `web/` (الواجهة)
- Vite 8 + React 18 (JavaScript). البناء ناجح. تُخدم عبر `Dockerfile` (`COPY --from=frontend-build /app/web/dist`). **مُصالحة R-119: كانت "Vite 5" وهذا قديم — القياس الحالي vite ^8.3.4 (R-071).**

### 5. `src/` + root TS (واجهة الجذر — TanStack)
- **بناء ناجح** منذ R-013 (M2/4، D-017؛ 2026-10-12) — استُكملت وحدات `lovable-error-reporting` و`error-capture` و`error-page`. **مصالحة R-119: الوثيقة كانت تقول "فشل البناء" وهذا ادعاء قديم من M0**. الجزء غير المتحصل عليه: تقرير الربط الحقيقي بخدمات AGOS (Q-002 — محلولة جزئياً).

## مسارات غير مرتبطة بمسار التشغيل المُتحقق منه

**مصالحة R-119**: هذا الجدول يعكس الحالة التاريخية لـ M0 (2026-10-12). الحالة الحالية (2026-10-15):

- `agos-kernel/` (dash) — **حُذف** في R-033 (D-026، 2026-10-12)؛ أُرشف في فرع `archive/agos-kernel-v1`.
- `agents.yaml` — **حُذف** في R-031b (D-025، 2026-10-12)؛ استرجاعه: `git checkout pre-R031b-cloud-scripts -- agents.yaml`.
- `src/` + root TS — **بناء ناجح** منذ R-013 (M2/4، D-017؛ 2026-10-12).
- `~50 دليلاً جذرياً` — **حُذفت** كلها في R-031/R-031b/R-033 (توزيع D-021/D-022/D-024/D-025/D-026: 14 + 21 + 4 دلائل في R-031، و5 دلائل `cloud_*` + 4 سكربتات جذرية في R-031b، و`agos-kernel/` + سكربت في R-033)؛ الجذر الآن 10 أدلة جذرية متتبعة (انظر شجرة النظرة العامة أعلاه).
- `specialists/` — موصولة اختيارياً عبر `AGOS_AGENT_IMPL=specialists` منذ R-032 (M7/4، D-037؛ 2026-10-13).

الجدول التالي سجل تاريخي لجرد M0 (قبل الحذف):

| المسار | الحجم (ملفات متتبعة) | ملاحظات |
|---|---|---|
| ~~`agos-kernel/` (dash)~~ | 523 | له `pyproject.toml` و`main.py` واختبارات مستقلة؛ CI الحالي يستهدفه. الاستخدام الفعلي غير محسوم — Q-001 |
| `src/` + root TS | 15 | ~~فشل البناء~~ **بناء ناجح منذ R-013**. Dockerfile لا يستهدفه — Q-002 |
| `agent_civilization/agents/specialists/` | 1,021 (تاريخي M0: 1,000 proxy + 21 `__init__.py`؛ الحالي 1,044 — راجع الجرد أدناه) | ~~لا استيراد مُكتشف~~ **موصولة اختيارياً بـAGOS_AGENT_IMPL=specialists منذ R-032/D-037** — Q-004 |
| ~50 دليلاً جذرياً (من أصل 55) | ~300+ ملفاً متتبعاً | (foundation, rie, meta, knowledge-base, cloud, ...) — Q-003 |
| `agents.yaml` | ملف واحد (136KB) | ~~يُقرأ من سكربتات مستقلة فقط~~ **حُذف في R-031b (D-025)** — Q-006 |

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
POST /civilization/execute → route() → tasks_store (dict فقط؛ لا استدعاء Supabase في هذا المسار)
  → execute_plan → dispatch
```

**تنبيه**: لا يُعمم أي تدفق على الآخر — كل مسار يتعامل مع التخزين بطريقته.

## نقاط الالتباس المعمارية

**مصالحة R-119 (2026-10-15)**: النقاط الثلاث أدناه حُسمت في M3/M7 — النقطة 1 والثالثة حُسمتا كلياً، والثانية جزئياً (Q-002 — محلولة جزئياً). الحالة الحالية:

1. ~~`agos-kernel` (dash) مقابل `agos_kernel` (underscore)~~ — **حُسم في R-033/D-026 (2026-10-12)**: `agos-kernel/` (dash) حُذف وأُرشف في `archive/agos-kernel-v1`؛ `agos_kernel/` (underscore) هي الحية.
2. ~~واجهتان أماميتان~~ — **حُسم جزئياً**: `web/` تُبنى وتُخدم؛ `src/` (TanStack) تُبنى بنجاح منذ R-013 (D-017) لكنها ليست في صورة Docker. التقرير الحقيقي للربط بخدمات AGOS مؤجل (Q-002 — محلولة جزئياً).
3. ~~مصدران لعدد الوكلاء~~ — **حُسم في R-031b (D-025)**: `agents.yaml` حُذف؛ `create_civilization()` هو المصدر الوحيد وقت التشغيل (يقرأ `AGENTS_PER_SPEC` — R-041).

## جرد الدلائل الجذرية غير المرتبطة بمسار التشغيل (R-010 — قرار موثق)

39 دليلاً جذرياً (إلى جانب `agos-kernel/` المُحوَّل إلى R-012) غير مستوردة في أي كود حي (`server.py`, `agent_civilization/`, `agos_kernel/`, `tests/`, `run.py`). البحث أُجري في M2/R-010 وأعطى صفر استيراد من هذه الدلائل في المسارات الحية.

القرار الأول (دفعة M3/R-031 #1، 2026-10-12): **حُذف 14 دليلاً** صافي التوثيق (صفر ملفات py، صفر مستهلك في إعدادات حية) من القائمة، مع وسم أمان `pre-R031-docs-batch1` قبل الحذف. الأعمدة المشطوبة أدناه. (تصحيح M3/8: الدفعة كانت 14 لا 13.)

القرار الثاني (دفعة M3/R-031 #2، 2026-10-12): **حُذف 21 دليلاً** المتبقية (فيها كود py لكن بدون مستهلك حي: صفر استيراد دقيق في المسارات الحية، صفر استيراد بين الدلائل نفسها، صفر مرجع في إعدادات التشغيل)، مع وسم أمان `pre-R031-docs-batch2`. دفعة ثالثة (M3/9، 2026-10-12): حُذف الـ4 المتبقية (`consolidation/`, `constitution/`, `sdk/`, `seos/`) مع وسم أمان `pre-R031-docs-batch3`. بهذا اكتمل جرد R-010: 14 + 21 + 4 = 39. المرجع الأمني: الدلائل تعود بـ`git checkout pre-R031-docs-batch3 -- <دليل>` (وللدفعتين الأوليين: `pre-R031-docs-batch1`/`pre-R031-docs-batch2`).

### المجموعة الفائتة عن جرد M0 (R-031b — اكتُشفت في تحديث الحالة بعد R-031)

**القرار (D-025، دفعة M3/10)**: حُذفت المجموعة كاملة مع وسم أمان `pre-R031b-cloud-scripts`: 5 دلائل `cloud_*` (وحدات مستقلة تماماً بلا استيرادات متبادلة ولا مستهلك حي؛ مجموعة PRODUCT-000076..080)، و4 سكربتات جذرية (`agent_manager.py`, `auto_fetch_agents.py`, `generate_real_agents.py`, `generate_agents_catalogue.py` — مولدات كتابة فقط، غير مستوردة من أي كود حي؛ `auto_fetch_agents` يحتاج `requests` و`agent_manager` يحتاج `GitPython` وكلاهما ليس في `requirements.txt` فلم يعملا في CI ولا Docker قط)، و`agents.yaml` (4,001 سطراً، 1,000 مدخل، يُقرأ من السكربتات الأربعة المحذوفة فقط؛ أسماء مداخله تطابق ملفات `specialists/` الـ1,000). الاسترجاع: `git checkout pre-R031b-cloud-scripts -- <المسار>`. القرار مُنسّق مع R-032: بقاء `specialists/` مرتبطاً بـ`real_dispatcher` يُحسم هناك، و`agents.yaml` حُذف لأن مستهلكيه الوحيدين حُذفا معه.

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


## جرد ملفات `specialists/` (R-011 — قرار موثق؛ مُصالَح R-119)

- العدد الإجمالي: **1,044 ملفاً متتبعاً** (قياس R-119). التوزيع الفعلي (وفق R-105/D-049):
  - 23 ملفاً في `_templates/` (20 قالب `*_base.py` + `_factory.py` + `_spec_layout.py` + `__init__.py`) + 1 `__init__.py` الجذري + 20 `__init__.py` مهيئ حزم التخصصات = 44 ملفاً بنية/تهيئة.
  - 1,000 وحدة توافق رقيقة موزعة على 20 دليلاً فرعياً (50 لكل تخصص) تعيد تصدير أصناف مولّدة من `type()`.
- المولّد: `generate_real_agents.py` — ~~سكربت مستقل في الجذر~~ **حُذف في R-031b (D-025)**؛ التوحيد الحالي عبر قوالب `_templates/` + مصنع `type()` (R-105/D-049).
- ما تفعله (تاريخياً): كل وحدة تُصدر صنفاً يرث `BaseAgent` ويستدعي `real_dispatcher.dispatch` — أي أن INTEGRITY-REPORT محق في كونها "real-dispatch proxy workers".
- ما لا تفعله (تاريخياً): كان لا يستوردها أي كود حي وقت جرد M0؛ **تغيّر مع R-032 (M7/4، D-037)**: الآن `AGOS_AGENT_IMPL=specialists` يبني الحضارة من أصنافها عبر `specialist_runtime.py` — مسار اختياري موثق. الافتراضي (`real`) لا يمر عبر `specialists/`.

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

## قرار مصير `agos-kernel/` (R-033 — D-026، دفعة M3/11)

حُذف `agos-kernel/` (515 ملفاً، 469 ملف py، 30 ملف اختبار، 5.0MB) من `main` بعد أرشفته في فرع دائم `archive/agos-kernel-v1` (يشير إلى `ceead7d`) ووسم `pre-R033-agos-kernel-removal`. الاسترجاع: `git checkout archive/agos-kernel-v1 -- agos-kernel`.

الأسباب المُتحقق منها: (1) صفر استيراد حي وصفر مرجع في CI/Docker/render/npm (R-012)؛ (2) اختباراته فاشلة تاريخياً؛ (3) `pyproject.toml` الخاص به يطلب حزمة `agos_kernel*` غير موجودة داخله؛ (4) عيب بنيوي مُعاد إنتاجه: الدليل `agos-kernel/platform/` يحجب وحدة `platform` من المكتبة القياسية، فيفشل `import platform` نفسه عند تشغيل أي أمر من داخل `agos-kernel/` (ويفشل pytest عبر ddtrace لهذا السبب). لم أختبر هل يكفي إعادة تسمية هذا الدليل وحده لتشغيل الحزمة؛ لم يكن ذلك مطلوباً لأن البند الحاكم هو غياب أي مستهلك.

كما حُذف `push_to_github.sh` (سكربت تاريخي لمرة واحدة يشير فقط إلى `agos-kernel/finalization`، مسار مطلق `/workspace`، غير مستدعى من أي مكان).

`agos_kernel/` (الشرطة السفلية، 9 ملفات) هي الحية ولم تُمس.


## تحديث R-032 (M7/4) — مسار التشغيل الاختياري عبر specialists
```
AGOS_AGENT_IMPL=real (افتراضي)   → RealSpecialistAgent ×1000
AGOS_AGENT_IMPL=specialists      → SpecialistBackedAgent ×1000
                                    └─ inner: AnalystAgent0015 … (BaseAgent)
                                        └─ BaseAgent.execute → _process_task (مغلف) → real_dispatcher.dispatch
```
- قيمة غير معروفة → `ValueError` عند البدء (لا رجوع صامت).
- إصلاح مصاحب: `BaseAgent.execute` كان يعيد `ok=True` حتى عند فشل المهمة — صار ينقل النتيجة الفعلية.
