# التشغيل والنشر (OPERATIONS)

> **آخر تحديث**: 2026-10-12 — بعد دفعة M4/1 (R-040)

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
npm install && npm run build   # ناجح منذ M2/4 (rc=0)
```

## النشر

### Docker (من `Dockerfile`)
- مراحل: `node:20-slim` لبناء `web/` ثم `python:3.12-slim` للتشغيل؛ يُخدم `web/dist` عبر `server.py`.
- **`.dockerignore`** (جديد في R-040): يمنع دخول `.git` و`node_modules` و`web/dist` والأسرار (`.env*`) في سياق `COPY . .` — كانت تغيب قبله فكان سياق البناء يشمل كل ملفات العمل.
- **التحقق (R-040، ✓ done — دفعة M4/1)**: job `docker` في CI تبني الصورة وتشغلها وتتحقق من `/health` (HTTP 200 + جسم JSON يحوي `status`)، مع جمع السجلات ورفعها كـ artifact. تحققت في تشغيلين: الأول (run 38285632672) على رأس `f02ed61` والثاني/النهائي (run 38285911497) على رأس `13fc37d` — كلاهما نجح. الأدلة مُرتكزة في `docs/audit/M4/logs/05..09`. بيئة التحرير المحلية بلا Docker (دليل `docs/audit/M4/logs/04`).

### Render (من `render.yaml` — محدث R-041)
- `PYTHON_VERSION=3.12.0` (يستهلكه Render لا كود التطبيق — Q-007)، `startCommand=uvicorn server:app`، `healthCheckPath=/api/system/status`.
- `AGENTS_PER_SPEC=3`: **يُقرأ من الكود منذ R-041** (كان متغيراً ميتاً في الإعدادات). ملاءمة القيمة 3 للموارد لم تُقس — Q-013.
- مدخلا `AGOS_API_KEY` و`AGOS_ALLOWED_ORIGINS` أُضيفا كـ`sync: false` (المشغّل يدخل القيم في لوحة Render؛ لا سر في المستودع).
- `VITE_SUPABASE_*` بدائل خلفية يقرأها `supabase_store.py`؛ الواجهة `web/` لا تقرأها.

### CI (`ci.yml`)
- الوظائف: quality (ruff F/E722 على المسارات الحية) + test (pytest tests/) + build (web/) + docker (R-040: بناء الصورة وتشغيلها والتحقق من `/health`) + publish (معطل إلا بـ`[publish]` — D-011).
- `publish` يرفع إلى PyPI عند push لـ main مشروطاً بنجاح الوظائف السابقة ووسم `[publish]` في رسالة الدمج (D-011) — راجع RISK-009.

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

**محدث R-041**: كل متغيرات `.env.example` موثقة ومقروءة في الكود (بما فيها `CIRCUIT_BREAKER_*` و`AGENTS_PER_SPEC` و`REDIS_URL` و`METRICS_NAMESPACE` و`VITE_API_BASE`).

## الفحوصات

`/health`, `/health/live`, `/health/ready`, `/metrics`, `/api/system/status` — مع عيب موثق: `/health` يعيد 200 دائماً (R-004).

## الفجوات

1. لا `docker-compose.yml` ولا `Makefile`.
2. `DEPLOY.md` لم يُدقق في M0 — يحتاج مراجعة وتحديثاً.
3. لا مراقبة خارج المقاييس المدمجة.
