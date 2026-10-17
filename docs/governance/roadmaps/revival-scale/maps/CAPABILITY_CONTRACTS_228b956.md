# مصفوفة عقود القدرات وواجهات التوافق — R-157 (D-069)

- **الأساس**: `main` عند `228b9560f74f8ef9035a57ce1e460362ac9d3a2c` (دمج PR #170). المدخلات: جرد R-155 (`inventory/FILE_INVENTORY_f8b2d48.csv`) وخريطة R-156 (`maps/ENTRYPOINT_MAP_caab69b.json`).
- **المنهج**: لا يُعدّ تشابه الأسماء دليلاً على التكافؤ. لكل قدرة مرشحة شُغّل التنفيذان على **الحالات المرجعية نفسها** قبل أي تعديل (`076`، `077`)، ثم قُرر: توحيد بمحوّل، أو تثبيت العقد بلا تعديل، أو تسجيل الفجوة دون حسم.
- **الاختبارات**: `tests/test_r157_contract_parity.py` (50 اختباراً). طفرات 13/13 مقتولة بفشل assertion (`078` ثم `079`). تحقق تشغيلي بعملية uvicorn حقيقية على الوقتين (`080`).
- **لا حذف ولا نقل ولا إعادة تسمية**. ملفات التنفيذ القديمة (`BaseAgent.execute`، قوالب `specialists/` الألف، فرع البناء القديم في `check_civilization`) باقية كما هي.

## الملخص

| # | القدرة | القرار | تعديل الكود | الحالة |
|---|---|---|---|---|
| C1 | تشغيل مهمة على وكيل (`real` / `specialists`) | **وُحّد** العقد + محوّل | `runtime_contract.py` (جديد)، `specialist_runtime.py`، `real_agents.py` | متكافئ إلا فرقين موثقين |
| C2 | صحة الحضارة (`/health` / `/health/ready` / `/api/system/status`) | **وُحّد** على مصدر حقيقة واحد + محوّل | `agos_kernel/infrastructure/health.py`، `server.py` | متكافئ |
| C3 | الذاكرة والتعلّم (`experience` / محرك النواة) | **مثبت بلا تعديل** — طبقات لا تنفيذان متوازيان | — | العقد مثبت باختبارات |
| C4 | الرسائل بين الوكلاء (`communication` / `communication_hub`) | **موحّد أصلاً** (إعادة تصدير) | — | هوية الكائن مثبتة |
| C5 | اختيار الوكيل من المجمّع | **غير موحّد** — ثلاث سياسات مختلفة فعلاً | — | مثبتة كما هي؛ الفجوة G-157-3 |
| C6 | الواجهتان `src/` و`web/` | **غير محسوم** (Q-002) | — | توافق المسارات فقط؛ الفجوة G-157-4 |
| C7 | وكلاء البنية التحتية عبر hub مقابل `pipeline/orchestrator` | **خارج التوحيد** — عقدان مختلفان بلا مستهلك مشترك | — | موثق |

## C1 — تشغيل مهمة على وكيل

| البند | التفاصيل |
|---|---|
| ملفات التنفيذ | `agent_civilization/agents/real/real_agents.py` (`RealSpecialistAgent`)؛ `agent_civilization/agents/real/specialist_runtime.py` (`SpecialistBackedAgent` يغلّف إحدى 1,000 فئة من `agent_civilization/agents/specialists/`، مولّدة من `_templates/<spec>_base.py` عبر `_factory.py`)؛ `agent_civilization/core/agents/base_agent.py` (`BaseAgent.execute`)؛ `agent_civilization/core/agents/real_dispatcher.py` (المعالجات العشرون، مشتركة بين الوقتين). |
| مسارات الوصول الفعلية | `AGOS_AGENT_IMPL` (`real` افتراضي / `specialists`) ← `create_civilization` ← `server.CIVILIZATION`. ثم: `server._pick_agent` ← `agent.execute` في `/api/tasks` (مهمة خلفية) و`/api/chat`؛ `pipeline/orchestrator.execute_plan` ← `execute` في `/api/chat` متعدد المراحل و`/civilization/*`؛ `snapshot()` في `/api/agents`. مسار قديم: `BaseAgent.execute` عبر `_execute_work` لوكلاء hub. `run_real` دالة عامة مُصدّرة بلا مستهلك في المستودع (خريطة R-156). |
| العقد العام | **الدخل**: `work` قاموس (`prompt`، `data`، …) أو `None`. **الخرج**: `{ok, agent, elapsed_s, result}` + `experience` عند وجود مستأجر + `impl` في specialists. **الأخطاء**: استثناء غير متوقع ⇒ `result={"ok": False, "error": "<النوع فقط>"}`؛ `UnsafeURLError` ⇒ رسالته الثابتة؛ رفض المعالج ⇒ يُعاد كما هو. **الآثار الجانبية**: `stats` (مكتمل/فاشل/الزمن)، `status` working ثم idle، ذاكرة وتعلّم المستأجر عبر `run_with_experience` (لا شيء عند استثناء). **الحدود الأمنية**: R-149 (لا نص استثناء خام)، SEC-01/SEC-05 (رفض العناوين الخاصة)، SEC-02 (ذاكرة لكل مستأجر)، R-100 (تنفيذ الكود بموافقة صريحة). |
| المرجعي المختار | **العقد**: `agent_civilization/agents/real/runtime_contract.py` (`AgentRuntime`، مفاتيح الخرج واللقطة والإحصاءات، `public_error`، `INTENDED_DIFFERENCES`). **التنفيذ المرجعي**: `real` — الافتراضي في الخادم وCI، وفيه قاعدة R-149 مطبقة ومختبرة (`tests/test_r149_security_qa_gaps.py`). |
| الفرق المكتشف قبل التعديل (دليل `076`) | في `specialists` يلتقط `BaseAgent.execute` استثناء المعالج قبل الغلاف ويعيد `"RuntimeError: <الرسالة>"` ⇒ (1) **تسريب نص الاستثناء** إلى نتيجة المستأجر (يخالف R-149)، (2) `UnsafeURLError` يصبح `"UnsafeURLError: <msg>"`، (3) **تُخزَّن الحالة الفاشلة بنصها المسرّب في ذاكرة المستأجر وتعلّمه** بينما `real` لا يخزن شيئاً، (4) يغيب `"ok": False` من `result`. |
| المحوّل | `SpecialistBackedAgent._run_inner`: يستدعي `_process_task` للفئة نفسها بالمحاسبة نفسها التي يجريها `BaseAgent.execute` (`status`، `performance_metrics`، قاعدة ok من R-032، النتائج غير القاموسية) لكنه يعيد رفع الاستثناء إلى الغلاف المرجعي. القاعدة `public_error`/`failure_result` صارت مشتركة؛ مخرجات `real` لم تتغير. `BaseAgent.execute` نفسه دون تعديل لمستدعيه الآخرين. |
| الفروق المقصودة الباقية | (أ) `impl` في الخرج واللقطة (R-032). (ب) رسالة «لا معالج»: `no handler for <spec>` مقابل `no real dispatcher for <spec>` — غير قابلة للوصول للتخصصات العشرين؛ القوالب الألف لم تُمس. (ج) نتيجة معالج بلا مفتاح `ok`: الوقتان يعيدان `ok=False` (القاعدة الخارجية)، والفئة المغلّفة تعدّها «مكتملة» في `performance_metrics` (قاعدة `BaseAgent` القديمة) — سلوك قديم محفوظ وموثق. |
| الاختبارات | `test_c1_both_runtimes_satisfy_the_canonical_contract`؛ `test_c1_reference_case_same_outcome_on_both_runtimes` ×20 تخصصاً؛ `test_c1_edge_and_refusal_cases_match` ×5 (رفض JSON، مدخل فارغ، بلا prompt، `None`، SSRF)؛ `test_c1_ssrf_refusal_is_kept_on_both`؛ `test_c1_unexpected_exception_reports_type_only_on_both` (سلبي)؛ `test_c1_unsafe_url_error_keeps_its_fixed_message_on_both`؛ `test_c1_no_handler_is_the_documented_intended_difference`؛ `test_c1_specialist_instance_bookkeeping_is_preserved`؛ `test_c1_result_without_ok_key_matches_and_keeps_legacy_inner_count`؛ `test_c1_memory_and_learning_side_effects_match` ×3 (نجاح/رفض/استثناء)؛ `test_c1_no_tenant_no_memory_on_both`؛ `test_c1_public_error_rule`. |
| الثغرات والإجراء التالي | **G-157-5**: ثلاثة اختبارات في `test_r149_security_qa_gaps.py` ترقّع `real_agents._dispatch` فقط، فتفشل عند تشغيل الخادم على `specialists` — قبل R-157 وبعده (`081`، مع تجربة ضابطة على الأساس)؛ ليست عيباً في المحوّل، والمكافئ مغطى في C1. الإجراء: جعلها ثنائية الوقت في بند الاختبارات اللاحق. **G-157-6**: `run_real` عقد مختلف (لا غلاف، الاستثناء يمر خاماً، لا ذاكرة) بلا مستهلك — تُرك دون تعديل؛ أي استخدام لاحق يجب أن يمر عبر `execute`. CI يشغّل `real` وحده. |

## C2 — صحة الحضارة

| البند | التفاصيل |
|---|---|
| ملفات التنفيذ | `agos_kernel/infrastructure/health.py` (`check_civilization` ضمن السجل)؛ `server.py` (`/health` عبر السجل، `/health/ready`، `/api/system/status`). |
| مسارات الوصول الفعلية | `/health` ← `get_health_registry().get_system_health()` ← `check_civilization`؛ `/health/ready` ← `CIVILIZATION` مباشرة؛ `/api/system/status` ← `_all_agents()`؛ Docker HEALTHCHECK وCI docker يستدعيان `/health` (خريطة R-156). |
| العقد العام | `/health`: 200 healthy/degraded، 503 unhealthy، `checks.civilization={status,message,details.agent_count}`. `/health/ready`: 200 `{status:"ready",agents,specializations}`، 503 `{status:"not_ready",reason}` بثلاثة أسباب ثابتة. لا نص استثناء في أي منهما. |
| الفرق المكتشف قبل التعديل (دليل `077`) | `check_civilization` كان يبني **حضارة ثانية افتراضية** (1,000 وكيل) في كل نداء متجاهلاً `AGENTS_PER_SPEC` والحضارة الحية: مع `AGENTS_PER_SPEC=2` أعلن `/health` «1000 agents» بينما `/health/ready` و`/api/system/status` أعلنا 40؛ وفي حضارة فارغة قال `/health` «healthy» بينما `/health/ready` قال 503. |
| المرجعي المختار | الحضارة الحية `server.CIVILIZATION` (ما يخدم الطلبات فعلاً) والقاعدة `civilization_status` (نصوص `/health/ready` نفسها). |
| المحوّل | `set_civilization_provider` في `health.py`؛ الخادم يسجّل `lambda: CIVILIZATION` (يُقرأ وقت النداء). `/health/ready` يستخدم `civilization_status` نفسها بمخرجات مطابقة. **بلا مزوّد** (استخدام مستقل خارج الخادم) يبقى السلوك القديم حرفياً. |
| الفروق المقصودة | `/health` يعكس الآن العدد الحي ويصبح 503 في الحالتين اللتين يقول فيهما `/health/ready` 503 — هذا هو التوحيد المقصود (تغيير مرئي موثق في API.md). فشل غير متوقع في المزوّد ⇒ «readiness check failed» الثابت. فرع البناء القديم (بلا مزوّد) ما زال يعيد `str(e)` — غير مستخدم من الخادم (G-157-2). |
| الاختبارات | `test_c2_health_views_agree_on_the_live_civilization`؛ `test_c2_default_server_counts_match`؛ `test_c2_not_ready_state_is_unhealthy_in_both_views` ×2 (سلبي)؛ `test_c2_broken_civilization_does_not_leak_in_either_view` (سلبي)؛ `test_c2_server_health_does_not_build_a_second_civilization`؛ `test_c2_standalone_registry_keeps_legacy_behaviour`؛ `test_c2_civilization_status_rule`. تشغيلياً: `080` (40 = 40 = 40 على الوقتين). |
| الثغرات | **G-157-2**: فرع البناء القديم يكشف `str(e)` عند الاستخدام المستقل فقط. **G-157-1**: `/api/system/status` يعيد 500 عاماً إذا انكسر كائن الحضارة (ليس عرض صحة؛ خارج النطاق). |

## C3 — الذاكرة والتعلّم

| البند | التفاصيل |
|---|---|
| ملفات التنفيذ | `agent_civilization/core/experience.py` (واجهة لكل مستأجر)؛ `agos_kernel/agent_production/memory_system.py` و`learning_runtime.py` (المحرك + المفردتان العامتان `get_memory_system` / `get_learning_runtime`). |
| مسارات الوصول | الوقتان ← `run_with_experience`؛ `/api/memory` (GET/DELETE) ← `experience.search/stats/forget_tenant/reset`؛ `/api/system/status` ← `experience.public_stats`. المفردتان العامتان بلا مستهلك في المسار الحي. |
| القرار | **لا توحيد مطلوب**: `experience` يستخدم فئتي النواة محرّكاً بمثيلات خاصة به ويضيف حدود المستأجر (SEC-02) والاسترجاع بتشابه Jaccard. ليسا تنفيذين متوازيين. |
| الفرق المقصود | المفردتان العامتان غير مقيّدتين بمستأجر — استخدامهما من مسار حي يكسر SEC-02. |
| الاختبارات | `test_c3_live_path_uses_the_tenant_facade_not_kernel_singletons` (المسار الحي لا يمس المفردتين، على الوقتين)؛ `test_c3_facade_search_matches_engine_contents_per_tenant` (ما يعيده البحث = ما في المحرك لكل مستأجر؛ البحث بلا نطاق مرفوض). وتكافؤ الآثار الجانبية بين الوقتين في C1. |

## C4 — الرسائل بين الوكلاء

`agent_civilization/communication/__init__.py` يعيد تصدير `CommunicationHub` و`DeliveryResult` من `core/agents/communication_hub.py` (R-104c2). الاختبار `test_c4_public_messaging_api_is_the_implementation` يثبت هوية الكائن (`is`) وأن وقت specialists يشارك hub واحداً. لا عمل إضافي.

## C5 — اختيار الوكيل (غير موحّد)

| المسار | السياسة الحالية |
|---|---|
| `server._pick_agent` (`/api/tasks`، `/api/chat` بمرحلة واحدة) | أول وكيل خامل بترتيب المجمّع؛ الحمل لا يُحتسب. |
| `server._pick_agent("external")` | الأقل في `tasks_completed` بين الوكلاء الخارجيين. |
| `pipeline/orchestrator._pick` (متعدد المراحل) | الأقل حملاً (مكتمل + فاشل) بين الخاملين، ويُحجز فوراً (R-114). |

**لماذا لم يوحَّد**: توحيده يغيّر توزيع العمل الظاهر في `/api/agents` و`stats` المخزنة وأسماء الوكلاء في الاستجابات، ولا يوجد قرار أو قياس يحدد السياسة المرجعية لمسار HTTP المفرد. **G-157-3**: يحتاج قراراً (المالك أو بند الأداء في P6) مع قياس. السياسات الثلاث مثبتة في `test_c5_selection_policies_are_pinned_as_they_are` بحيث يصبح أي تغيير صامت فشلاً (الطفرة M12).

## C6 — الواجهتان `src/` و`web/` (Q-002 مفتوح)

- لم تُعدّل أي ملفات واجهة (`082`: `git diff` فارغ)؛ بناء `web/` واختباراته (31/31) وفحص الجذر (eslint + build) نجحت محلياً على Node 22.22.0.
- `test_c6_src_client_paths_exist_on_the_server_under_api_prefix`: كل مسار يستدعيه `src/lib/api.ts` موجود في الخادم تحت `/api` — **توافق مسارات فقط**.
- **G-157-4** (غير محسوم): `src/lib/api.ts` لا يرسل `X-API-Key` ولا `allow_public_llm`، بينما مسارات بيانات المستأجر (`/api/tasks`، `/api/chat`) ترفض المجهول منذ SEC-02 (رُصد تشغيلياً في `080`: «authentication is not configured…»). إصلاحه يستلزم معرفة هل تُنشر `src/` خارجياً — **قرار المالك في Q-002**. لا اختيار ضمني لواجهة نشر أساسية.

## C7 — وكلاء البنية التحتية عبر hub

`agents/infrastructure/` (`task_dispatcher`، `orchestrator_agent`، `resource_manager`) و`agents/executors/data_worker.py` تعمل برسائل `CommunicationHub`، بينما `pipeline/orchestrator.execute_plan` ينفّذ خطة HTTP على مجمّعات الحضارة. لا مستهلك مشترك ولا عقد مشترك، فلا أساس لادعاء التكافؤ أو التوحيد. يبقى كما هو (R-104).

## الفجوات غير المحسومة

| المعرّف | الوصف | الإجراء التالي |
|---|---|---|
| G-157-1 | `/api/system/status` يعيد 500 عاماً عند كسر كائن الحضارة | بند المرونة/الأخطاء اللاحق |
| G-157-2 | فرع `check_civilization` القديم (بلا مزوّد) يعيد `str(e)` | يُعالج عند أول مستهلك مستقل للسجل |
| G-157-3 | ثلاث سياسات لاختيار الوكيل | قرار + قياس (المالك / P6) |
| G-157-4 | `src/lib/api.ts` بلا مصادقة مقابل SEC-02 | قرار Q-002 |
| G-157-5 | ثلاثة اختبارات R-149 خاصة بوقت `real` | جعلها ثنائية الوقت؛ CI يشغّل `real` وحده |
| G-157-6 | `run_real` عقد مختلف بلا مستهلك | يُترك؛ أي استخدام جديد عبر `execute` |

## ما لم يُختبر

- لا تحقق على تخزين Supabase حي أو مزود LLM مدفوع في هذه الدفعة (اختبارات التخزين الحي تعمل في وظيفة `storage` في CI).
- لم يُحقن استثناء معالج في عملية خادم حية؛ مغطى بالاختبارات داخل العملية فقط.
- لا أهداف سعة (10,000 وكيل، 1,000,000 مهمة، 10,000 أداة) ولا جاهزية إنتاجية.
