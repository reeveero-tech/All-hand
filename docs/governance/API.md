# الواجهات (API)

> **آخر تحديث**: 2026-10-19 — **R-166 (D-085)**: مسار جديد `POST /civilization/coordinate` (الصف 27) — جولة تنسيق fan-out/fan-in فوق وكلاء الحضارة: الرسالة تصبح مهمة فرعية لكل تخصص مطلوب (الافتراضي: تخصصات الخطة الموجَّهة)، توزيع عادل، تنفيذ حقيقي، تجميع بكشف فقد صريح (غياب نتيجة = `ok:false` + `missing` — لا نجاح خفي)، وتقرير مقاييس. عقدان جديدان: `coordination_report` v1 و`coordinate_request` v1؛ والتغييرات الأخرى جمعية بلا رفع نسخة. قبلها 2026-10-19 — **R-165 (D-084)**: `POST /civilization/execute` يقبل `approve_stages` اختيارياً، والمراحل الحساسة (تنفيذ كود، طلب خارجي، تعديل ذاتي) تنتظر موافقة صريحة (`awaiting_approval`). الرد يضيف حقول تقرير DAG اختيارية (`run_id`، `status`، `pending_approvals`، `budget`، `order`، `ignored_approvals`). مساران جديدان: `GET /civilization/runs/{run_id}` و`POST /civilization/runs/{run_id}/resume` (الصفان 25 و26). عقدان جديدان: `stage_artifact` v1 و`resume_request` v1؛ والتغييرات الأخرى جمعية بلا رفع نسخة. قبلها 2026-10-18 — **R-163 (D-082)**: طابور مهام دائم اختياري (`AGOS_TASK_QUEUE=1` + تخزين مُعد). `POST /api/tasks` يقبل حقل `idempotency_key` اختيارياً (≤128 حرفاً): المفتاح نفسه لنفس المستأجر يعيد المهمة نفسها بنفس `task_id` و`duplicate: true` (بلا تشغيل ثانٍ ولا أثر جانبي ثانٍ) — عقد `task_accepted` صار v2 (الملف v1 محفوظ). في الوضع المتين تُقبل المهمة فقط بعد حفظها (فشل التخزين = 503 لا فقد صامت)، ويطالب بها المُرسِل (claim/lease) لا الطلب. الحالات الداخلية العشر (accepted/queued/claimed/running/waiting/retrying/completed/failed/cancelled/dead-letter) لا تُسرّب للـAPI: تُعرض عبر الربط العام (claimed/waiting/retrying → queued؛ cancelled/dead-letter → failed بنتيجة صريحة). مسار جديد: `POST /api/tasks/{task_id}/cancel` (الصف 24). عقد جديد `task_cancelled` v1، وعقد `task_row` صار v2 (أعمدة الطابور الست اختيارية — v1 محفوظ؛ ملفات العقود 29). قبلها 2026-10-17 — **R-157 (D-069)**: `GET /health` — `checks.civilization` يعكس الحضارة الحية (العدد الفعلي، و`unhealthy`/503 في حالتي عدم الجاهزية نفسيهما في `/health/ready`)؛ بنية الرد ورموزه كما هي. `/health/ready` بمخرجات مطابقة. تحت `AGOS_AGENT_IMPL=specialists` صار خطأ المعالج غير المتوقع يذكر النوع فقط كما في `real` (R-149). قبلها 2026-10-16 — **R-147 (BUG-05)**: لا تغيير في أي مسار أو عقد أو ردود — سطر سجل الإقلاع فقط صار يعلن العدد الفعلي لوكلاء الحضارة (مجموع قوائم `CIVILIZATION` الفعلي — `AGENTS_PER_SPEC` يتحكم فيه منذ R-041) بلا الثابت 50، مع بقاء الوكلاء الخارجيين في سطرهم المنفصل؛ لا أثر على أي سلوك موثّق هنا. قبلها **R-145 (SEC-06، D-059)**: المزود العام بلا مفتاح (keyless) صار **opt-in على مستويين** — (1) إعداد المشغّل `AGOS_LLM_KEYLESS=1` صراحةً (الافتراضي معطّل؛ القيمة غير الصالحة خطأ إقلاع)، و(2) موافقة صريحة لكل طلب عبر حقل `allow_public_llm` (boolean صارم؛ غيابه/`false`/قيمة غير منطقية = رفض — 422 للقيم غير المنطقية) في `POST /api/tasks` و`POST /api/chat` و`POST /civilization/execute`. البوابة مركزية في `LLMClient.complete()`: بلا الموافقة لا يُبنى طلب خارجي ولا يُرسل ولا تُلفق إجابة — فشل صريح بالعقد نفسه (`result.error` يحمل النص «public LLM provider not used: this request did not consent …»). المهام التي لا تحتاج LLM لا تتأثر. `GET /api/system/status` يضيف `llm_provider` (`openrouter`/`keyless`/`none`) و`public_llm_enabled` و`public_llm_requires_consent` (غير سرية). مزود OpenRouter ذو المفتاح وأولويته دون تغيير. الواجهة تعرض تحذيراً وتطلب تأكيداً لكل طلب عند تفعيل المزود العام. قبلها **R-144 (SEC-04)**: `GET /api/events/stream` — بث مستقل لكل اتصال (كل اتصال للمستأجر يستلم نسخته)، طابور محدود لكل اتصال مع إسقاط الأقدم وحدث `stream_gap`، حدود اتصالات ومعدل فتح وعمر أقصى، و429 + `Retry-After`. المصادقة والعزل (SEC-02) كما هما. قبلها **R-143 (BUG-04)**: `GET /api/tasks/{task_id}` يقرأ المهمة الغائبة عن ذاكرة العملية (بعد إعادة تشغيل) من التخزين بمعرّفها، ونطاق المستأجر داخل الاستعلام؛ مهمة مستأجر آخر وصف بلا مالك يبقيان 404 مطابقاً للمعرّف المجهول. قبلها **R-142 (BUG-03)**: `GET /health/ready` يرد **503** `{"status": "not_ready", "reason": …}` في كل حالة عدم جاهزية (كان 200)؛ الجاهزية الناجحة 200 بنفس الجسم؛ سبب الاستثناء ثابت «readiness check failed» (كان يعيد نص الاستثناء). `/health` و`/health/live` دون تغيير. قبلها **R-141 (BUG-02)**: `POST /api/chat` — رمز HTTP وبنية الاستجابة بلا تغيير؛ حالة المهمة الناتجة واحدة في الذاكرة والتخزين (`completed`/`failed` من `outcome.ok`؛ كان التخزين `completed` دائماً)، وحدث `chat_completed` يحمل `status` بجانب `ok`. قبلها **R-140 (BUG-01)**: `POST /api/tasks` بـ`specialization=external` حين لا يوجد وكيل خارجي مسجّل يرد **409** (بعد المصادقة وقبل أي أثر جانبي) بدل 202 ثم مهمة عالقة `queued`. قبلها **R-139 (SEC-03، D-057)**: لا تغيير في المسارات ولا في ردودها؛ `server.py` يستدعي `tenancy.enforce_startup` عند الاستيراد — في الإنتاج (`AGOS_ENV` غير مضبوط أو `production`) بلا بيانات اعتماد صالحة لا يقلع الخادم إطلاقاً (فلا ترد أي مسار، ولا `/health`). قبلها **R-138 (SEC-02، D-056): متعدد المستأجرين** — كل مسار يقرأ أو يغيّر بيانات مستأجر (المهام، الذاكرة، التعلم، الأحداث، تدفق SSE، `/api/chat`، `/civilization/execute`) يتطلب `X-API-Key` صالحاً يربطه الخادم بمستأجر (`AGOS_TENANT_KEYS` أو `AGOS_API_KEY`=المشغّل/admin)؛ المفتاح الخاطئ 401 (كان 403)؛ مهمة مستأجر آخر 404 كالمفقودة؛ `?all_tenants=true` للدور admin فقط (وإلا 403)؛ بلا إعداد مفاتيح كل هذه المسارات 401. تسجيل/حذف الوكلاء الخارجيين للدور admin. قبلها 2026-10-15 — R-132 (مجمّعات الخيوط؛ لا تغيير في العقد). قبلها R-131 (توجيه `/api/chat` مع LLM بلا مفتاح). قبلها 2026-10-14 — بعد R-104c2 (مسار النص في `data_processor`: الحمولة تصل LLM بالتعليمة **والبيانات** معاً — راجع الملاحظات)
> (تحديث سابق: 2026-10-12 — R-041: مصادلة مع الكود الفعلي وتصحيح أوصاف الحماية القديمة)
> **المصدر**: قراءة مباشرة من `server.py` و`agent_civilization/civilization_routes.py`

الإجمالي: **25 تعريف مسار** — 22 تعريفاً صريحاً في `server.py` (بما فيها catch-all؛ 24 أضافها R-163 و21..23 أضافها R-103 وأُبقي ترقيم ما قبلها) + 3 تعريفات ديناميكية.

**تنبيه مهم**: تعريف catch-all وتركيب `/assets` مشروطان بوجود `web/dist` (راجع `server.py:635-637`)؛ لذا عدد المسارات **المسجلة فعلياً** وقت التشغيل يعتمد على وجود الواجهة المبنية. العدد أعلاه هو عدد التعريفات في الكود.

## المسارات الصريحة

| # | المسار | الفعل | الحماية | ملاحظات |
|---|---|---|---|---|
| 1 | `/api` | GET | لا | معلومات API |
| 2 | `/health` | GET | لا | يعيد 503 عند `unhealthy` فقط (أُصلح في R-004؛ `degraded` يعني 200 — D-012). **R-157 (D-069)**: `checks.civilization` يحكم على الحضارة الحية للخادم بقاعدة `/health/ready` نفسها — `details.agent_count` = العدد الفعلي (كان دائماً 1000 من حضارة ثانية تُبنى في كل نداء)، و`unhealthy` (⇒ 503) بالسببين `Civilization not initialized` / `No agents available` أو «readiness check failed» الثابت، حين يرد `/health/ready` ‏503 |
| 3 | `/health/live` | GET | لا | liveness |
| 4 | `/health/ready` | GET | لا | readiness: **200** `{"status": "ready", "agents": N, "specializations": 20}` فقط حين الحضارة مهيأة وبها وكيل واحد على الأقل؛ وإلا **503** `{"status": "not_ready", "reason": …}` — `Civilization not initialized` / `No agents available` / `readiness check failed` (استثناء أثناء الفحص؛ السجل الداخلي يذكر نوع الاستثناء فقط) — R-142، BUG-03 (كان 200 في الحالات الثلاث) |
| 5 | `/metrics` | GET | لا | Prometheus (تصدير صحي — أُصلح في R-003b) |
| 6 | `/api/system/status` | GET | لا | healthCheckPath في render.yaml؛ SEC-02: كتلة `experience` إعدادات فقط (`enabled`, `max_entries`, `persistence_file`, `scope`) — لا عدادات ذاكرة/تعلّم؛ العدادات التشغيلية الإجمالية (`tasks_*`، `tasks_in_db`) أعداد بلا محتوى |
| 7 | `/api/agents` | GET | لا | `?specialization=` اختياري |
| 8 | `/api/specializations` | GET | لا | |
| 9 | `/api/tasks` | POST | `require_principal` (SEC-02): 401 بلا مفتاح/بمفتاح خاطئ/بلا إعداد مفاتيح | 202 Accepted؛ المالك = مستأجر المفتاح (أي `tenant_id` في الجسم أو الترويسات يُتجاهل)؛ 400 لتخصص مجهول (العقد كما هو)؛ **409** لـ`external` بلا وكيل خارجي مسجّل (R-140، BUG-01): `no external agent is registered: an 'external' task cannot run now (an admin registers one via POST /api/agents/external)` — بعد المصادقة (المجهول 401 دائماً) وقبل إنشاء `task_id` أو أي كتابة أو حدث أو SSE أو تشغيل خلفي |
| 10 | `/api/tasks` | GET | `require_principal` (SEC-02) | مهام مستأجر المتصل فقط؛ `?limit=`، `?all_tenants=true` (admin فقط، وإلا 403)؛ يعود للذاكرة إذا فشل Supabase؛ مع التخزين `source=database` و`result`/`event_data` كائنات JSON (R-042)؛ صفوف بلا `tenant_id` لا تظهر لمستأجر |
| 11 | `/api/tasks/{task_id}` | GET | `require_principal` (SEC-02) | 404 `{"detail": "task not found"}` إن لم توجد **أو كانت لمستأجر آخر** (رد مطابق لا يكشف الوجود)؛ `?all_tenants=true` admin فقط (وإلا 403). **R-143 (BUG-04)**: في الذاكرة ← سجل الذاكرة كما هو (وخارج النطاق 404 مباشرة، بلا رجوع للتخزين)؛ غائبة عن الذاكرة ومع Supabase ← `storage.get_task(task_id, scope=)` (مرشحا `task_id` و`tenant_id` في الاستعلام نفسه) ← صف التخزين كما هو + `id` (= `task_id`) + `source: "database"`. **فرق التمثيل** (موثق، بلا توحيد): سجل الذاكرة `id`, `agent`, `created_at`/`finished_at` بثوانٍ (epoch) ولا `source`؛ صف التخزين `task_id` (و`id` المضاف)، `agent_name`, `elapsed_s`, `created_at`/`finished_at` نص ISO-8601، و`prompt` ≤500 حرف — الحقول المشتركة: `status`, `result`, `tenant_id`, `specialization`, `prompt`. لا تُخترع قيم لحقول غائبة. بلا تخزين: سلوك الذاكرة كما كان |
| 12 | `/api/events` | GET | `require_principal` (SEC-02) | أحداث مستأجر المتصل فقط (`tenant_id=eq.` في PostgREST)؛ `?all_tenants=true` admin فقط |
| 13 | `/api/events/stream` | GET | `require_principal` (SEC-02) + `@rate_limit` (R-144) | SSE لأحداث مستأجر المتصل فقط؛ الواجهة تقرؤه بـfetch مع `X-API-Key` (`openEventStream`) لأن EventSource لا يرسل ترويسات. **R-144 (SEC-04)** — التفاصيل في «بث SSE وحدوده» أدناه: نسخة لكل اتصال، لا حفظ بلا مستمع، 429 عند تجاوز الحدود |
| 14 | `/api/chat` | POST | `require_principal` (SEC-02) | توجيه تلقائي: LLM إن توفر **بمفتاح** وإلا `routing.route()` (R-106؛ R-131: لا توجيه عبر LLM بلا مفتاح)؛ حقل `routing`. 200 دائماً بنفس الحقول (`task_id`, `routed_to`, `routing`, `agent`, `outcome`)؛ المهمة المسجلة `completed` أو `failed` حسب `outcome.ok` في `TASKS` والتخزين وحدث `chat_completed` (`status`) معاً (R-141، BUG-02) |
| 15 | `/api/agents/external` | POST | `require_admin` (SEC-02): 401 بلا مفتاح صالح، 403 لدور user | 201؛ الوكيل الخارجي مشترك بين المستأجرين (يستقبل مهام `external` للجميع) فالتسجيل للمدير؛ المدير قد يستهدف شبكة خاصة كما كان حامل `AGOS_API_KEY` (R-100) |
| 16 | `/api/agents/external/{name}` | **DELETE** | `require_admin` (SEC-02) | |
| 17 | `/{full_path:path}` | GET | لا | catch-all يخدم `web/dist` |
| 21 | `/api/memory` | GET | `require_principal` (SEC-02) | R-103: `?q=&specialization=&limit=` (≤100) — ذكريات مهام مستأجر المتصل فقط؛ `stats` للمستأجر؛ `?all_tenants=true` admin (يضيف `tenant_id` لكل ذكرى و`quarantined_untenanted`) |
| 22 | `/api/learning` | GET | `require_principal` (SEC-02) | R-103: ما قيس من مهام مستأجر المتصل فقط (`tenant_id` في الرد)؛ `?all_tenants=true` admin |
| 23 | `/api/memory` | **DELETE** | `require_principal` (SEC-02): 401 بلا مفتاح أو بلا إعداد مفاتيح (كان 403) | يمسح ذاكرة وتعلّم مستأجر المتصل فقط؛ `?all_tenants=true` (admin فقط) يمسح الكل بما فيه المعزول |
| 24 | `/api/tasks/{task_id}/cancel` | POST | `require_principal` (SEC-02) | **R-163 (D-082)**: إلغاء مهمة مستأجر المتصل (مستأجر آخر = 404 كالمفقودة). غير المنتهية ← 200 `{"task_id", "status": "failed", "cancelled": true, "result": …}` (التمثيل العام: الملغاة تُعرض failed بـ`result.result.error="cancelled"` — قاموس الحالة بلا تغيير)، وإنهاء التشغيل الحي، والكتابة المسوّجة إلى `cancelled` في التخزين (الوضع المتين) تمنع أي نتيجة متأخرة من إحيائها. المنتهية ← 409 بحالتها الحالية. الحد المتبقي (متطاير بلا تخزين): صف التخزين لا يُحدّث أثناء الإلغاء (G-160-2) |

## ملاحظات السلوك الدلالي (R-104c2)

- **مسار النص في `data_processor`**: مهمة `POST /api/tasks` ذات `specialization=data_processor` و`prompt` نصي (و/أو `data`) تُسلَّم إلى LLM عبر `process_text` بحمولة تجمع **التعليمة والبيانات معاً** (`build_llm_payload`: التعليمة ثم `=== data ===` ثم البيانات مسلسلة JSON). قبل R-104c2 كانت `data` تُسقط صامتة (التعليمة فقط). فشل LLM يُعاد كفشل صريح (`ok=False`، `status=failed`) — لا ادعاء «معالج» بمحتوى فارغ.
- **مستوى الوكيل** (`DataWorkerAgent`): العدادات صادقة — `tasks_completed` تُزيد فقط عند نجاح فعلي؛ `task_complete` تعني «انتهاء المحاولة» لا «نجاح» (المستهلك الحقيقي `TaskDispatcherAgent` يخفض الحمل بغض النظر عن `result.ok`).
- **بحث `researcher`**: إعادة واحدة للعابرين فقط (مهلة/قطع اتصال/EOF مفاجئ) بميزانية كلية 20ث (R-107c)؛ تحذير TLS الحقيقي (`SSLV3_ALERT_HANDSHAKE_FAILURE`) صلب لا يُعاد.

## المسارات الديناميكية (`civilization_routes.py`)

| # | المسار | الفعل | الحماية |
|---|---|---|---|
| 18 | `/civilization/registry` | GET | `verify_api_key` إن فُعّل (R-004/D-013) |
| 19 | `/civilization/plan` | POST | `verify_api_key` إن فُعّل (R-004/D-013) |
| 20 | `/civilization/execute` | POST | `require_principal` (SEC-02) — يخزّن مهمة بمالكها ويشغّل الذاكرة للمستأجر |
| 25 | `/civilization/runs/{run_id}` | GET | `require_principal` (R-165، D-084) — تقرير تشغيل DAG لمستأجر المتصل؛ مستأجر آخر أو معرّف مجهول = 404 مطابق |
| 26 | `/civilization/runs/{run_id}/resume` | POST | `require_principal` (R-165، D-084) — استئناف بموافقات جديدة؛ 404 كالسابق، و409 لتشغيل جارٍ |
| 27 | `/civilization/coordinate` | POST | `require_principal` (R-166، D-085) — جولة تنسيق fan-out/fan-in: توزيع عادل، كشف فقد، موافقة SEC-06 (`allow_public_llm`)، ومقاييس |

### خطط DAG واعتمادات المراحل (R-165، D-084)

- **`POST /civilization/execute`**:
  - **حقل جديد اختياري** `approve_stages`: قائمة أسماء مراحل (≤32، نصوص). أي نوع آخر = 422.
  - **المراحل الحساسة**: مرحلة تخصصها `test_runner` أو `modifier` (تنفيذ كود)، أو `network_client` أو `api_integrator` (طلب خارجي)، أو `self_developer` (تعديل ذاتي)، لا تُنفَّذ إلا إذا سُمّيت. وإلا تتوقف بحالة `awaiting_approval`، وما بعدها `blocked`.
  - **الرد**: 200 دائماً. `status` = `awaiting_approval`، مع `pending_approvals: [{stage, specialization, why}]`.
  - **الأسماء المجهولة**: تُعاد في `ignored_approvals` ولا تُرفض.
  - **خطة غير صالحة** = 400 `invalid plan: …`، والسجل `failed`. خطط `auto_router` صالحة دائماً، فهذا حارس.
- **حقول الرد الجديدة** (جمعية، `execute_response` v1):
  - على مستوى التشغيل: `run_id` (32 حرفاً سداسياً)، و`status`، و`pending_approvals`، و`budget {max_stage_runs, stage_runs_used, deadline_s}`، و`order` (طوبولوجي)، و`ignored_approvals`.
  - لكل مرحلة: `depends_on` و`status` (succeeded/failed/blocked/awaiting_approval/timed_out/skipped/cancelled) و`attempts` و`error` و`artifacts` (أنواع القطع) و`requires_approval` و`sensitive`.
  - `ok` و`output` و`agent` و`elapsed_s` كما هي.
  - سجل المهمة (`GET /api/tasks/{id}`) يحمل `run_id`، وتبقى حالته `completed` حين تعمل الخطة.
- **`GET /civilization/runs/{run_id}`**: التقرير نفسه مع `task_id`.
- **`POST /civilization/runs/{run_id}/resume`**:
  - الجسم `resume_request` v1: `approve_stages` و`allow_public_llm` (boolean صارم، SEC-06).
  - المراحل الناجحة لا تُنفَّذ ثانية، وما سواها يُعاد تشغيله.
  - تشغيل مكتمل يُعاد كما هو بلا تنفيذ.
- **`POST /civilization/plan`**: يعيد الخطة **المطبّعة** (`depends_on`، `requires_approval`، `sensitive`، `max_attempts`، `timeout_s`، `order`، `budget`).
- **الحدود**: السجل في ذاكرة العملية، فيضيع بإعادة التشغيل ولا يُشارك بين النسخ (RISK-035).

## ملاحظات مُتحققة

- واجهة التسجيل تطلب `name, url, model, api_key` (Pydantic `ExternalAgentRequest`).
- `POST /api/tasks` يقص الـ prompt إلى 500 حرف.
- `GET /docs` (Swagger) متاح ويستجيب 200 — مُتحقق فعلياً في R-041 (دليل M4/logs/17).
- `AGOS_AGENT_IMPL` (R-032): مع `specialists` تحمل لقطات الوكلاء (`/api/agents` ونحوها عبر `snapshot()`) حقلاً إضافياً `impl` (اسم الصنف، مثل `AnalystAgent0015`)، ونتيجة `execute` حقل `impl`. بقية الشكل مطابق. الافتراضي `real` بلا تغيير.
- `AGENTS_PER_SPEC` متغير بيئة يقرؤه `server.py` منذ R-041 (افتراضي 50) ويحدد عدد الوكلاء لكل تخصص.

## فجوات التوثيق

- لا يوجد OpenAPI مخصص خارج المولد الافتراضي من FastAPI.
- المصادقة على المسارات الحساسة تُفرض عبر `verify_api_key` (R-004)؛ منذ R-101 الواجهة `web/` ترسل `X-API-Key` الذي يدخله المستخدم (كان قيداً موثقاً لـ R-043).

## دورة حياة الخادم (R-042، M7/6)
- `FastAPI(lifespan=_lifespan)`: عند الإيقاف تُغلق جلسة HTTP لطبقة التخزين (`get_storage().close()`) — كانت تتسرب ("Unclosed client session"). لا تغيير في المسارات أو العقود.
- التحقق من المسارات مع تخزين حي: `tests/test_r042_live_storage.py` (POST/GET `/api/tasks`، GET `/api/tasks/{task_id}` بعد إفراغ الذاكرة — R-143، `/api/events`، `/api/chat`، `/api/system/status`).

## R-078 — تصنيف مفتاح Supabase (2026-10-13)
- `GET /api/system/status`: حقل جديد `supabase_key: {"kind": "service"|"public"|"unknown"|"none", "source": "<اسم المتغير>"|"none"}` — **لا يحوي قيمة المفتاح أبداً**. الحقول الموجودة بلا تغيير (`supabase_configured` باقٍ).
- `GET /health`: `checks.storage.status` = `degraded` (والرمز 200 كما هو) إذا كان المفتاح عاماً، مع رسالة توجّه إلى `SUPABASE_SERVICE_KEY`؛ `checks.storage.details.supabase_key` = النوع. المفتاح السري → `healthy` كما كان.
- عند البدء (lifespan): سطر سجل واحد — `WARNING` للمفتاح العام أو غير المعروف، `INFO` للسري. الاختبارات: `tests/test_r078_supabase_key_kind.py` (18).

## R-082 — نتائج التخزين وقت التشغيل وسلاسل المقاييس (2026-10-13)
- `GET /health`: `checks.storage.status` = `degraded` إذا فشلت آخر عملية تخزين (رسالة: العملية + `HTTP <code>` أو `exception: <Type>` + الوقت)، و`healthy` بعد أول نجاح. `details` تضيف `last_ok`، `last_error`، `last_success_at`، `total_ok`، `total_failed`. الرمز 200 كما هو. ترتيب الأولوية: غير مهيأ ← مفتاح عام (R-078) ← فشل آخر عملية.
- `GET /metrics`: `agos_storage_operations{operation="…",outcome="ok|failed"}` (تراكمي منذ بدء العملية، مُصدَّر كـgauge) و`agos_storage_last_operation_ok` (1/0/‎-1). **إصلاح**: كل gauge/counter موسوم صار سلسلة مستقلة — `agos_agents_active` يصدّر 20 سطراً (كان 1).
- الاختبارات: `tests/test_r082_storage_outcomes.py` (8).

## R-083 — طلبات HTTP في `/metrics` (2026-10-13)
- `MetricsMiddleware` مركّب: `agos_http_requests_total{method,endpoint,status}` و`agos_http_request_duration_seconds` (مدرّج: `_bucket{…,le}`، `_sum`، `_count`) لكل مجموعة وسوم.
- `endpoint` = قالب المسار المطابق (مثل `/api/tasks/{task_id}`، `/{full_path}` لمسارات الواجهة)، `<unmatched>` لغير المطابق؛ طلبات `/metrics` لا تُعدّ. استثناء غير معالج → `status="500"`.
- لا تغيير في أي استجابة؛ SSE `/api/events/stream` يمر بلا تخزين مؤقت (اختبار).

## R-100 — سياسة الأدوات الحقيقية (D-044، 2026-10-14)
- `GET /api/system/status`: حقل جديد `security.tool_policy` = `{"code_execution": {"allowed", "reason", "limits", "wall_timeout_s", "server_process_non_dumpable"}, "outbound_network": {"private_targets_allowed"}, "sqlite": {"time_budget_s", "attach", "max_value_bytes"}}` — لا قيمة سرية. الحقول الموجودة بلا تغيير.
- `POST /api/tasks` بـ`specialization=test_runner` في الوضع المفتوح: 202 كما كان، والنتيجة `{"ok": false, "error": "code execution is disabled in open mode: …", "tool_output": {"executed": false}}`. عند الإذن: `tool_output` يضيف `elapsed_s` و`sandbox: true`؛ القتل بحد العزل → `answer: "killed by signal N (sandbox limit)"`.
- `api_integrator`/`network_client` لهدف غير عام: `{"ok": false, "error": "blocked: …"}`؛ `network_client` (فحص DNS) يضيف `addresses` (كل العناوين المحلولة) بجانب `ip`.
- `db_manager`: `ATTACH`/`VACUUM INTO` → `sqlite: not authorized`؛ تجاوز 3 ثوانٍ → `sqlite: interrupted`؛ قيمة > 1MB → `sqlite: string or blob too big`؛ الحد 50 صفاً (كما كان).
- `POST /api/agents/external`: 400 `url rejected: blocked non-public address …` إذا كان العنوان داخلياً والخادم في الوضع المفتوح بلا `AGOS_ALLOW_PRIVATE_NETWORK=1`؛ 400 `url host cannot be resolved` لاسم لا يُحل.

## R-101 — عقد الواجهة في الوضع المصادَق (2026-10-14)
- لا تغيير في الخادم. العقد المثبت (`tests/test_r101_web_auth_contract.py`): `GET /api/system/status` → `security.authentication = "enabled"|"disabled"` (تكتشف به الواجهة الوضع بلا مفتاح)؛ مسارات القراءة التي تستطلعها الواجهة (`/api/specializations`، `/api/agents`، `/api/tasks`، `/api/events`، `/api/events/stream`) بلا مفتاح؛ الكتابة: 401 `API key required` بلا مفتاح، 403 `Invalid API key` بمفتاح خاطئ؛ CORS يسمح بترويسة `X-API-Key`.
- العميل (`web/src/api.js`): يرسل `X-API-Key` إن وُجد في sessionStorage (`agos_api_key`)؛ أخطاؤه من نوع `ApiError` بحقل `status`؛ 401/403 تُطلق حدث `agos-auth-required`.

## R-132 — لا تغيير في العقد (2026-10-15، D-055)
- `POST /api/agents/external`: فحص عنوان الوكيل الخارجي (`safety.check_url`، DNS) يجري الآن على مجمّع الخيوط `net` بدل المجمّع الافتراضي المشترك؛ الطلب والاستجابة ورموز الحالة بلا تغيير.
- `/api/chat`: المعالجات الحاجبة تجري على مجمّعات منفصلة (`agent_civilization/core/pools.py`)؛ شكل الاستجابة بلا تغيير. نص خطأ جديد ممكن في `outcome.error` لـeducator: «web: no result within 20s (queue + call, AGOS_WEB_WAIT_S)».

## R-131 — `/api/chat` لا يوجّه عبر LLM بلا مفتاح (2026-10-15، D-054)
- **قبل**: مع LLM بلا مفتاح (الافتراضي بلا `OPENROUTER_API_KEY`) كل رسالة تنتظر استدعاء توجيه للمزود العام — حتى «sum these numbers» انتظر وسيط 47–88ث تحت حمل HTTP متزامن (`docs/audit/M7/logs/raw/156`). **بعد**: `routing.method` = `keywords` مع المزود بلا مفتاح؛ `llm` فقط مع مزود بمفتاح. شكل الاستجابة بلا تغيير.
- تحت ضغط LLM بلا مفتاح قد يحمل `outcome.error` النص «keyless LLM busy: N request(s) ahead …» (رفض فوري بدل انتظار طويل)؛ المعالجات ذات الاحتياطي تستخدمه فوراً.

## R-106 — توجيه `/api/chat` (2026-10-14)
- **قبل**: بلا LLM (الافتراضي) كل رسالة → `communicator`. **بعد**: LLM أولاً إن كان مهيأً وذكر تخصصاً صالحاً؛ وإلا `agent_civilization.routing.route()` (الموجّه نفسه الذي يستخدمه `/civilization/*`).
- الاستجابة تضيف `routing: {"method": "llm"|"keywords", "reason": "<سبب>"}`؛ `routed_to` و`agent` و`outcome` بلا تغيير في الشكل.
- `route()`: النية تُقرأ من نص التعليمة بعد حذف البيانات المضمنة (كائنات JSON، كتل ```، النصوص بين علامتي تنصيص مزدوجتين)؛ إن خلت التعليمة من كلمة مفتاحية يُبحث في النص كاملاً (كتلة ```python``` وحدها تبقى `code_generator`).

## R-102 — شكل ناتج المعالجات (D-045، 2026-10-14)
الحقول داخل `result.tool_output` لمهام هذه التخصصات (عبر `/api/tasks` و`/api/chat` و`/civilization/*`):
- `builder`: `{stack, project, source, persisted: false, files: [{path, bytes, check: compile|json|toml|none, verified, content}]}`؛ `ok=false` لمسار غير آمن (مطلق، `..`، مسافات) أو أكثر من 20 ملفاً أو ملف لا يجتاز الفحص. `data` قائمة مسارات أو `{مسار: محتوى}` (يُعتد بها فقط إن بدت كل المداخل مسارات).
- `architect`: `{assumed_baseline, components: [{name, layer, responsibility, depends_on, why}], layers, build_order, edges, unmatched_items}` (كان `{layers, detected_actions, entities}`).
- `designer`: `{palette: {background, surface, text, primary, secondary, accent}, theme, source, hue, wcag: {<pair>: {ratio, target, pass}}, typography_scale: {base_px, ratio, sizes_px}}` (كان `{palette: [5 hex], typography_scale: [..]}`).
- `modifier`: `{changed, applied, new_code, diff, verified}` (كان `{changed, new_lines, diff_bytes}` بلا الكود).
- `code_generator`: `{generated: bool, method: structured-spec|llm, code, functions, classes, verified}`؛ للكود المرفق `generated: false`. بلا LLM وبلا مواصفة: `ok=false` برسالة فيها مثال مواصفة.
- `analyst` (نص): `{type: "text", words, chars, sentences, questions, language, top_terms, unique_terms}`؛ `strategist`: يقبل `data` قائمة نصوص.

## R-103 — `experience` في ناتج كل مهمة (D-046، 2026-10-14)
ناتج الوكيل (`outcome` في `/api/chat`، `result` في `GET /api/tasks/{id}`، مراحل خطوط الأنابيب) يضيف بجانب `ok/agent/elapsed_s/result`:
`experience: {memory: {recalled: [{memory_id, score, ok, prompt, answer, agent, age_s}], stored_id, fallback_used}, learning: {prior: {specialization, input_kind, samples, success_rate?, avg_duration_ms?}, after: {...}}}`.
يغيب الحقل إن `AGOS_MEMORY=0` أو فشلت الطبقة. عند بديل `researcher` يحمل `result`: `degraded: true`، `source: "memory"`، `live_error`، `memory_id`. `GET /api/system/status` يضيف `experience` (الإحصاءات نفسها التي يعيدها `/api/learning`).
## ملاحظات السلوك الدلالي (R-105)

- **الأصناف المولَّدة**: أصناف التخصصات الخمسون لكل تخصص تُولَّد عند الاستيراد من صنف أساس واحد لكل تخصص (`_templates/<spec>_base.py`) عبر `type()` — بالأسماء/الافتراضات/الترتيب التاريخية نفسها. الاستيراد عبر الجذر أو الحزمة أو الملف الفردي يعيد **نفس كائن الصنف**. لا تغيير في الواجهة أو السلوك (1000/1000 محفوظة).
- **`create()`**: الاسم يُحسب وقت الاستدعاء (`create(hub, aid)` → `name="{spec}_{aid:04d}"`) — ليس مُثبَّتاً.

## بث SSE وحدوده — `GET /api/events/stream` (R-144، SEC-04)

- **المصادقة والعزل**: كما في SEC-02 — `X-API-Key` إلزامي (401 بلا مفتاح أو بمفتاح غير صالح، **قبل** أي اشتراك أو حجز سعة أو احتساب في معدل الفتح)؛ الاتصال يشترك في أحداث مستأجر المفتاح **فقط**. لا يوجد بث شامل: مفتاح المدير يستلم أحداث مستأجره هو، ولا يقبل المسار `all_tenants` (يُتجاهل).
- **البث**: كل اتصال اشتراك مستقل بطابوره الخاص؛ `_publish(tenant, event)` ينسخ الحدث لكل اشتراكات المستأجر الحالية — مستمعان للمستأجر نفسه يستلمان الحدث نفسه (قبل R-144: طابور واحد مشترك يستهلك منه مستمع واحد). بلا مستمع لا يُحفظ الحدث (قبل R-144: يتراكم بلا حد ويُسلَّم لاحقاً): الاستمرارية مسؤولية التخزين، والعميل يستعيد الحالة من `/api/tasks` و`/api/events`.
- **التنسيق**: كما كان `data: <JSON>\n\n`؛ أحداث المهام (`task_created`, `task_started`, `task_completed`) بلا تغيير؛ `{"type": "heartbeat"}` بعد `AGOS_SSE_HEARTBEAT` ثانية (30 افتراضياً، كما كان) بلا أحداث.
- **الازدحام**: طابور كل اتصال محدود (`AGOS_SSE_QUEUE_SIZE`، 100). الناشر لا ينتظر أبداً؛ إن امتلأ طابور مستمع بطيء يُسقط **أقدم** حدث فيه لصالح الأحدث، ولا يتأثر غيره. يستلم ذلك المستمع قبل بقية أحداثه حدثاً اصطناعياً واحداً: `{"type": "stream_gap", "missed": <عدد المفقود>, "recover": ["/api/tasks", "/api/events"]}` — **فقد أحداث تحت الضغط سلوك موثق**: على العميل إعادة قراءة الحالة (الواجهة تستدعي `refreshStatus`). العدّ: `agos_sse_events_dropped_total` في `/metrics` وسجل تحذير.
- **القبول قبل 200** (بالترتيب): `@rate_limit` العام ← المصادقة ← معدل الفتح لكل عميل (`AGOS_SSE_OPEN_RATE` خلال `AGOS_SSE_OPEN_WINDOW`: 10/60 ث) ← الإجمالي (`AGOS_SSE_MAX_CONNECTIONS`، 100) ولكل مستأجر (`AGOS_SSE_MAX_PER_TENANT`، 10) ولكل عميل (`AGOS_SSE_MAX_PER_CLIENT`، 5). العميل = `request.client.host` (لا يُوثق بـ`X-Forwarded-For`). المحاولة المرفوضة للسعة تُحتسب في معدل الفتح.
- **الرفض**: **429** `{"detail": "too many event streams, retry later"}` (رسالة ثابتة لكل الأسباب، لا معلومات عن مستأجرين أو اتصالات أخرى) مع `Retry-After`: لمعدل الفتح = الثواني حتى خروج أقدم محاولة من النافذة؛ للسعة = 30. تجاوز `@rate_limit` العام: 429 بالرسالة العامة (بلا `Retry-After`).
- **العمر**: يُنهى البث بعد `AGOS_SSE_MAX_LIFETIME` (3600 ث) بإغلاق طبيعي؛ العميل يعيد الاتصال. التنظيف (الاشتراك والعدادات) مضمون مرة واحدة عند الانتهاء والإلغاء وانقطاع العميل والاستثناء، وحتى إن لم يبدأ مولد الاستجابة (`_SSEResponse`).
- **العميل** (`web/src/api.js` `openEventStream`): 401/403 ← توقف (لا حلقة)؛ 429 ← ينتظر `Retry-After` (حد ساعة) وليس أقل من التراجع؛ أخطاء أخرى/الشبكة ← تراجع أسي من `retryMs` (5 ث) ×2 حتى `maxRetryMs` (5 دقائق)؛ بث قُبل ثم انتهى (العمر الأقصى) ← إعادة بعد `retryMs` وتصفير التراجع.
- الاختبار: `tests/test_sec04_sse_broadcast_bounds.py` (خادم uvicorn حقيقي واتصالات متزامنة)، `web/tests/api.test.mjs`.

## R-145 — موافقة صريحة للمزود العام (SEC-06، D-059، 2026-10-16)

- **الحقل**: `allow_public_llm` (boolean **صارم**؛ الافتراضي `false`) في أجسام `POST /api/tasks` و`POST /api/chat` و`POST /civilization/execute`. غياب الحقل أو `false` = رفض؛ قيمة غير منطقية (نص `"true"`، رقم `1`، `null`) = **422** — الموافقة لا تُستنتج أبداً من نص `prompt` ولا من مفتاح API صالح ولا من إعداد المشغّل.
- **البوابة المركزية**: الحارس في `LLMClient.complete()` (المسار المركزي الوحيد للمزود العام)، لا في الواجهة وحدها — استدعاء المكتبة مباشرة أو مهمة خلفية أو مسار منسِي عاجز عن الإرسال. بلا موافقة: لا يُبنى الطلب الخارجي ولا يُرسل ولا تُلفق إجابة؛ يُعاد فشل صريح بعقد نتائج LLM نفسه: `{"ok": false, "content": "", "error": "public LLM provider not used: this request did not consent to sending its text to the external public provider (send allow_public_llm=true with the request, or set OPENROUTER_API_KEY for the keyed provider)", "model": …}`.
- **عقد المسارات**: لا يتغير رمز HTTP ولا شكل الاستجابات — `POST /api/tasks` يبقى 202 والمهمة تنتهي `failed` بنتيجة تحمل النص أعلاه في `result.error` (فوري ومفهوم، لا مهمة عالقة)؛ `POST /api/chat` يبقى 200 والنص في `outcome.error`؛ `POST /civilization/execute` يعيد نتيجة المراحل كما هي. المهام التي لا تحتاج LLM تنجح كالمعتاد (العقد المختار: قبول المهمة وفشل واضح عند حاجتها للمزود العام بلا موافقة — لا رفض مسبق يشمل عمل لا يحتاج LLM).
- **بوابتان مستقلتان**: موافقة الطلب لا تتجاوز تعطيل المشغّل (`AGOS_LLM_KEYLESS=0`/غياب ← `provider=none` ← خطأ «OPENROUTER_API_KEY is not configured» المعتاد)؛ وتفعيل المشغّل وحده لا يكفي بلا موافقة الطلب. المزود بمفتاح OpenRouter وسلوكه وأولويته دون تغيير ولا يحتاج هذا الحقل.
- **عدم التسرب**: الموافقة تُحمل في سياق الطلب (ContextVar) وتُنسخ إلى العمل الخلفي وحده؛ لا تُخزّن في سجل المهمة أو الأحداث أو التخزين، ولا تنتقل لمستأجر آخر أو لطلب لاحق، وتُصفّر بعد الطلب. لا تُسجّل نصوص المستخدم ولا قيم الموافقة في السجلات.
- **`GET /api/system/status`** يضيف: `llm_provider` (`openrouter` | `keyless` | `none`)، `public_llm_enabled` (bool — هل فعّل المشغّل المزود العام)، `public_llm_requires_consent` (bool — إرسال النص إليه يحتاج موافقة الطلب). كلها غير سرية ولا تكشف مفاتيح أو محتوى. `llm_configured` القديم باقٍ.
- **الواجهة (`web/`)**: عند `llm_provider=keyless` تعرض تحذيراً بجانب حقول المحادثة وإنشاء المهام يشرح تدفق البيانات الخارجي (نص الطلب والتعليمات قد تُرسل إلى مزود خارجي — Pollinations ومزودو النماذج خلفه) وترفض الإرسال بلا تأكيد صريح لكل طلب على حدة (خيار غير محدد مسبقاً وغير محفوظ، يُصفّر بعد كل إرسال)؛ عند `none`/`openrouter` لا تعرض ادعاءً عن مزود عام. الواجهة لا ترسل الطلب إلا بعد الموافقة الصريحة، وعند غيابها يظهر سبب مفهوم.
- **الإعدادات**: `AGOS_LLM_KEYLESS` ‏(عمليات) — غير مضبوط/`0`/`false`/`no` = معطّل؛ `1`/`true` = مفعّل؛ غير ذلك = خطأ إقلاع (`KeylessConfigError`). `.env.example` و`render.yaml` صارا يعطّلانه افتراضياً.
- **الاختبار**: `tests/test_sec06_public_llm_consent.py` (43 — عمليات جديدة للاختيار/الإقلاع، بوابة النقل بعدّاد استدعاءات، حارس طبقة الشبكة بلا استبدال نقل، المسارات الحية الثلاثة، الخلفية بحاجز بعد عودة الطلب، مستأجران حقيقيان عبر المسارات، الطلبات المتزامنة، مسار OpenRouter بنقل وهمي، تنقية السجلات، عزل إعادة اختبار self_developer، عدم التسرب، العقد) + `web/tests/api.test.mjs` (+2) + `web/tests/ui_consent.test.mjs` (8 اختبارات سلوك واجهة حقيقية — R-145b).

## R-159 — الرصد لا يغيّر العقد (D-071، 2026-10-17)
- لا مسار جديد، ولا رأس جديد، ولا حقل جديد في أي استجابة، ولم تتغير قيم الإرجاع ولا رموز الحالة.
- `/metrics` (القائم) يضيف سلسلتين: `agos_ops_total{kind,op,outcome}` و`agos_op_duration_seconds{kind,op}` (histogram). الوسوم محدودة القيم، ولا تحوي معرّفات طلب أو مهمة أو مستأجر.
- `TelemetryMiddleware` (ASGI خام، أبعد middleware) يفتح trace لكل طلب عدا `/health*` و`/metrics`، والبث SSE يمر دون تعديل. `trace_id` يظهر في سجلات الخادم فقط، ولا يُعاد للعميل.

## R-160 — العقود القابلة للتحقق (D-072، 2026-10-17)
- **المرجع**: [`docs/governance/contracts/README.md`](contracts/README.md) والمخططات `docs/governance/contracts/*.v1.schema.json`، المولّدة من `agent_civilization/core/contracts.py`. فحص الانحراف: `python3 scripts/export_contract_schemas.py --check` (خطوة في job ‏`test` عبر `tests/test_r160_contracts.py`).
- **ما يغطيه v1**: `POST /api/tasks` (202 ‏task_accepted)، `GET /api/tasks/{id}` (task_record أو execute_record)، `GET /api/tasks` (task_list بمصدر `memory` أو `database`)، `POST /api/chat`، `POST /civilization/execute`، `GET /api/system/status`، `GET /health/ready` (200 ‏ready، 503 ‏not_ready)، `GET /api/memory`، أجسام الأخطاء `{"detail": "<نص>"}` (400/401/404/409/429/500) و422 (`detail` قائمة FastAPI)، وأطر SSE.
- **الحقول غير المعروفة في الطلب**: تُتجاهل ولا تُنفَّذ (السلوك القائم، بلا تغيير). `tenant_id` في الجسم لا يغيّر المالك (SEC-02). `allow_public_llm` يجب أن يكون JSON boolean (القائم منذ SEC-06).
- **سلوك صار موثقاً كعقد v1 (مرصود، لم يتغير)**:
  - الطلب المكرر لـ`POST /api/tasks` أو `/api/chat` = مهمتان بمعرّفين مختلفين (لا مفتاح idempotency — G-160-4).
  - قطع العميل لطلب `/api/chat` بطيء لا يلغي العمل: السجل يكتمل `completed` ويُسترجع من `/api/tasks` (مقيس عبر uvicorn حقيقي، `112`).
  - SSE بلا `id:` ولا إعادة تشغيل: الاتصال الجديد يستقبل ما يُنشر بعده فقط (G-160-5). قطع الاتصال يحرر المقعد (`agos_sse_connections` يعود 0).
- **سلوك تغيّر (إصلاح، لا تغيير شكل)**:
  - مهمة يُلغى تشغيلها الخلفي (مثل الإيقاف) أو يرفع استثناءً غير متوقع: السجل `failed` بالنتيجة `{"error": "task cancelled before completion"}` أو `{"error": "task failed: internal error"}`، ويُنشر `task_completed` بـ`ok: false` (كان يبقى `running` للأبد). صف التخزين لا يُعاد كتابته عند الإلغاء (G-160-2).
  - `/civilization/execute`: استثناء داخلي ⇒ 500 كما كان، والسجل `failed` (كان `running`).
  - استجابة LLM أو وكيل خارجي بمحتوى null/فارغ/بنية خاطئة ⇒ نتيجة `ok: false` بسبب ثابت (`LLM empty completion`، `LLM malformed completion`، `keyless LLM malformed completion`، `empty completion`، `malformed completion`) — كانت `ok: true` بإجابة فارغة.
  - صف تخزين مشوه (بلا `task_id` أو بمعرّف مختلف، أو جسم ليس قائمة): `GET /api/tasks/{id}` ‏404 (كان 500)، و`GET /api/tasks` يعود إلى مصدر `memory`.
  - `/health`: رسالة المكوّن الفاشل `Error: <نوع الاستثناء>` بلا نص الاستثناء (G-159-1/G-157-2).

## R-162 — سجل الوكيل والسعة (D-081، 2026-10-18)
- **لا نقطة نهاية جديدة ولا تغيير في شكل العقود v1.** السجلات الناتجة تطابق `task_record` (يتحقق منه `tests/test_r162_agent_registry.py`).
- **`POST /api/tasks` (التشغيل الخلفي)**: قبل `running` يأخذ التشغيل إيجاراً على عامل العملية، ويحرره في نهايته أياً كانت النتيجة.
  - إذا بلغ عدد المهام الجارية `AGOS_WORKER_CAPACITY` (افتراضي 1024): السجل `failed` بالنتيجة `{"error": "worker capacity saturated: the task was not started (AGOS_WORKER_CAPACITY tasks are already running)"}`، و`agent: null`، ويُحدَّث التخزين ويُنشر `task_completed` بـ`ok: false`. المهمة لا تنتظر في طابور (R-163/R-164).
  - عامل في `draining` (أو غير متاح لسبب آخر) → السبب الثابت `worker not available`. وبعد إيقاف lifespan، إذا استمرت العملية تخدم، تبدأ المهمة التالية جيلاً جديداً من العامل `inproc-<pid>-<n+1>`، ويُحذف المتوقف من الجدول.
  - إيقاف الخادم أثناء تشغيل مهمة: السجل `failed` بالنتيجة `{"error": "task cancelled before completion: worker stopped"}`. والنتيجة التي تصل بعد ذلك تُهمل، فلا تعيد السجل إلى `completed`. صف التخزين لا يُعاد كتابته هنا (امتداد G-160-2).
- **`DELETE /api/agents/external/{name}`**: يعطّل هوية `external:<name>` في السجل، فلا تأخذ إيجاراً جديداً، والمهام الجارية تكمل. إعادة التسجيل تعيد تفعيلها.

## R-164 — الضغط الخلفي والحدود (D-083، 2026-10-18)
- **لا نقطة نهاية جديدة، ولا تغيير في جسم أي رد**: كل رفض جديد جسمه `error_detail` v1 (`{"detail": "..."}`)، والجديد ترويسة **`Retry-After`** (ثوانٍ صحيحة ≥1).
- **`POST /api/tasks`** (الوضعان، بعد فحص إعادة الإرسال بالمفتاح نفسه وقبل أي أثر — المرفوض لا سجل ولا صف ولا حدث ولا تشغيل):
  - مهام المستأجر المعلّقة بلغت `AGOS_TASK_QUEUE_MAX_PER_TENANT` (افتراضي 1000) → **429** «task queue full for this tenant: N pending task(s) …; retry later» + `Retry-After` (`AGOS_TASK_RETRY_AFTER_S`، افتراضي 5).
  - مهام الخدمة المعلّقة بلغت `AGOS_TASK_QUEUE_MAX` (افتراضي 10000؛ في الوضع المتطاير لا يتجاوز 500 = ما تحفظه الذاكرة) → **503** «task queue full: the service is at capacity; retry later» + `Retry-After` — لا يذكر أي مستأجر.
  - `AGOS_TASK_ACCEPT_RATE_PER_MIN` (معطّل افتراضياً) → **429** «task acceptance rate exceeded for this tenant …» + `Retry-After` محسوب.
  - إعادة الإرسال بمفتاح `idempotency_key` مقبول سابقاً تُجاب دائماً `202` + `duplicate: true` حتى والطابور ممتلئ.
  - الوضع المتين: تخزين لا يجيب (أو قاطع `storage` مفتوح) → 503 «storage unavailable: the task was not accepted» كما في R-163، والجديد `Retry-After` (المدة المتبقية للقاطع إن كان مفتوحاً).
- **حد المعدل العام** (`AGOS_RATE_LIMIT_REQUESTS`/`_WINDOW`): 429 بالجسم نفسه، والجديد `Retry-After` = الثواني حتى يخرج أقدم طلب من النافذة.
- **`POST /api/tasks/{id}/cancel`**: في الوضع المتطاير صار يلغي **التشغيل الحي** أيضاً (لا السجل فقط)؛ 503 «cancel not confirmed» صار يحمل `Retry-After`.
- **مهلة التنفيذ** `AGOS_TASK_TIMEOUT_S` (افتراضي 600): المهمة التي تتجاوزها تنتهي `failed` بالنتيجة `{"error": "task timed out after Ns (AGOS_TASK_TIMEOUT_S)"}` (شكل `agent_outcome` v1)، ولا يُعاد تشغيلها؛ وكذلك `/api/chat`.
- **عقد داخلي `llm_result` v1**: حقلان اختياريان جمعيان `busy` (رفض قبول: طابور بلا مفتاح/حد التزامن/قاطع مفتوح — كان يُرجع قبل R-164 ولم يكن في النموذج الصارم) و`retry_after` (ثوانٍ). مصنّف R-160: جمعي، صفر كسر — لا رفع نسخة.
- **`/metrics`**: مقاييس جديدة `pool_threads`/`pool_waiting`/`pool_busy_rejections` (وسم `pool`)، و`breaker_state` (0 مغلق/1 نصف مفتوح/2 مفتوح) و`breaker_opened`/`breaker_rejected` (وسم `service`)، و`tasks_pending`.

### R-164 — إصلاحات مراجعة PR #189 (D-083، 2026-10-18)
- **لا تغيير في أي عقد أو جسم رد.** التغيير سلوكي تحت التزامن فقط:
  - `AGOS_TASK_ACCEPT_RATE_PER_MIN` صار يصمد أمام الطلبات المتزامنة في الوضعين: الطلب المقبول للفحص يحجز حصته فوراً، والطلب الجاري يحتجزها حتى ينتهي — فقد يُرفض طلب بـ429 بينما طلبات أخرى جارية ثم تفشل؛ الحصة المحتجزة تُعاد عند فشل التخزين (503) أو تحوّل الطلب إلى إعادة إرسال (`duplicate: true`)، ولا تُحسب إعادة الإرسال أبداً.
  - بعد 429/503 مع `Retry-After` من المزود لا يصل أي طلب إلى المزود قبل انقضاء المدة، حتى لو وصلت ردود ناجحة متأخرة لطلبات سابقة؛ والطلبات المنتظرة لمقعد المزود بمفتاح تُرد `busy` + `retry_after` إذا فُتح القاطع أثناء انتظارها.
- **`/metrics`**: مقياس جديد `breaker_stale_outcomes` (وسم `service`) — نتائج نداءات قُبلت قبل تغيّر حالة القاطع فتُهمل.
