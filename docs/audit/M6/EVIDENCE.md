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
