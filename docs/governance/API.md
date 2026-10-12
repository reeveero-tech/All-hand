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
| 10 | `/api/tasks` | GET | لا | `?limit=`, يعود للذاكرة إذا فشل Supabase |
| 11 | `/api/tasks/{task_id}` | GET | لا | 404 إن لم توجد |
| 12 | `/api/events` | GET | لا | |
| 13 | `/api/events/stream` | GET | لا | SSE |
| 14 | `/api/chat` | POST | `verify_api_key` (401/403 إن فُعّل `AGOS_API_KEY`) | توجيه تلقائي |
| 15 | `/api/agents/external` | POST | `verify_api_key` — يفحص قيمة المفتاح (R-004) | 201 |
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
- `AGENTS_PER_SPEC` متغير بيئة يقرؤه `server.py` منذ R-041 (افتراضي 50) ويحدد عدد الوكلاء لكل تخصص.

## فجوات التوثيق

- لا يوجد OpenAPI مخصص خارج المولد الافتراضي من FastAPI.
- المصادقة على المسارات الحساسة تُفرض عبر `verify_api_key` (R-004)؛ الواجهة `web/` لا ترسل `X-API-Key` بعد (قيد موثق لـ R-043).
