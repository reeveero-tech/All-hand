# التشغيل والنشر (OPERATIONS)

> **آخر تحديث**: 2026-10-12

## البناء والتشغيل المحلي

### الواجهة (`web/`)
```bash
cd web && npm install && npm run build
```
- مُتحقق: نجاح في 902ms ([log 08](../audit/M0/logs/08_web_build.log)).

### الخادم
```bash
pip install -r requirements.txt
python -c "import server"   # يفشل حالياً على 3.12/3.14 (خطأ utc — R-001)
uvicorn server:app --host 0.0.0.0 --port 8000
```

### واجهة الجذر (TanStack)
```bash
npm install && npm run build   # يفشل: lovable-error-reporting مفقود (rc=1)
```

## النشر

### Docker (من `Dockerfile`)
- مراحل: `node:20-slim` لبناء `web/` ثم `python:3.12-slim` للتشغيل؛ يُخدم `web/dist` عبر `server.py`.
- **تنبيه**: `python:3.12-slim` + خطأ `utc` → الاستيراد يفشل وقت التشغيل. **بناء الصورة لم يُنفَّذ في بيئة التدقيق** (Docker غير متوفر) — فشل التشغيل استدلال.

### Render (من `render.yaml`)
- `PYTHON_VERSION=3.12.0`، `startCommand=uvicorn server:app`، `healthCheckPath=/api/system/status`.
- **تنبيه**: نفس استنتاج `utc` أعلاه.
- يحدد `AGENTS_PER_SPEC=3` (لا يُقرأ من الكود — الافتراضي في `create_civilization` هو 50) — راجع Q-013 في OPEN_QUESTIONS.

### CI (`ci.yml`)
- أهدافه (تاريخي، قبل R-002): lint/typing/اختبارات/بناء/نشر على `agos-kernel/` فقط. الحالي: انظر QUALITY (quality/test/build/publish على المسارات الحية؛ `agos-kernel/` حُذف في R-033).
- إعادة تنفيذ محلي فاشل (4 أخطاء جمع + 8,961 خطأ ruff). حالة التشغيل عن بُعد غير مُتحقرة.
- `publish` يرفع إلى PyPI عند push لـ main مشروطاً بنجاح الوظائف السابقة (تفشل حالياً) — راجع RISK-009.

## متغيرات البيئة (المرصودة في الكود)

| المتغير | المكان | الافتراضي |
|---|---|---|
| `AGOS_API_KEY` | server.py | `""` |
| `AGOS_ALLOWED_ORIGINS` | server.py | `"*"` |
| `AGOS_RATE_LIMIT_REQUESTS` | server.py | 100 |
| `AGOS_RATE_LIMIT_WINDOW` | server.py | 60 |
| `SUPABASE_URL` / `VITE_SUPABASE_URL` | supabase_store.py | `""` |
| `SUPABASE_ANON_KEY` / `VITE_SUPABASE_ANON_KEY` | supabase_store.py | `""` |
| `OPENROUTER_API_KEY` | core/llm.py | `""` |
| `LLM_MODEL` | core/llm.py | `meta-llama/llama-3.3-70b-instruct:free` |
| `EXTERNAL_AGENTS` | agents/external_adapter.py | `""` |
| `REDIS_URL` | agos_kernel/infrastructure/redis_queue.py | `redis://localhost:6379` |
| `METRICS_NAMESPACE` | agos_kernel/infrastructure/metrics.py | `agos` |
| `CIRCUIT_BREAKER_*` (4 متغيرات) | agos_kernel/infrastructure | 5/3/60/3 |

**غير مُوثق في `.env.example`**: `CIRCUIT_BREAKER_*`، `AGENTS_PER_SPEC`.

## الفحوصات

`/health`, `/health/live`, `/health/ready`, `/metrics`, `/api/system/status` — مع عيب موثق: `/health` يعيد 200 دائماً (R-004).

## الفجوات

1. لا `docker-compose.yml` ولا `Makefile`.
2. `DEPLOY.md` لم يُدقق في M0 — يحتاج مراجعة وتحديثاً.
3. لا مراقبة خارج المقاييس المدمجة.
