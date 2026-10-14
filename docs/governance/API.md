# الواجهات (API)

> **آخر تحديث**: 2026-10-12 — بعد R-041 (مصادلة مع الكود الفعلي وتصحيح أوصاف الحماية القديمة)
> **المصدر**: قراءة مباشرة من `server.py` و`agent_civilization/civilization_routes.py`

الإجمالي: **20 تعريف مسار** — 17 تعريفاً صريحاً في `server.py` (بما فيها catch-all) + 3 تعريفات ديناميكية.

**تنبيه مهم**: تعريف catch-all وتركيب `/assets` مشروطان بوجود `web/dist` (راجع `server.py:635-637`)؛ لذا عدد المسارات **المسجلة فعلياً** وقت التشغيل يعتمد على وجود الواجهة المبنية. العدد أعلاه هو عدد التعريفات في الكود.

## المسارات الصريحة

| # | المسار | الفعل | الحماية | ملاحظات |
|---|---|---|---|---|
| 1 | `/api` | GET | لا | معلومات API |
| 2 | `/health` | GET | لا | يعيد 503 عند `unhealthy` فقط (أُصلح في R-004؛ `degraded` يعني 200 — D-012) |
| 3 | `/health/live` | GET | لا | liveness |
| 4 | `/health/ready` | GET | لا | readiness |
| 5 | `/metrics` | GET | لا | Prometheus (تصدير صحي — أُصلح في R-003b) |
| 6 | `/api/system/status` | GET | لا | healthCheckPath في render.yaml |
| 7 | `/api/agents` | GET | لا | `?specialization=` اختياري |
| 8 | `/api/specializations` | GET | لا | |
| 9 | `/api/tasks` | POST | `verify_api_key` (401/403 إن فُعّل `AGOS_API_KEY`) | 202 Accepted |
| 10 | `/api/tasks` | GET | لا | `?limit=`, يعود للذاكرة إذا فشل Supabase؛ مع التخزين `source=database` و`result`/`event_data` كائنات JSON (R-042) |
| 11 | `/api/tasks/{task_id}` | GET | لا | 404 إن لم توجد |
| 12 | `/api/events` | GET | لا | |
| 13 | `/api/events/stream` | GET | لا | SSE |
| 14 | `/api/chat` | POST | `verify_api_key` (401/403 إن فُعّل `AGOS_API_KEY`) | توجيه تلقائي |
| 15 | `/api/agents/external` | POST | `verify_api_key` — يفحص قيمة المفتاح (R-004) | 201؛ 400 `url rejected` لعنوان غير عام في الوضع المفتوح (R-100) |
| 16 | `/api/agents/external/{name}` | **DELETE** | `verify_api_key` — يفحص قيمة المفتاح (R-004) | |
| 17 | `/{full_path:path}` | GET | لا | catch-all يخدم `web/dist` |

## المسارات الديناميكية (`civilization_routes.py`)

| # | المسار | الفعل | الحماية |
|---|---|---|---|
| 18 | `/civilization/registry` | GET | `verify_api_key` إن فُعّل (R-004/D-013) |
| 19 | `/civilization/plan` | POST | `verify_api_key` إن فُعّل (R-004/D-013) |
| 20 | `/civilization/execute` | POST | `verify_api_key` إن فُعّل (R-004/D-013) |

## ملاحظات مُتحققة

- واجهة التسجيل تطلب `name, url, model, api_key` (Pydantic `ExternalAgentRequest`).
- `POST /api/tasks` يقص الـ prompt إلى 500 حرف.
- `GET /docs` (Swagger) متاح ويستجيب 200 — مُتحقق فعلياً في R-041 (دليل M4/logs/17).
- `AGOS_AGENT_IMPL` (R-032): مع `specialists` تحمل لقطات الوكلاء (`/api/agents` ونحوها عبر `snapshot()`) حقلاً إضافياً `impl` (اسم الصنف، مثل `AnalystAgent0015`)، ونتيجة `execute` حقل `impl`. بقية الشكل مطابق. الافتراضي `real` بلا تغيير.
- `AGENTS_PER_SPEC` متغير بيئة يقرؤه `server.py` منذ R-041 (افتراضي 50) ويحدد عدد الوكلاء لكل تخصص.

## فجوات التوثيق

- لا يوجد OpenAPI مخصص خارج المولد الافتراضي من FastAPI.
- المصادقة على المسارات الحساسة تُفرض عبر `verify_api_key` (R-004)؛ الواجهة `web/` لا ترسل `X-API-Key` بعد (قيد موثق لـ R-043).

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
- `GET /api/system/status`: حقل جديد `security.tool_policy` = `{"code_execution": {"allowed", "reason", "limits", "wall_timeout_s", "server_process_non_dumpable"}, "outbound_network": {"private_targets_allowed"}, "sqlite": {"time_budget_s", "attach"}}` — لا قيمة سرية. الحقول الموجودة بلا تغيير.
- `POST /api/tasks` بـ`specialization=test_runner` في الوضع المفتوح: 202 كما كان، والنتيجة `{"ok": false, "error": "code execution is disabled in open mode: …", "tool_output": {"executed": false}}`. عند الإذن: `tool_output` يضيف `elapsed_s` و`sandbox: true`؛ القتل بحد العزل → `answer: "killed by signal N (sandbox limit)"`.
- `api_integrator`/`network_client` لهدف غير عام: `{"ok": false, "error": "blocked: …"}`؛ `network_client` (فحص DNS) يضيف `addresses` (كل العناوين المحلولة) بجانب `ip`.
- `db_manager`: `ATTACH`/`VACUUM INTO` → `sqlite: not authorized`؛ تجاوز 3 ثوانٍ → `sqlite: interrupted`؛ الحد 50 صفاً (كما كان).
- `POST /api/agents/external`: 400 `url rejected: blocked non-public address …` إذا كان العنوان داخلياً والخادم في الوضع المفتوح بلا `AGOS_ALLOW_PRIVATE_NETWORK=1`؛ 400 `url host cannot be resolved` لاسم لا يُحل.

