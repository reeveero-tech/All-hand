# الواجهات (API)

> **آخر تحديث**: 2026-10-16 — **R-142 (BUG-03)**: `GET /health/ready` يرد **503** `{"status": "not_ready", "reason": …}` في كل حالة عدم جاهزية (كان 200)؛ الجاهزية الناجحة 200 بنفس الجسم؛ سبب الاستثناء ثابت «readiness check failed» (كان يعيد نص الاستثناء). `/health` و`/health/live` دون تغيير. قبلها **R-141 (BUG-02)**: `POST /api/chat` — رمز HTTP وبنية الاستجابة بلا تغيير؛ حالة المهمة الناتجة واحدة في الذاكرة والتخزين (`completed`/`failed` من `outcome.ok`؛ كان التخزين `completed` دائماً)، وحدث `chat_completed` يحمل `status` بجانب `ok`. قبلها **R-140 (BUG-01)**: `POST /api/tasks` بـ`specialization=external` حين لا يوجد وكيل خارجي مسجّل يرد **409** (بعد المصادقة وقبل أي أثر جانبي) بدل 202 ثم مهمة عالقة `queued`. قبلها **R-139 (SEC-03، D-057)**: لا تغيير في المسارات ولا في ردودها؛ `server.py` يستدعي `tenancy.enforce_startup` عند الاستيراد — في الإنتاج (`AGOS_ENV` غير مضبوط أو `production`) بلا بيانات اعتماد صالحة لا يقلع الخادم إطلاقاً (فلا ترد أي مسار، ولا `/health`). قبلها **R-138 (SEC-02، D-056): متعدد المستأجرين** — كل مسار يقرأ أو يغيّر بيانات مستأجر (المهام، الذاكرة، التعلم، الأحداث، تدفق SSE، `/api/chat`، `/civilization/execute`) يتطلب `X-API-Key` صالحاً يربطه الخادم بمستأجر (`AGOS_TENANT_KEYS` أو `AGOS_API_KEY`=المشغّل/admin)؛ المفتاح الخاطئ 401 (كان 403)؛ مهمة مستأجر آخر 404 كالمفقودة؛ `?all_tenants=true` للدور admin فقط (وإلا 403)؛ بلا إعداد مفاتيح كل هذه المسارات 401. تسجيل/حذف الوكلاء الخارجيين للدور admin. قبلها 2026-10-15 — R-132 (مجمّعات الخيوط؛ لا تغيير في العقد). قبلها R-131 (توجيه `/api/chat` مع LLM بلا مفتاح). قبلها 2026-10-14 — بعد R-104c2 (مسار النص في `data_processor`: الحمولة تصل LLM بالتعليمة **والبيانات** معاً — راجع الملاحظات)
> (تحديث سابق: 2026-10-12 — R-041: مصادلة مع الكود الفعلي وتصحيح أوصاف الحماية القديمة)
> **المصدر**: قراءة مباشرة من `server.py` و`agent_civilization/civilization_routes.py`

الإجمالي: **23 تعريف مسار** — 20 تعريفاً صريحاً في `server.py` (بما فيها catch-all؛ الثلاثة الأخيرة 21..23 أضافها R-103 وأُبقي ترقيم ما قبلها) + 3 تعريفات ديناميكية.

**تنبيه مهم**: تعريف catch-all وتركيب `/assets` مشروطان بوجود `web/dist` (راجع `server.py:635-637`)؛ لذا عدد المسارات **المسجلة فعلياً** وقت التشغيل يعتمد على وجود الواجهة المبنية. العدد أعلاه هو عدد التعريفات في الكود.

## المسارات الصريحة

| # | المسار | الفعل | الحماية | ملاحظات |
|---|---|---|---|---|
| 1 | `/api` | GET | لا | معلومات API |
| 2 | `/health` | GET | لا | يعيد 503 عند `unhealthy` فقط (أُصلح في R-004؛ `degraded` يعني 200 — D-012) |
| 3 | `/health/live` | GET | لا | liveness |
| 4 | `/health/ready` | GET | لا | readiness: **200** `{"status": "ready", "agents": N, "specializations": 20}` فقط حين الحضارة مهيأة وبها وكيل واحد على الأقل؛ وإلا **503** `{"status": "not_ready", "reason": …}` — `Civilization not initialized` / `No agents available` / `readiness check failed` (استثناء أثناء الفحص؛ السجل الداخلي يذكر نوع الاستثناء فقط) — R-142، BUG-03 (كان 200 في الحالات الثلاث) |
| 5 | `/metrics` | GET | لا | Prometheus (تصدير صحي — أُصلح في R-003b) |
| 6 | `/api/system/status` | GET | لا | healthCheckPath في render.yaml؛ SEC-02: كتلة `experience` إعدادات فقط (`enabled`, `max_entries`, `persistence_file`, `scope`) — لا عدادات ذاكرة/تعلّم؛ العدادات التشغيلية الإجمالية (`tasks_*`، `tasks_in_db`) أعداد بلا محتوى |
| 7 | `/api/agents` | GET | لا | `?specialization=` اختياري |
| 8 | `/api/specializations` | GET | لا | |
| 9 | `/api/tasks` | POST | `require_principal` (SEC-02): 401 بلا مفتاح/بمفتاح خاطئ/بلا إعداد مفاتيح | 202 Accepted؛ المالك = مستأجر المفتاح (أي `tenant_id` في الجسم أو الترويسات يُتجاهل)؛ 400 لتخصص مجهول (العقد كما هو)؛ **409** لـ`external` بلا وكيل خارجي مسجّل (R-140، BUG-01): `no external agent is registered: an 'external' task cannot run now (an admin registers one via POST /api/agents/external)` — بعد المصادقة (المجهول 401 دائماً) وقبل إنشاء `task_id` أو أي كتابة أو حدث أو SSE أو تشغيل خلفي |
| 10 | `/api/tasks` | GET | `require_principal` (SEC-02) | مهام مستأجر المتصل فقط؛ `?limit=`، `?all_tenants=true` (admin فقط، وإلا 403)؛ يعود للذاكرة إذا فشل Supabase؛ مع التخزين `source=database` و`result`/`event_data` كائنات JSON (R-042)؛ صفوف بلا `tenant_id` لا تظهر لمستأجر |
| 11 | `/api/tasks/{task_id}` | GET | `require_principal` (SEC-02) | 404 `task not found` إن لم توجد **أو كانت لمستأجر آخر** (رد مطابق لا يكشف الوجود)؛ `?all_tenants=true` admin فقط |
| 12 | `/api/events` | GET | `require_principal` (SEC-02) | أحداث مستأجر المتصل فقط (`tenant_id=eq.` في PostgREST)؛ `?all_tenants=true` admin فقط |
| 13 | `/api/events/stream` | GET | `require_principal` (SEC-02) | SSE من طابور مستأجر المتصل فقط؛ الواجهة تقرؤه بـfetch مع `X-API-Key` (`openEventStream`) لأن EventSource لا يرسل ترويسات |
| 14 | `/api/chat` | POST | `require_principal` (SEC-02) | توجيه تلقائي: LLM إن توفر **بمفتاح** وإلا `routing.route()` (R-106؛ R-131: لا توجيه عبر LLM بلا مفتاح)؛ حقل `routing`. 200 دائماً بنفس الحقول (`task_id`, `routed_to`, `routing`, `agent`, `outcome`)؛ المهمة المسجلة `completed` أو `failed` حسب `outcome.ok` في `TASKS` والتخزين وحدث `chat_completed` (`status`) معاً (R-141، BUG-02) |
| 15 | `/api/agents/external` | POST | `require_admin` (SEC-02): 401 بلا مفتاح صالح، 403 لدور user | 201؛ الوكيل الخارجي مشترك بين المستأجرين (يستقبل مهام `external` للجميع) فالتسجيل للمدير؛ المدير قد يستهدف شبكة خاصة كما كان حامل `AGOS_API_KEY` (R-100) |
| 16 | `/api/agents/external/{name}` | **DELETE** | `require_admin` (SEC-02) | |
| 17 | `/{full_path:path}` | GET | لا | catch-all يخدم `web/dist` |
| 21 | `/api/memory` | GET | `require_principal` (SEC-02) | R-103: `?q=&specialization=&limit=` (≤100) — ذكريات مهام مستأجر المتصل فقط؛ `stats` للمستأجر؛ `?all_tenants=true` admin (يضيف `tenant_id` لكل ذكرى و`quarantined_untenanted`) |
| 22 | `/api/learning` | GET | `require_principal` (SEC-02) | R-103: ما قيس من مهام مستأجر المتصل فقط (`tenant_id` في الرد)؛ `?all_tenants=true` admin |
| 23 | `/api/memory` | **DELETE** | `require_principal` (SEC-02): 401 بلا مفتاح أو بلا إعداد مفاتيح (كان 403) | يمسح ذاكرة وتعلّم مستأجر المتصل فقط؛ `?all_tenants=true` (admin فقط) يمسح الكل بما فيه المعزول |

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
- التحقق من المسارات مع تخزين حي: `tests/test_r042_live_storage.py` (POST/GET `/api/tasks`، `/api/events`، `/api/chat`، `/api/system/status`).

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
