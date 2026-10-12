# نشر حضارة الوكلاء على Render (مجاني)

> محدث في R-041 (دفعة M4/3) ليطابق الكود الفعلي: كل مسار هنا مُتحقق منه من
> `server.py` (بادئة `/api` للمسارات التطبيقية)، وكل متغير موثق يُقرأ في الكود
> أو يُستهلك من المنصة (`PYTHON_VERSION`). راجع `docs/governance/API.md` للمرجع الكامل.

## الخطوات

1. أنشئ حساباً مجانياً على [render.com](https://render.com)
2. اضغط **New → Blueprint** واربط مستودع GitHub هذا (`reeveero-tech/All-hand`)
3. Render سيقرأ `render.yaml` تلقائياً وينشئ الخدمة
4. أدخل قيمة `OPENROUTER_API_KEY` (اختياري — يفعّل التوجيه بالـLLM):
   - أنشئ مفتاحاً مجانياً من [openrouter.ai/keys](https://openrouter.ai/keys)
   - النموذج الافتراضي المعلن في الكود هو `meta-llama/llama-3.3-70b-instruct:free`
5. لتشغيل المصادقة: أدخل `AGOS_API_KEY` في لوحة Render (مدخل موجود في
   `render.yaml` كـ`sync: false`). تركه فارغاً = وضع مفتوح (سلوك R-004).
6. انتظر اكتمال النشر — رابطك سيكون مثل: `https://agos-civilization.onrender.com`

## التحقق

```bash
curl https://YOUR-APP.onrender.com/api/system/status
curl https://YOUR-APP.onrender.com/health
```

- `/health` يعيد 200 عند `healthy` و`degraded` (وضع بديل الذاكرة)، و503 عند `unhealthy` فقط (D-012).

## نقاط النهاية (بادئة `/api` للتطبيقية — مُتحقق من الكود)

| Endpoint | الوظيفة |
|---|---|
| `GET /api` | معلومات API |
| `GET /health` | صحة النظام (بلا بادئة) |
| `GET /health/live` و`GET /health/ready` | liveness/readiness (بلا بادئة) |
| `GET /metrics` | مقاييس Prometheus (بلا بادئة) |
| `GET /api/system/status` | صحة الحضارة (healthCheckPath في render.yaml) |
| `GET /api/agents` | قائمة الوكلاء (`?specialization=` اختياري) |
| `GET /api/specializations` | التخصصات الـ20 |
| `POST /api/tasks` | إرسال مهمة `{specialization, prompt, data?}` — 202 |
| `GET /api/tasks` و`GET /api/tasks/{id}` | جلب المهام |
| `GET /api/events` و`GET /api/events/stream` | الأحداث وSSE |
| `POST /api/chat` | أمر بلغة طبيعية → توجيه تلقائي |
| `POST /api/agents/external` و`DELETE /api/agents/external/{name}` | وكلاء خارجيون (يتطلب مفتاحاً إن فُعّلت المصادقة) |
| `POST /civilization/plan` و`POST /civilization/execute` و`GET /civilization/registry` | مسارات الحضارة (بلا بادئة `/api`) |
| `GET /docs` | توثيق تفاعلي (Swagger — مُتحقق: 200) |

## متغيرات البيئة (كلها مقروءة في الكود)

| المتغير | الوصف | الافتراضي |
|---|---|---|
| `AGENTS_PER_SPEC` | عدد الوكلاء لكل تخصص (يُقرأ منذ R-041) | `50` (أي 1000 وكيل) |
| `OPENROUTER_API_KEY` | مفتاح LLM (للذكاء الحقيقي) | — |
| `LLM_MODEL` | النموذج المستخدم | `meta-llama/llama-3.3-70b-instruct:free` |
| `AGOS_API_KEY` | مفتاح المصادقة (فارغ = وضع مفتوح) | — |
| `AGOS_ALLOWED_ORIGINS` | قائمة CORS (غير مضبوط = `*` بلا credentials) | `*` |
| `AGOS_RATE_LIMIT_REQUESTS` / `AGOS_RATE_LIMIT_WINDOW` | تحديد معدل الطلبات | `100` / `60` |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | تخزين دائم (فارغ = ذاكرة مؤقتة، حالة `degraded`) | — |
| `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` | بدائل خلفية يقرأها `supabase_store.py` (الواجهة لا تقرأها) | — |
| `REDIS_URL` | طابور Redis (فارغ = بديل داخلي) | `redis://localhost:6379` |
| `CIRCUIT_BREAKER_*` | ضبط القاطع (4 متغيرات) | `5/3/60/3` |
| `EXTERNAL_AGENTS` | JSON لوكلاء خارجيين عند الإقلاع | — |

`PYTHON_VERSION` في `render.yaml` يستهلكه Render (المنصة) لا كود التطبيق.

ملاحظة على `AGENTS_PER_SPEC=3` في `render.yaml`: قيمة موجودة سابقاً؛ صارت
مؤثرة فعلاً منذ R-041. ملاءمتها للموارد (الخطة المجانية) لم تُقس — راجع Q-013.

## التشغيل محلياً

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-...   # اختياري
uvicorn server:app --reload
```

الواجهة: `cd web && npm install && npm run build` ثم تُخدم تلقائياً من `web/dist`.
