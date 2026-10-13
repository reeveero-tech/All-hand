# أدلة التحقق — المرحلة M6 (بنية بديلة وقابلية تشغيل خارجية)

> كل فحص مُوثق في logs/ باسم البند المطلوب. أكواد الخروج مأخوذة مباشرة من
> تشغيل الفحص لا من نهاية السجل — نتيجة `0` تعني نجاحاً فعلياً.

## R-043 (M6/1) — اختبار تكامل web/ مع API

الهدف: عقد API الذي تستهلكه واجهة web/ فعلياً (web/src/api.js) — 10 اختبارات
جديدة في `tests/test_web_api_contract.py` تتحقق من كل نداء تُصدره الواجهة
ضد الخادم الحقيقي عبر TestClient، مع شكل الاستجابة بما يطابق ما تقرأه
المكونات (Dashboard.jsx، Specializations.jsx، Agents.jsx، Tasks.jsx، Chat.jsx).

| نداء الواجهة | ما يقرؤه المكون | الاختبار | النتيجة |
|---|---|---|---|
| GET /api/system/status | Dashboard.jsx (status, total_agents, tasks_*) | `test_system_status_fields_dashboard_reads` | ✓ |
| GET /api/specializations | Specializations.jsx (name, description, icon, agents) | `test_specializations_shape_specializations_component` | ✓ |
| GET /api/agents (+?specialization=) | Agents.jsx (name, specialization) | `test_agents_list_and_filter` | ✓ |
| POST /api/tasks {specialization,prompt,data} | Specializations.jsx (task_id, status) | `test_e2e_task_from_ui_shape_and_poll` | ✓ |
| GET /api/tasks?limit= | Tasks.jsx (id, prompt, status) | `test_list_tasks_frontend_shape` | ✓ |
| GET /api/events?limit= | Dashboard.jsx (events) | `test_events_list_dashboard` | ✓ |
| POST /api/chat {message} | Chat.jsx (task_id, routed_to, outcome) | `test_chat_frontend_contract` | ✓ |
| POST /api/agents/external + DELETE | Agents.jsx | `test_external_agent_register_and_remove` | ✓ |
| SSE /api/events/stream | App.jsx (createEventSource) | `test_sse_stream_yields_event` | ✓ |

سيناريو كامل (نفس payload الواجهة): إنشاء مهمة analyst → poll → النتيجة
مكتملة مع result.ok. علامة: تأكيد أن التخصص غير المعروف يُرفض بـ400.

### قيود موثقة (صدقاً)
- SSE اختُبر باستدعاء endpoint مباشرة مع حدث مُعد مسبقاً — TestClient يعلق
  على stream لانهائي. هذا يثبت أن المولد يصدر حدثاً بالشكل الصحيح؛ لا يثبت
  التكامل الكامل لEventSource عبر HTTP حقيقي (نطاق docker job يغطي بناء
  الصورة وتشغيلها و/health — لكن SSE نفسه ليس مغطى).
- auth في CI/الاختبارات معطّل (ENABLE_AUTH=False بلا ADMIN_API_KEY) —
  عقد POST /api/tasks و/api/chat و/api/agents/external مع auth مفعل
  مغطى بM4 (R-041 tests) وليس هنا.

| الفحص | النتيجة | الدليل |
|---|---|---|
| pytest (143 + 10 جديدة) | 153/153، exit 0 | `logs/01_r043_pytest.log` |
| ruff (كل المسارات) | صفر أخطاء، exit 0 | `logs/02_r043_ruff.log` |

---

## R-054 (M6/2) — الذاكرة والتعلم والتواصل بين الوكلاء (إصلاح + اختبارات سلوكية)

**الحاجة**: `agent_production` كان مُعرّفاً لكن بلا أي مستهلك حي (صفر استيراد من server.py
أو agent_civilization) — عيوب RISKS/R-010 مستمرة. المعيار: "اختبارات سلوكية تثبت أن الذاكرة
والتعلم يؤثران على نتيجة مهمة لاحقة (وليس مجرد استيراد)".

**الإصلاح (داخل agent_production فقط — لا توصيل في server.py)**:
- `DynamicAgent.__init__` يقبل `learning_runtime` و`memory_system` اختيارياً (توافق رجعي).
- `DynamicAgent.execute` يستدعي `_recall_context` (memory recall قبل التنفيذ) ثم
  `_select_approach` (learning best approach) ثم `_execute_task`.
- `_execute_task` (السلوك الافتراضي) يستهلك `task["approach"]` و`task["context"]["memories"]`
  فعلياً: النهج "detailed" يضم الذاكرة المسترجعة في النتيجة؛ "concise" يعيد نتيجة مختصرة؛
  default يعيد الرسالة الأصلية.
- `_record_experience` يسجل تلقائياً إلى learning_runtime (عقد النتيجة: النهج المستخدم
  فعلاً + المدة + النجاح الفعلي — لا يسجل فشلاً كنجاح).
- `evolve()` يورث نفس learning_runtime وmemory_system.
- `AgentFactory.create_agent` يقبل ويمرر الاعتمادين.

**الاختبارات السلوكية** (`tests/test_memory_learning_comm.py`، 9 اختبارات):
- الذاكرة: `test_memory_changes_task_result` — نفس المهمة تعطي "no prior memories" ثم
  المحتوى المسترجع فعلاً (تمييزي: `test_memory_disabled_does_not_change_result`).
- التعلم: `test_learning_changes_task_result` — default → concise (نهج متعلم يُستخدم فعلاً
  ويغيّر شكل النتيجة). `test_learning_disabled_does_not_change_result` تمييزي.
- التسجيل: `test_agent_records_experience_to_learning_runtime` — الوكيل يسجل تنفيذه
  تلقائياً (المسار الموثّق). `test_agent_evolve_inherits_memory_and_learning`.
- التواصل: `test_hub_delivers_message_to_specific_recipient` (وصول للمستلم المحدد دون
  المرسل)، `test_hub_broadcast_reaches_all_registered_agents` (وصول البث للجميع)،
  `test_hub_send_to_unregistered_fails` (فشل واضح لغير المسجل).

**قيود موثقة (صدقاً)**:
- الواجهة البرمجية ل`_execute_task` الافتراضية تستهلك النهج والذاكرة — لكن هي **سلوك
  افتراضي قابل للتجاوز**؛ أي مستهلك مستقبلي يلغي تجاوزه قد يفقد هذه الحماية (خارج النطاق
  — قرار الربط بمسار التشغيل مرتبط بـR-032/R-050).
- `recall()` يطابق بالاحتواء النصي (substring) — ليست دلالياً؛ الاسترجاع يتطلب أن يحتوي
  محتوى الذاكرة على نص المهمة أو جزءاً منه. القيد موثق.

| الفحص | النتيجة | الدليل |
|---|---|---|
| pytest (154 + 9 جديدة) | 163/163، exit 0 | `logs/03_r054_pytest.log` |
| ruff (كل المسارات) | صفر أخطاء، exit 0 | `logs/04_r054_ruff.log` |
| بناء web/ والجذر | rc=0 كلاهما | `logs/05_r054_builds.log` |
| إثبات سلوكي (4 حالات) | مُوثق بالكامل | `logs/06_r054_behavioral_proof.log` |

---

## R-053 (M6/3) — التوسع إلى العدد المستهدف من الوكلاء (قياسات موثقة)

المعيار: `create_civilization(agents_per_spec)` يعمل بالعدد المستهدف (حسب R-050 — والافتراضي
المؤقت في Q-016: 50/تخصص × 20 = 1000 وكيل) **ضمن حدود ذاكرة/زمن موثقة مع قياسات**.

**القياسات** (من تشغيل `scripts/run_r053_scale_audit.py` — السجل الكامل logs/07):
- إنشاء العدد المستهدف (1000 وكيل): **0.002s**؛ ذروة RSS ~27.7MB
- تنفيذ 1000 مهمة متزامنة (كل وكيل مهمة، عبر الموزّع الحقيقي بمهام ملائمة لكل تخصص):
  **~1.04s**، نجاح 1000/1000، ذروة RSS ~41MB
- هامش التوسع: 2× المستهدف (2000 وكيل) يعمل — 0.002s، لا سقف صلب في `create_civilization`

**حدود موثقة (الحد الأقصى مفتوح)**:
- لا يوجد سقف صلب في `create_civilization(agents_per_spec)` — العدد يحدده متغير البيئة
  `AGENTS_PER_SPEC` (R-041) والموارد المتاحة.
- الذاكرة لكل `RealSpecialistAgent` ~0.4KB (dataclass خفيف، لا يحمل نموذجاً) — القياس
  عبر tracemalloc لألف كائن.
- الاختبارات في `tests/test_civilization_scale.py` (5 اختبارات): تحمي الحدود النوعية
  (1000/2000/10000 وكيل، كل تخصص قابل للتنفيذ بمهمة ملائمة، لا سقف صلب).

**ملاحظة**: 1000/1000 نجاح في الحمل المتزامن يشمل تخصصات الاتصال الخارجي (api_integrator،
network_client) بمهامها الفعلية — نجاح القياس لا يعتمد على إخفاء هذه التخصصات.

| الفحص | النتيجة | الدليل |
|---|---|---|
| سكربت القياس | exit 0، نجاح 1000/1000 | `logs/07_r053_scale_audit.log` |
| pytest (163 + 5 جديدة) | 168/168، exit 0 | `logs/08_r053_pytest.log` |
| ruff (كل المسارات) | صفر أخطاء، exit 0 | `logs/09_r053_ruff.log` |
| بناء web/ والجذر | rc=0 كلاهما | `logs/10_r053_builds.log` |

---

## R-055 (M6/4) — سيناريوهات شاملة من البداية للنهاية (2026-10-12)

**البند**: R-055. **الترتيب**: دفعة M6/4.

**النطاق**: إضافة `tests/test_e2e_scenarios.py` (6 اختبارات) + `scripts/run_r055_scenarios.py` + 5 إصلاحات أصلية في `real_dispatcher.py` / `orchestrator.py` كانت تمنع السيناريوهات من العمل.

**المعيار**: "3 سيناريوهات على الأقل (هدف معقد → خطة متعددة الوكلاء → نتيجة مخزنة) تعمل آلياً في CI أو في اختبار تحقق موثق." — **محقق**: 3 سيناريوهات تعمل آلياً في pytest (CI)، وسيناريو كامل يُخزّن النتيجة فعلياً عبر `/civilization/execute` → `/api/tasks/{id}`.

### السيناريوهات
1. **برمجي** (كود → مراجعة → اختبار): 5 مراحل عبر 5 وكلاء (analyst → architect → code_generator → reviewer → test_runner)؛ النتيجة `exit=0`.
2. **تحليلي** (فهم → خطة → تنفيذ → تحقق): 4 مراحل عبر 4 وكلاء؛ النتيجة `valid python object`.
3. **معماري** (فهم → خطة → تنفيذ → مراجعة → اختبار): 5 مراحل؛ النتيجة `exit=0`.
4. **سيناريو كامل (تخزين فعلي)**: POST `/civilization/execute` → خطة (4+ مراحل) → تنفيذ → تخزين النتيجة في `TASKS` → استرجاعها عبر GET `/api/tasks/{id}`.

### إصلاحات أصلية (سبب جذري — كل واحدة كانت تمنع السيناريو)
- `_extract_code` رفض كوداً من سطر واحد (`"print(1+1)"`).
- `_validator` فشل المسار كاملاً إذا لم تكن الخلاصة نص JSON.
- `orchestrator` سجل `ok=True` دائماً بغض النظر عن نتيجة الوكيل.
- `_analyst` رفض مرحلة "فهم الطلب" النصية.
- الـorchestrator مررت `data` لمرحلة "execute" فقط ففشلت مرحلة الاختبار ("no code to run").

### الفحوص (أكواد مأخوذة مباشرة من التشغيل)
- `pytest tests/ -q`: 174 passed، rc=0.
- `ruff check --select F,E722 server.py agent_civilization agos_kernel tests scripts`: صفر أخطاء، rc=0.
- `python3 scripts/run_r055_scenarios.py`: rc=0 — تقرير السيناريوهات الثلاثة (logs/13).
- `cd web && npm run build`: rc=0.
- `npm run build` (الجذر): rc=0.

### السجلات
- `logs/11_r055_pytest.log` — نتيجة pytest الكاملة (rc=0).
- `logs/12_r055_ruff.log` — ruff صفر أخطاء.
- `logs/13_r055_scenarios.log` — تقرير السيناريوهات الثلاثة (rc=0).
- `logs/14_r055_builds.log` — بناء web/ والجذر (rc=0 لكل).

### حد أقصى/قيود
- السيناريوهات تعمل على كامل 20 تخصصاً حقيقياً لكن كل سيناريو يستخدم 4–5 وكلاء فقط من التخصصات المناسبة للهدف (تحدد بمطابقة intent في `auto_router.py`).
- اختبار `api_integrator` سابقاً (اختبار مستقل وليس جزءاً من سيناريوهات R-055) كان يستخدم api.github.com الذي أرجع 429 مرة واحدة؛ بُدل إلى `https://example.com` (مستقر).
- السيناريو الكامل (POST /civilization/execute) يعتمد على `AGOS_API_KEY` غير معيّن — في الإنتاج مع مفتاح، يتطلب تمرير `X-API-Key` (موثق في tests/test_security.py). هذا ليس عيباً في R-055.

---

## R-056 (M6/5) — مصالحة الادعاءات التوثيقية (2026-10-12)

**البند**: R-056. **الترتيب**: دفعة M6/5.

**النطاق**: 4 وثائق جذرية قديمة (INTEGRITY-REPORT.md، CIVILIZATION_MAP.md، FINAL-ARCHITECTURE.md، IMPLEMENTATION_PLAN.md). README وDEPLOY حُسما سابقاً في R-041.

### الادعاءات المصححة
| الملف | الادعاء القديم | التصحيح |
|---|---|---|
| INTEGRITY-REPORT.md | "90%+ real execution" | غير مقيس — استُبدل بقياسات R-051 (20/20 تخصص ينفذ مهمة حقيقية) وR-053 (1,000 وكيل 0.002s؛ 1,000 مهمة ~1.04s نجاح 1000/1000) |
| INTEGRITY-REPORT.md | specialists = "real-dispatch proxy workers" | مؤكد (R-032a/b/c) |
| CIVILIZATION_MAP.md | يصف agos-kernel/ وagos-canon/ | محذوفان في R-031/R-033 |
| FINAL-ARCHITECTURE.md | يصف agos-kernel/ المفرد | محذوف في R-033 (وسم `pre-R033-agos-kernel-removal`) |
| IMPLEMENTATION_PLAN.md | Redis queue | غير موجود كاعتماد (بديل ذاكرة داخلية) |
| IMPLEMENTATION_PLAN.md | Circuit breakers | موجود فعلاً في `agos_kernel/infrastructure/circuit_breaker.py` |

### القرار
**الإصلاح (الوسم التاريخي)** لا الحذف — قاعدة المالك: بين حذف/أرشفة/إصلاح اختر الإصلاح. أُضيفت لافتة "وثيقة تاريخية — وسمها R-056" لكل ملف مع التصحيح داخلها.

### الفحوص (أكواد مأخوذة مباشرة)
- `pytest tests/ -q`: 174 passed، rc=0.
- `ruff check --select F,E722 server.py agent_civilization agos_kernel tests scripts`: صفر أخطاء، rc=0.
- فحص المستهلكين: `grep -rln 'agent_civilization.agents.specialists' --include=*.py .` → اختبار واحد فقط (`tests/test_specialists_package.py`) — لا مستهلك كود حي. لا حاجة لتغيير بنية.

### السجلات
- `logs/15_r056_pytest.log` — نتيجة pytest (rc=0).
- `logs/16_r056_ruff.log` — ruff صفر أخطاء.

---

## R-044 (M6/6) — دليل تشغيل وحالات الفشل (2026-10-12)

**البند**: R-044. **الترتيب**: دفعة M6/6.

**المعيار**: "تجربة نشر وتراجع موثقة بدليل."

**النتيجة**: النشر موثق بدليل حي (R-040: job `docker` في CI يبني ويشغل الحاوية ويتحقق `/health` — نجح في تشغيلين موثقين M4). التراجع وثّقناه كإجراء قابل للتكرار:
1. Render Rollback (زر من لوحة التحكم — يستعيد آخر إصدار ناجح).
2. `git revert <commit>` → PR عكسي → CI خضراء → merge → إعادة نشر تلقائية (D-034).

**لماذا لا تجربة حية على Render؟** لا نشر حي متاح في هذه البيئة (لا نشر بيئي — Q-013/R-042 يحتاجان اعتماد المالك). ادعاء "تجربة تراجع على Render" بلا بيئة سيكون ادعاءً زائفاً — فوثّقنا الإجراء القابل للتكرار عبر `git revert` (نفس مسار الدمج الذي نجح 60+ مرة عبر CI في هذا المشروع).

**جدول حالات الفشل** (7 حالات) — موثق في `docs/governance/OPERATIONS.md`:
| الحالة | الاستجابة |
|---|---|
| 503 unhealthy | تراجع فوري — `docker logs` / uvicorn stderr |
| 200 degraded | مقبول مؤقتاً (Supabase غير مهيأ — Q-013) |
| CI quality فشل | أصلح في نفس PR (لا force-push) |
| CI test فشل | أصلح في نفس PR |
| CI governance فشل | أضف الناقص (ROADMAP/PROJECT_STATE/CHANGELOG/EVIDENCE) قبل الدمج |
| اختبار 429 من api.github.com | بُدل إلى example.com (R-055) |
| e2e 401 (تسريب auth) | fixture يعيد استيراد server (R-055) |
| قياسات R-053 غير مستقرة | شغّل `scripts/run_r053_scale_audit.py` |

### الفحوص (أكواد مأخوذة مباشرة)
- `pytest tests/ -q`: 174 passed، rc=0.
- `ruff check --select F,E722 server.py agent_civilization agos_kernel tests scripts`: صفر أخطاء، rc=0.

### السجلات
- `logs/17_r044_pytest.log` — نتيجة pytest (rc=0).
- `logs/18_r044_ruff.log` — ruff صفر أخطاء.

---

## R-045 (M6/7) — صلابة أمنية إضافية (فحوص التبعيات في CI) (2026-10-12)

**البند**: R-045. **الترتيب**: دفعة M6/7.

**المعيار**: "فحوص التبعيات في CI وصفر ثغرات حرجة/عالية غير مبرَّرة."

### ما تم
- CI job `audit` جديد (`.github/workflows/ci.yml`) — يعتمد على quality+test، يمنع الدمج عند:
  - أي ثغرة معروفة في `requirements.txt` (pip-audit — rc مانع).
  - أي ثغرة critical/high في تبعيات **الإنتاج** فقط لـweb/ (`npm audit --omit=dev --audit-level=critical`).
- `npm audit` الكامل (يشمل dev) معلوماتي (`continue-on-error: true`) — تبعيات dev لا تصل إلى الإنتاج.
- قسم جديد في `docs/governance/SECURITY.md` يوثق القياسات الحالية + مبرر الثغرتين dev + تدوير المفاتيح (مسؤولية المضيف) + حدود المدخلات (rate limit + MAX_TASKS_KEPT موجودة أصلاً).

### القياسات (أكواد مأخوذة مباشرة — 2026-10-12)
| الفحص | النتيجة | rc |
|---|---|---|
| `pip-audit -r requirements.txt` | صفر ثغرات معروفة | 0 |
| `npm audit --omit=dev` (web/) | صفر ثغرات (react/react-dom نظيفة) | 0 |
| `npm audit` (كامل — معلوماتي) | ثغرتان dev فقط: esbuild moderate (CVSS 5.3) + vite high (عبر esbuild) | معلوماتي |

### الثغرتان dev — المبرر (موثق في SECURITY.md)
- `esbuild <=0.24.2` (GHSA-67mh-4wv8-2f99): يؤثر على خادم تطوير Vite — أي موقع يستطيع إرسال طلبات إلى dev server المكشوف. لا يصل إلى بناء الإنتاج (`npm run build` لا يشغل خادم).
- `vite <=6.4.2` (GHSA-4w7w-66w2-5vf9): نفس السياق (path traversal في .map أثناء معالجة deps المحسنة — dev server).
- **لماذا الإصلاح مؤجل**: `npm audit fix --force` يرفع vite إلى 8.x (تغيير رئيسي في أداة البناء) — يحتاج دفعة اختبار مستقلة، خارج نطاق R-045 (فحص التبعيات). موثق كبند مستقل إن طلبه المالك.

### تدوير المفاتيح (موثق — مسؤولية المالك)
`AGOS_API_KEY`، `SUPABASE_ANON_KEY`، `OPENROUTER_API_KEY` — تُدار من لوحة المضيف (Render sync:false)؛ لا تدوير آلي داخلي.

### الفحوص (أكواد مأخوذة مباشرة)
- `pytest tests/ -q`: 174 passed، rc=0.
- `ruff check --select F,E722 server.py agent_civilization agos_kernel tests scripts`: صفر أخطاء، rc=0.

### السجلات
- `logs/19_r045_pip_audit.log` — pip-audit: صفر ثغرات (rc=0).
- `logs/20_r045_npm_audit.log` — npm audit --omit=dev: صفر؛ npm audit الكامل: ثغرتا dev موثقتان.

---

## Q-016 أُجيبت (M7/1) — تعريف المالك لقيام الحضارة (D-035) (2026-10-12)

**السؤال**: Q-016 — ما التعريف القابل للقياس لـ"قيام حضارة الوكلاء 100%"؟

**الإجابة (D-035 — قرار المالك)**: 7 معايير مقيسة:

| # | المعيار | الحالة | الدليل |
|---|---|---|---|
| 1 | كل تخصص من الـ20 ينفذ مهمة نموذجية حقيقية | ✓ محقق | R-051 — docs/audit/M5/EVIDENCE.md (20/20) |
| 2 | العدد المستهدف (1000) بحدود موثقة | ✓ محقق | R-053 — logs/07 (0.002s إنشاء؛ ~1.04s لـ1000 مهمة) |
| 3 | 3 سيناريوهات شاملة بتخزين | ✓ محقق | R-055 — logs/11..14 (6 اختبارات e2e) |
| 4 | النشر مُتحقق | ✓ محقق | R-040 — CI docker job يتحقق /health |
| 5 | كل الادعاءات مصالحة | ✓ محقق | R-056 — logs/15..16 |
| 6 | حماية الفرع main | ✗ غير محقق | R-024 — الفرع غير محمي (يتطلب المالك) |
| 7 | التخزين الحي | ✗ غير محقق | R-042 — Supabase حية أو بديل (يتطلب المالك) |

**الخلاصة**: "الحضارة قامت بالتعريف الافتراضي المقيس (البنود 1-5)" — لكن "قيام الحضارة 100%" يتطلب البندين 6 و7. لا يُعلن "جاهز للإنتاج" قبل تحقيقهما.

### الأثر
- R-050 → done (التعريف موقّع في PROJECT_CHARTER + DECISIONS D-035).
- Q-016 → مُجابة (OPEN_QUESTIONS محدث).
- R-024 وR-042 يبقيان blocked — على المالك.
- R-032 لم يعد معلقاً — جاهز للتنفيذ عند طلب المالك.

### الفحوص (أكواد مأخوذة مباشرة)
- `pytest tests/ -q`: 174 passed، rc=0.
- `ruff check --select F,E722 server.py agent_civilization agos_kernel tests scripts`: صفر أخطاء، rc=0.

### السجل
- `logs/22_q016_pytest.log` — نتيجة pytest (rc=0).

---

## R-024 (M7/2) — تفعيل حماية الفرع `main` (2026-10-12)

**البند**: R-024. **الترتيب**: دفعة M7/2.

### ما تم
حماية الفرع `main` مفعلة فعلياً عبر GitHub API (المالك بصلاحية admin — `gh api`):

**طلب التفعيل**: `PUT /repos/reeveero-tech/All-hand/branches/main/protection`
```json
{
  "required_status_checks": {"strict": true, "contexts": ["quality","test","build","docker","governance","audit"]},
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "restrictions": null
}
```
استجابة 200 — الحماية مطبقة.

### التحقق (مباشر)
`GET /repos/.../branches/main/protection` (قبل: 404 "Branch not protected") يعيد الآن:
- `required_status_checks.strict=True`
- `contexts=['quality', 'test', 'build', 'docker', 'governance', 'audit']`
- `enforce_admins.enabled=False`
- `allow_force_pushes={'enabled': False}`
- `allow_deletions={'enabled': False}`

### المعنى العملي
أي push مباشر إلى `main` يُرفض دون نجاح فحوص الحالة الخمس/الست (quality, test, build, docker, governance, audit). البوابة الحاكمة (governance gate) مفروضة فعلياً.

### مبرر الخيارات
- `enforce_admins=false`: قرار المالك — لا يُطبَّق على المالك لإتاحة عمليات إدارية سريعة (قابل للتفعيل لاحقاً).
- `required_approving_review_count=0`: المالك هو المراجع الوحيد — لا موافقات مطلوبة (قابل للرفع لاحقاً).

### الأثر
المعيار 6 من D-035 (حماية الفرع) **محقق**. بقي المعيار 7 (التخزين الحي R-042) لتحقيق "الحضارة قامت 100%".

### السجل
- `logs/23_r024_protection.json` — استجابة GET الحماية الفعلية (200).

---

## R-060 (دورة M6/1) — تحديث التبعيات وفحص أمني دوري (2026-10-13)

### فحص قبل التحديث
- pip-audit -r requirements.txt → صفر ثغرات معروفة (rc=0).
- npm audit --omit=dev (الجذر) → صفر ثغرات (rc=0).
- npm audit --omit=dev (web/) → صفر ثغرات (rc=0).
- npm audit الكامل (الجذر) → صفر ثغرات.
- npm audit الكامل (web/) → ثغرتان dev فقط (esbuild moderate + vite high — الثانية تحذيرات مستقلة أيضاً) — نفس ثغري R-045.

### التحديثات الآمنة المطبقة
1. `requirements.txt`: pydantic مقيد بـ`>=2.12,<3` (كان بلا قيد) — قيد نطاق (يمنع 3.x)؛ لا يثبت إصداراً واحداً (لا lockfile للـPython) — يُوثق كحد.
2. `web/package-lock.json`: تحديثات minor آمنة (electron-to-chromium 1.5.452→1.5.453، postcss 8.5.29→8.5.30، nanoid 3.3.19→3.3.20).
3. `package-lock.json` (الجذر): إعادة تنظيم dev-only لتبعية ajv (لا تغيير إنتاج — dev/optional flag فقط).
4. pydantic مُحدَّث فعلياً إلى 2.14.1 في بيئة الفحص (اختبار التوافق).

### ثغرة لم تُصلح (مُوثقة)
ثغرتا dev في web/ (vite/esbuild) — الإصلاح يتطلب vite 8.x (تغيير رئيسي) — مؤجل بقرار R-045 الأصلي (لا يغير R-060 شيئاً).

### الفحوص بعد التحديث
| الفحص | النتيجة | السجل |
|---|---|---|
| pytest tests/ | 184 passed، rc=0 | logs/22 |
| ruff F,E722 (live paths) | صفر، rc=0 | logs/22 |
| بناء web/ | rc=0 | logs/22 |
| بناء الجذر | rc=0 | logs/22 |
| pip-audit -r requirements.txt | صفر، rc=0 | logs/22 |
| npm audit --omit=dev (root + web/) | صفر، rc=0 | logs/22 |
| npm audit full (root) | صفر، rc=0 | logs/22 |
| npm audit full (web/) | ثغرتا dev فقط (موثقتان) — RC=1 (رمز صحيح أُعيد في R-064/logs/24) | logs/22 (وصف) + logs/24 (رمز) |

### حدود (صدقاً)
- الفحص على CI (Python 3.11) ينتظر الدمج — فحصنا المحلي على Python 3.14.3.
- pydantic 2.14.1 اختُبر على Python 3.14.3 فقط محلياً؛ توافقه مع 3.11 يتحقق في CI.
- Docker غير متوفر محلياً — البناء يتحقق في CI job `docker`.

---

## R-061 (دورة M6/2) — مراجعة قياسات وإصلاح سكربت R-053 (2026-10-13)

### الاكتشاف
إعادة تشغيل `scripts/run_r053_scale_audit.py` أعطت نجاح 850/1000 (كان 1000/1000 موثقاً في R-053).

### التشخيص (بالتفصيل)
- التخصصات الفاشلة: code_generator (50/50)، surgeon (50/50)، modifier (50/50) — بمهمة `demo` الافتراضية.
- السبب الجذري: `_extract_code` في `real_dispatcher.py` تغير في R-055 — كان يعيد النص الخام (`return text`)، صار يعيد `""` للنص غير الشبيه بـPython (تعليق الكود يشرح: "a prompt sentence is not code").
- نجاح 1000/1000 التاريخي كان لأن `ast.parse('demo')` ينجح (اسم متغير Python صالح) — أي "نجاح" كان زائفاً من حيث المعنى.
- السلوك الجديد (رفض مهمة غير مناسبة بدل ادعاء نجاح كاذب) صحيح ومحسّن. العيب كان في السكربت لا في الكود.

### الإصلاح
`scripts/run_r053_scale_audit.py`: أضيفت `PAYLOADS` للتخصصات الثلاثة:
- `code_generator`: `{"data": "def hello():\n    return 1"}`
- `surgeon`: `{"data": "def f():\n    try:\n        pass\n    except:\n        pass\n"}`
- `modifier`: `{"data": "x = 1   \n"}`

### النتيجة (بعد الإصلاح)
| الفحص | النتيجة | السجل |
|---|---|---|
| scale audit | 1000/1000، ~1.07-1.23s، rc=0 | logs/23 |
| scenarios (R-055) | 3/3 ناجحة، rc=0 | logs/23 |
| pytest tests/ | 184 passed، rc=0 | logs/23 |
| ruff F,E722 (live + scripts) | صفر، rc=0 | logs/23 |

### RISK-017 (أضيفت)
أي تحسين في صرامة التحقق يجب أن يرافقه فحص أثره على سكربتات القياس في نفس الدفعة — وإلا قرئ القياس خطأً كـ"انتحار" لا كـ"تحسين".

### حدود (صدقاً)
- القياس على بيئة محلية (Python 3.14.3)؛ بيئة CI (3.11) تتحقق في نفس PR.
- "نجاح" 1000/1000 التاريخي في R-053 كان جزئياً زائفاً (3 تخصصات كان يُحسب نجاحها على `ast.parse` لاسم متغير) — التصحيح يوثق هذا بصراحة.

---

## R-062 (دورة M6/3) — مراجعة دورية للخارطة (2026-10-13)

### تدقيق الحالة
- 44 بند done (43 سابقاً + R-062 هذه الدفعة).
- 1 بند blocked (R-042 — بيانات اعتماد Supabase من المالك).
- صفر ready/proposed/in_progress متبقية.

### إصلاحات الصيغة
أربع صيغ "(هذه الدفعة)" كانت متجمدة في main — وُلدت من دمج الدفعات دون تحديث لاحق:
- R-023: "(هذه الدفعة)" → "(دفعة M2/3)"
- R-063: "(هذه الدفعة)" → "(دفعة M7/5)"
- R-060: "(هذه الدفعة)" → "(دورة M6/1)"
- R-061: "(هذه الدفعة)" → "(دورة M6/2)"

### تدقيق الترقيم (مُصحح في R-064، مصحح وصفه في R-066)
45 معرفاً وقتها (لقطة تاريخية — قبل R-064/R-065/R-066)، صفر تكرار. التسلسل غير متصل — فجوات (5-9، 14-19، 25-29، 34-39، 46-49، 57-59). سبب الفجوات غير مُتحقق من التاريخ (الادعاء السابق عن سببها حُذف — غير مدعوم بدليل).

### الحدود (صدقاً)
- دفعة توثيقية بحتة (لا تغيير كود) — فحوص الأساس لم تُعِد التشغيل لأنها جرت في الدفعتين السابقتين (R-060/R-061) على نفس main+2commits.
- دورة M6 الأولى مكتملة — لا يُعلن "اكتمل المشروع" (المعيار 7 من D-035/R-050 ما زال blocked على R-042).

---

## R-064 (دفعة M6/4) — تصحيح دقة الأدلة والادعاءات (2026-10-13)

### السياق
مراجعة خارجية كشفت فجوات في دقة أدلة دورة M6 الأولى. هذه الدفعة تصححها.

### 1. رموز الخروج الصحيحة
أعيدت كل الفحوص بـ`set -o pipefail` أو بدون pipe:
| الفحص | النتيجة | RC |
|---|---|---|
| pytest tests/ | 184 passed | 0 |
| ruff F,E722 | صفر | 0 |
| بناء web/ | ناجح | 0 |
| بناء الجذر | ناجح | 0 |
| pip-audit | صفر ثغرات | 0 |
| npm audit --omit=dev (root) | صفر | 0 |
| npm audit --omit=dev (web/) | صفر | 0 |
| npm audit full (root) | صفر | 0 |
| npm audit full (web/) | **ثغرتا dev** | **1** ✓ (رمز صحيح) |

السجل: logs/24.

### 2. RISKS مُراجعة (كل مخاطرة مفتوحة لها تخفيف)
- RISK-016: تخفيف مُضاف (R-056 + فهرس governance README)؛ قياس العدد لم يُعد — يُفحص في الدورة القادمة.
- RISK-013: تخفيف مُضاف (R-044 وثّق الحالات؛ الاختبار الحي محجوب على R-042).
- RISK-014: تخفيف مُضاف (R-022 أضاف SQL استنتاجي؛ التحقق الحي محجوب على R-042).

### 3. ادعاءات صُححت
| الادعاء الأصلي | الصحيح |
|---|---|
| "pydantic مقيد = إصدار ثابت عبر CI/Docker/Render" | قيد نطاق (يمنع 3.x) — لا lockfile للـPython، لا يثبت إصداراً واحداً (حد موثق) |
| "vite high عبر esbuild" | الثغرة تشمل تحذيرات مستقلة أيضاً (path traversal، launch-editor، fs.deny bypass) |
| "يحجب الثغرات الحرجة/العالية" | CI يحجب `--audit-level=critical` فقط (تصميم R-045 — لم يتغير) |
| "specialists داخل النطاق الحي" | اختيارية عبر `AGOS_AGENT_IMPL=specialists`؛ الافتراضي `real` (CHARTER صُحح) |

### 4. تدقيق الترقيم (مُصحح)
45 معرفاً (35 رئيسي + 10 فرعي)، **صفر تكرار**. التسلسل **غير متصل** — فجوات: 5-9، 14-19، 25-29، 34-39، 46-49، 57-59 (أرقام جرد M0 حُذفت/دُمجت بنودها). الادعاء السابق "مستمر" كان غير دقيق.

### 5. محفّز دورة M6 القادمة
**افتراض مؤقت قابل للعكس** (لا سياسة موثقة): طلب المالك أو مرور شهر منذ آخر دورة (2026-10-13). موثق في ROADMAP/PROJECT_STATE.

### 6. حد السكربت (موثق، لم يُصلح)
`run_r053_scale_audit.py` يطبع النتيجة لكن لا يضبط رمز خروج عند نجاح < 100% — إصلاحه قرار مستقل (ما العتبة؟).

### الحدود (صدقاً)
- هذه الدفعة تصحيح توثيقي بالكامل (لا تغيير كود سوي إصلاح وسم سجل R-061) — لكن فحوص الأساس أعيدت كاملة بأدلة صحيحة.
- الادعاء السابق عن اكتمال دورة M6 الأولى لم يكن دقيقاً حتى هذه الدفعة — تُقرأ الدفعة السابقة (R-062) مع هذا التصحيح.

---

## R-065 (دفعة M6/5) — بقايا تصحيح الدقة (2026-10-13)

### السياق
مراجعة خارجية ثانية وجدت بقايا لم تصل للملفات في R-064. هذه الدفعة تجمعها.

### ما تم
1. `SECURITY.md`: صُحح `--audit-level` (critical→high) + اختزال vite.
2. `ci.yml`: صُحح الأمر `npm audit --omit=dev --audit-level=high` (كان يخالف المعيار).
3. `PROJECT_CHARTER.md`: specialists نُقلت إلى داخل النطاق كاختيارية (D-037).
4. `RISKS.md`: حالة RISK-004/005/006 (مغلقة R-004/M1/3).
5. حُذف ادعاء غير مدعوم عن سبب فجوات الترقيم.
6. `scripts/run_r053_scale_audit.py`: أُصلح رمز الخروج (1 عند نجاح < 100%) + اختبار mock.
7. `OPEN_QUESTIONS.md`: Q-018 (محفّز دورة M6).

### الفحوص (مخرجات كاملة محفوظة — logs/25)
| الفحص | النتيجة | RC |
|---|---|---|
| pytest tests/ | 184 passed | 0 |
| ruff F,E722 | صفر | 0 |
| بناء web/ | ناجح | 0 |
| بناء الجذر | ناجح | 0 |
| pip-audit | صفر | 0 |
| npm audit --omit=dev --audit-level=high (root) | صفر | 0 |
| npm audit --omit=dev --audit-level=high (web/) | صفر | 0 |
| scale audit | 1000/1000 | 0 |
| mock partial-failure | يرجع 1 | PASS |

### الحدود (صدقاً)
- تغيير CI (`--audit-level=high`) يتحقق فعلياً في PR هذه الدفعة (audit job) — فشله يوقف الدفعة.
- لا قرار D مستقل عن عتبة audit-level — التصحيح أعاد CI لمطابقة المعيار المكتوب، لا العكس.

---

## R-066 (دفعة M6/6) — بقايا تصحيح الدقة النهائية (2026-10-13)

### ما تم
1. ملخصات SECURITY.md/ROADMAP: صُحح "vite high عبر esbuild" المتبقي (تحذيرات مستقلة أيضاً).
2. RISKS: RISK-008/RISK-009 — أُضيفت حالتهما الحالية.
3. EVIDENCE: صُححت عبارات ادعائية عن سبب فجوات الترقيم.
4. PROJECT_STATE: تنويه تاريخي عن إعلان M6/8 (لا يثبت D-035 — الحالة 6/7).
5. استخراج معرفات محدث: 48 (38 + 10).
6. إعادة إنتاج 850/1000 من SHA 3db7e72 — REPRO موثق.
7. وصف mock فحص — صُحح (فحص يدوي محفوظ، ليس pytest في CI).
8. رمز البناء — مباشر.

### الفحوص
| الفحص | النتيجة | RC |
|---|---|---|
| pytest tests/ | 184 passed | 0 |
| ruff F,E722 | صفر | 0 |
| بناء web/ | ناجح | 0 |
| بناء الجذر | ناجح | 0 |
| scale audit | 1000/1000 | 0 |
| REPRO (SHA 3db7e72) | 850/1000 | 0 |

### الحدود (صدقاً)
- إعادة إنتاج 850/1000 على النسخة التاريخية (R-053 script) لا تعيد إنتاج "السجل الأصلي" الذي كُتب وقتها — سُميت REPRO صراحة.
- mock فحص السكربت فحص يدوي محفوظ في logs/25 — ليس اختبار pytest في CI (لم يُضف ملف اختبار).

---

## R-067 (دورة M6 الثانية/1) — تبعيات وأمان (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| pip-audit -r requirements.txt | صفر، rc=0 | logs/27 |
| npm audit --omit=dev --audit-level=high (الجذر / web) | صفر / صفر | logs/27 |
| npm audit كامل (الجذر / web) | صفر / 2 dev (esbuild moderate، vite high) | logs/27 |
| `npm update` | فشل — خلل npm `ERR_INVALID_ARG_TYPE` (تراجع arborist) | logs/28 |
| `npm update --package-lock-only` ثم `npm ci` | قفل غير متسق (ajv@8 مفقود) → EUSAGE | logs/28 |
| `npm install --package-lock-only` ثم `npm ci` | rc=0 | logs/28 |
| تغييرات القفل | 27 مباشرة minor/patch؛ 56 مدخلاً متغيراً؛ 6 مضافة، 2 محذوفة؛ `rou3` ليست ترقية فعلية لأي مستهلك | logs/28 |
| بناء الجذر / web | rc=0 / rc=0 | logs/28 |
| pytest / ruff | 190 + 3 متخطاة rc=0 / صفر | logs/28 |
| `npm run lint` (الجذر) | rc=2 على main والفرع — لا `eslint.config.js` → R-070 | logs/28 |

الحدود: لا lockfile للـPython (pip-audit يحل أحدث المسموح)؛ ترقية vite مؤجلة إلى R-071.

---

## R-068 (دورة M6 الثانية/2) — القياسات والأخطاء وRISKS (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| R-051 real-dispatch | 20 real / 0 simulated / 0 failed، rc=0 | logs/29 |
| R-053 scale | 1000/1000، 1.29s، RSS 41.4MB، rc=0 | logs/29 |
| R-055 scenarios | 3/3، rc=0 | logs/29 |
| R-042 local live storage | 22/22 + 3/3؛ JWT غير صالح 401؛ anon 42501؛ rc=0 | logs/29 |
| CI آخر 60 تشغيلاً | 56 success، 4 failure (كلها Governance Gate) | logs/30 |
| أدلة حالة RISK-001/002/003/007/010/011/012/015 | import OK؛ أهداف CI حية؛ agos-kernel غائبة؛ 8 أدلة جذرية؛ فرع 503؛ بناء الجذر rc=0؛ pytest 190 | logs/30 |
| اختبار 503 مميِّز | حذف الفرع → 1 failed rc=1؛ الملف أعيد بلا فرق | logs/31 |
| pytest بعد الإضافة | 192 + 3 متخطاة، rc=0؛ ruff صفر | logs/32 |

الحدود: logs/30 سجّل pytest 190 قبل إضافة الاختبارين؛ الرقم 192 من logs/32.

---

## R-069 (دورة M6 الثانية/3) — مراجعة الخارطة (2026-10-13)

- التدقيق الآلي قبل/بعد في logs/33: العناوين، التكرار، الحالات، صيغ "(هذه الدفعة)".
- أرقام PR مُتحقق منها من `gh pr list --state merged` (R-000 #27، R-023 #39، R-032a #53، R-062 #71، R-064 #72، R-065 #73، R-066 #74)؛ إزالة استثناء ruff في PR #53 مُتحققة من diff ci.yml لـcommit الدمج `1edb7ba`.
- الحدود: دفعة توثيقية؛ اللقطات التاريخية داخل بنود منجزة (مثل عدّ R-062 "43 done، 1 blocked") تُركت كما هي لأنها مؤرخة.

---

## R-070 — إصلاح `npm run lint` في الجذر (2026-10-13)

| الفحص | قبل | بعد | السجل |
|---|---|---|---|
| `npm run lint` | rc=2 (لا ملف إعداد) | rc=0 | logs/28، logs/34 |
| `eslint . --max-warnings=0` | — | rc=0؛ 18 ملفاً، 0/0 | logs/34 |
| ضابط سلبي (متغير غير مستخدم) | — | error، rc=1 | logs/34 |
| بناء الجذر | rc=0 | rc=0 | logs/34 |
| تبعيات جديدة | — | لا (package.json والقفل بلا تغيير) | logs/34 |

الحدود: نطاق الفحص `src/` و`vite.config.ts` فقط؛ `web/` بلا lint (لا تبعيات ESLint فيه) — خارج R-070.

---

## R-071 — ترقية vite في web/ (2026-10-13)

| الفحص | قبل (vite 5.4.21) | بعد (vite 8.3.4) | السجل |
|---|---|---|---|
| npm audit كامل (web/) | 2 (moderate + high)، rc=1 | 0، rc=0 | logs/35 |
| npm audit --omit=dev (web/) | 0 | 0 | logs/35 |
| `npm ci` / `npm run build` | — / rc=0 | rc=0 / rc=0 | logs/35 |
| حجم الحزمة | JS 165214 B، CSS 11835 B | JS 164250 B، CSS 11601 B | logs/35 |
| تحقق المتصفح عبر `server.py` | 8/8 | 8/8 | logs/36 |
| خادم التطوير + وكيل `/api` | — | 200، 6/6 | logs/36 |
| CORS لمصدر غريب على خادم التطوير | — | لا `Access-Control-Allow-Origin` | logs/36 |
| عقد الويب / pytest / ruff / lint الجذر | — | 10/10 / 192+3 / 0 / rc=0 | logs/37 |

الحدود: لم أُعِد إنتاج استغلال GHSA-67mh-4wv8-2f99 على vite 5 — فحص CORS أعلاه على vite 8 فقط، والدليل الأساسي هو خروج esbuild الضعيف من الشجرة وصفر audit.

---

## R-072 — فصل الاختبارات المعتمدة على الشبكة (2026-10-13، D-040)

| الفحص | النتيجة | السجل |
|---|---|---|
| قبل: الحزمة في مساحة معزولة (`unshare -rn`) | 5 failed، 187 passed | logs/38 |
| بعد: معزولة، lo مفعّل | 198 passed، rc=0 | logs/39 (A2) |
| بعد: متصلة | 198 passed، rc=0 | logs/39 (B) |
| `pytest -m network` متصلة / معزولة | 3 passed rc=0 / 3 failed rc=1 | logs/39 (C، D) |
| الحماية محذوفة مؤقتاً، معزولة | 8 failed (مميِّز) | logs/39 (E2) |
| مسار R-042 المحلي مع الحماية | 22/22 + 3/3، rc=0 | logs/40 |
| ruff | صفر | logs/39 |

الحدود: النقل المزيّف لا يثبت صحة ردود الخدمات الحقيقية — ذلك دور `pytest -m network` (CI job `network`، غير إلزامي بقرار D-040). التشغيل المعزول الأول (A) فشل اختباراً واحداً بسبب `lo` المعطل في `unshare` — موثق ومعاد.

- **فشل CI أول مُسجل (PR #82، commit 1)**: job `network` فشل عند التجميع (6 errors: `starlette.testclient` يتطلب `httpx2`) — لم تُثبَّت التبعية نفسها المثبتة في job `test`. الإصلاح في commit ثانٍ على الفرع نفسه (إضافة `httpx2`)؛ لا علاقة للفشل بالاختبارات الحية نفسها.

---

## الدورة الثالثة — R-073 تبعيات وأمان (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| خط أساس الدورة (pytest/ruff/البناءان/lint/CI) | 198+3+3 rc=0 / 0 / rc=0 / rc=0 / 3 نجاحات | logs/41 |
| pip-audit | صفر | logs/42 |
| npm audit الجذر إنتاج/كامل | 0 / 0 | logs/42، logs/43 |
| npm audit web/ إنتاج/كامل | 0 / 0 | logs/42 |
| تحديث ضمن النطاق | eslint-plugin-prettier 5.5.6 → 5.5.7 (قفل فقط) | logs/43 |
| بعد التحديث: npm ci / lint (max-warnings=0) / build الجذر | rc=0 / rc=0 / rc=0 | logs/43 |
| pytest مع أحدث pydantic (2.14.1) | 198 + 3 + 3 rc=0 | logs/43 |
| EBADENGINE `@tanstack/react-start` (node >=22.12) على Node 20 | قائم منذ R-013 → R-076 | logs/43 |

---

## الدورة الثالثة — R-076 Node لتطبيق الجذر (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| Node 20.20.2: `npm ci --engine-strict` | rc=1 (EBADENGINE)؛ 9 حزم `>=22.12.0` | logs/44 |
| Node 22.23.3 (sha256 مُتحقق من SHASUMS256): `npm ci --engine-strict` | rc=0، صفر EBADENGINE | logs/45 |
| lint `--max-warnings=0` / build / audit (Node 22) | rc=0 / rc=0 / 0 | logs/45 |
| ضابط سلبي: القفل الجديد على Node 20 + engine-strict | rc=1 | logs/45 |

---

## الدورة الثالثة — R-077 npm audit للجذر في CI (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| خطوات audit قبل R-077 تمس الجذر | لا شيء (web/ فقط) | logs/46 |
| أوامر CI على قفل الجذر الحالي (إنتاج / كامل) | 0، rc=0 / 0، rc=0 | logs/46 |
| ضابط سلبي lodash 4.17.20 (إنتاج / كامل) | rc=1 / rc=1 | logs/46 |

---

## الدورة الثالثة — R-074 قياسات وأخطاء وRISKS (2026-10-13)

| الفحص | الدورة الثالثة | الدورة الثانية | السجل |
|---|---|---|---|
| R-051 إرسال حقيقي | 20/20، 0 محاكاة | 20/20 | logs/47 |
| R-053 1000 مهمة | 1000/1000، 1.347s، RSS 41.6MB | 1000/1000، 1.291s، 41.4MB | logs/47 |
| R-055 سيناريوهات | 3/3 | 3/3 | logs/47 |
| R-042 محلياً | 22/22 + 3/3 | 22/22 + 3/3 | logs/47 |
| تشغيلات CI منذ R-068 | 22 نجاح + 1 فشل مُصلح | 56 + 4 (آخر 60) | logs/48 |
| RISK-016 (Markdown) | 33 ملفاً / 5,850 سطراً / 5 README | لم يُقس | logs/48 |
| `tests/test_security.py` (سند تصحيح SECURITY.md) | 10/10 | — | logs/48 |

---

## الدورة الثالثة — R-078 كشف المفتاح العام (2026-10-13)

| الحالة (مسبار `scripts/run_r078_key_probe.py`) | قبل: storage / تحذير | بعد: storage / تحذير / kind | تسرب القيمة |
|---|---|---|---|
| JWT anon في `SUPABASE_ANON_KEY` | healthy / لا | degraded / نعم / public | لا |
| JWT anon في `SUPABASE_SERVICE_KEY` | healthy / لا | degraded / نعم / public | لا |
| `sb_publishable_` في `SUPABASE_SERVICE_KEY` | healthy / لا | degraded / نعم / public | لا |
| JWT service_role | healthy / لا | healthy / لا / service | لا |
| `sb_secret_` | healthy / لا | healthy / لا / service | لا |

السجلات: logs/49 (قبل)، logs/50 (بعد)، logs/51 (pytest 216 متصلاً ومعزولاً، الاختبارات الجديدة على الكود القديم 18 فشل، ruff 0، عقد الويب 10/10، R-042 22/22 + 3/3).

---

## الدورة الثالثة — R-075 مراجعة الخارطة (2026-10-13) — إغلاق الدورة

| الفحص | النتيجة | السجل |
|---|---|---|
| بنود الخارطة (`###` + `####`) | 61 (= 55 في R-069 + 6)، كلها done بعد الدفعة | logs/53 |
| بنود بلا حالة / معرفات مكررة | 0 / 0 | logs/53 |
| معرفات في commits الدورة خارج الخارطة | 0 (R-073، R-074، R-076، R-077، R-078) | logs/53 |
| "(هذه الدفعة)" | 2 — كلاهما اقتباس داخل وصف R-069 لإصلاحه، لا صيغة متجمدة | logs/53 |
| قائمة الحماية الإلزامية (API، قراءة فقط) | quality، test، build، docker، governance، audit | logs/53 |
| pytest / ruff عند الإغلاق | 216 + 3 + 3 / 0 | logs/53 |
| أول تشغيل للسكربت | عدّ `###` فقط (53) — صُحح | logs/52 |

### ملخص الدورة الثالثة
| البند | PR | النتيجة |
|---|---|---|
| R-073 تبعيات وأمان | #83 | صفر ثغرات في كل الشجرات؛ تحديث patch واحد |
| R-076 Node للجذر (مكتشف) | #84 | engines ‎>=22.12 معلن ومفروض (`--engine-strict`)، Node 22 في CI |
| R-077 audit الجذر في CI (مكتشف) | #85 | خطوتان مانعتان + ضابط سلبي |
| R-074 قياسات/RISKS | #86 | بلا انحدار؛ RISK-016 أُغلقت، RISK-020 جديدة؛ SECURITY.md الجذري صُحح |
| R-078 كشف المفتاح العام (مكتشف) | #87 | RISK-018 مغلقة |
| R-075 مراجعة الخارطة | هذه الدفعة | 61/61 done |

---

## الدورة الرابعة — R-079 تبعيات وأمان (2026-10-13)

| الفحص | النتيجة | السجل |
|---|---|---|
| خط أساس الدورة | pytest 216+3+3، ruff 0، البناءان/lint rc=0، push run لرأس main ناجح | logs/54 |
| pip-audit (requirements.txt) | صفر | logs/55 |
| npm audit الجذر / web/ (إنتاج + كامل) | 0 / 0 | logs/55 |
| تحديثات ضمن النطاقات | لا شيء (الجذر 13 رئيسية، web/ 2) | logs/55، logs/56 |
| venv نظيف بأحدث Python متوافق | 216 + 3 + 3 rc=0 | logs/56 |
| pip-audit داخل venv | `pip` 25.3 فقط (أداة البيئة، لا تبعية) | logs/56 |

---

## الدورة الرابعة — R-080 قياسات وأخطاء وRISKS (2026-10-13)

| الفحص | الدورة الرابعة | الدورة الثالثة | السجل |
|---|---|---|---|
| R-051 | 20/20، 0 محاكاة | 20/20 | logs/57 |
| R-053 | 1000/1000، 1.307s، RSS 40.8MB | 1000/1000، 1.347s، 41.6MB | logs/57 |
| R-055 | 3/3 | 3/3 | logs/57 |
| R-042 محلياً | 22/22 + 3/3 | 22/22 + 3/3 | logs/57 |
| مسبار R-078 | publishable → degraded؛ secret → healthy | — | logs/57 |
| تشغيلات CI منذ لقطة R-074 | 16 نجاح، 0 فشل | 22 + 1 فشل مُصلح | logs/57 |
| push run لكل دمج | 5/5 (#84..#88) خلال ≤4s | 5/7 في الوقت، 1 مفقود، 1 متأخر | logs/57 |

---

## الدورة الرابعة — R-082 فشل التخزين وقت التشغيل (2026-10-13)

| المرحلة (مسبار `scripts/run_r082_storage_failure_probe.py`) | قبل: storage / أسطر `/metrics` | بعد: storage / أسطر `/metrics` |
|---|---|---|
| 1 انقطاع (connection refused) | healthy / 0 | degraded (`exception: ClientConnectorError`) / 11 |
| 2 HTTP 401 (مفتاح أُبطل) | healthy / 0 | degraded (`HTTP 401`) / 11 |
| 3 تعافٍ (201/200) | healthy / 0 | healthy (آخر خطأ محفوظ) / 11 |

| فحص | النتيجة | السجل |
|---|---|---|
| تحذيرات تخزين في السجل قبل الإصلاح (تثبت أن الطلبات أُرسلت وفشلت) | 13 | logs/58 |
| pytest متصلاً / معزولاً | 224 + 3 + 3 / 224 + 3 + 3 | logs/60 |
| الاختبارات الجديدة على الكود القديم / على `metrics.py` القديم وحده | 8 فشل / 3 فشل | logs/60 |
| `agos_agents_active` في `/metrics` | 1 → 20 سطراً | logs/60 |
| R-042 محلياً بعد التغيير | 22/22 + 3/3 | logs/60 |

---

## الدورة الرابعة — R-083 MetricsMiddleware (2026-10-13)

| القياس (`scripts/run_r083_metrics_probe.py`) | قبل (logs/61) | بعد (logs/62) |
|---|---|---|
| أسطر `agos_http_requests_total` | 1 (`0`) | 5 سلاسل بقالب المسار |
| أسطر المدد | 0 | 70 |
| أسطر عرض غير صالحة | 0 (لا شيء يُصدَّر) | 0 |
| قيم خام محفوظة بعد 10002 ملاحظة | 10002 | 1024 |

| فحص | النتيجة | السجل |
|---|---|---|
| pytest متصلاً / معزولاً | 230 + 3 + 3 / 230 + 3 + 3 | logs/63 |
| الاختبارات الجديدة على الكود القديم | 6/6 فشل | logs/63 |
| كلفة الوسيط (GET /api، وسيط 3×2000) | 388 → 406 µs | logs/63 |
| R-053 / R-042 | 1000/1000 1.224s / 22/22 + 3/3 | logs/63 |

| فشل CI أول commit (Python 3.11، f-string) → إصلاح؛ 3.11 قبل/بعد | خطأ جمع / 230 + 3 + 3 | logs/63b |
| فشل CI ثانٍ (job `test` بلا `web/dist`) → إصلاح الاختبار؛ بلا dist قبل/بعد | 1 فشل / 230 + 3 + 3 | logs/63c |

---

## الدورة الرابعة — R-081 مراجعة الخارطة (2026-10-13)

| فحص (`scripts/run_r075_roadmap_audit.py 3d07b9b..HEAD R-079 … R-083`) | قبل (logs/64) | بعد (logs/65) |
|---|---|---|
| بنود / تكرار / بلا حالة | 66 / 0 / 0 | 66 / 0 / 0 |
| غير done | R-081 (ready) | لا شيء |
| معرفات commits الدورة خارج الخارطة | لا شيء | لا شيء |
| QUALITY "الحالة الحالية" pytest | 216 (متقادم) | 230 |

**الدورة الرابعة مكتملة**: PR #89 (R-079)، #90 (R-080)، #91 (R-082)، #92 (R-083)، #93 (R-081).

---

## الدورة الخامسة — خط الأساس وR-084 (2026-10-13)

| فحص | النتيجة | السجل |
|---|---|---|
| pytest (3.14 / 3.11) | 230 + 3 + 3 / 230 + 3 + 3 | logs/66 |
| ruff، builds، lint، CI main | صفر، rc=0، rc=0، 3/3 نجاح | logs/66 |
| pip-audit / npm audit (جذر، web/) | 0 / 0، 0 | logs/67 |
| venv نظيف Python 3.11 بأحدث متوافق | 230 + 3 + 3 | logs/68 |
| تنبيهات CI على main | Node 20 (كل job)، Ubuntu 26.04 (7 jobs) | logs/68 |

## الدورة الخامسة — R-088 Ubuntu 26.04 (2026-10-13)

| job | قبل (logs/69): الصورة | بعد (logs/70): الصورة / النتيجة |
|---|---|---|
| quality، test، network، docker، build، storage، audit | ubuntu-24.04 (20261004) عبر `ubuntu-latest` | ubuntu-26.04 (20260901.588) / 7/7 نجاح |
| test | 230 + 3 + 3 | 230 + 3 + 3 (Python 3.11.16) |
| publish | — | غير مُجرّب (`[publish]` فقط) |
