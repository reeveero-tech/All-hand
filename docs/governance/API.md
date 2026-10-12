# الواجهات (API)

> **آخر تحديث**: 2026-10-12 — M0
> **المصدر**: قراءة مباشرة من `server.py` و`agent_civilization/civilization_routes.py`

الإجمالي: **20 تعريف مسار** — 17 تعريفاً صريحاً في `server.py` (بما فيها catch-all) + 3 تعريفات ديناميكية.

**تنبيه مهم**: تعريف catch-all وتركيب `/assets` مشروطان بوجود `web/dist` (راجع `server.py:635-637`)؛ لذا عدد المسارات **المسجلة فعلياً** وقت التشغيل يعتمد على وجود الواجهة المبنية. العدد أعلاه هو عدد التعريفات في الكود.

## المسارات الصريحة

| # | المسار | الفعل | الحماية | ملاحظات |
|---|---|---|---|---|
| 1 | `/api` | GET | لا | معلومات API |
| 2 | `/health` | GET | لا | **عيب: يعيد 200 دائماً** (`status_code` يُحسب ولا يُمرر) |
| 3 | `/health/live` | GET | لا | liveness |
| 4 | `/health/ready` | GET | لا | readiness |
| 5 | `/metrics` | GET | لا | Prometheus (عيب في التصدير — R-003b) |
| 6 | `/api/system/status` | GET | لا | healthCheckPath في render.yaml |
| 7 | `/api/agents` | GET | لا | `?specialization=` اختياري |
| 8 | `/api/specializations` | GET | لا | |
| 9 | `/api/tasks` | POST | المفتاح يُستقبل ولا يُفحص | 202 Accepted |
| 10 | `/api/tasks` | GET | لا | `?limit=`, يعود للذاكرة إذا فشل Supabase |
| 11 | `/api/tasks/{task_id}` | GET | لا | 404 إن لم توجد |
| 12 | `/api/events` | GET | لا | |
| 13 | `/api/events/stream` | GET | لا | SSE |
| 14 | `/api/chat` | POST | المفتاح يُستقبل ولا يُفحص | توجيه تلقائي |
| 15 | `/api/agents/external` | POST | يفحص `ENABLE_AUTH` فقط | 201 |
| 16 | `/api/agents/external/{name}` | **DELETE** | يفحص `ENABLE_AUTH` فقط | |
| 17 | `/{full_path:path}` | GET | لا | catch-all يخدم `web/dist` |

## المسارات الديناميكية (`civilization_routes.py`)

| # | المسار | الفعل | الحماية |
|---|---|---|---|
| 18 | `/civilization/registry` | GET | لا |
| 19 | `/civilization/plan` | POST | لا |
| 20 | `/civilization/execute` | POST | لا |

## ملاحظات مُتحققة

- واجهة التسجيل تطلب `name, url, model, api_key` (Pydantic `ExternalAgentRequest`).
- `POST /api/tasks` يقص الـ prompt إلى 500 حرف.
- README يوثق `/civilization/plan` كـ POST — مطابق للكود.

## فجوات التوثيق

- لا يوجد OpenAPI مخصص أو ملف swagger.
- المخطط الميداني للطلبات/الاستجابات موثق أعلاه من قراءة الكود فقط؛ لم تُنفذ طلبات فعلية (الاستيراد يفشل).
