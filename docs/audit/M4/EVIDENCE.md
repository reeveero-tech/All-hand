# أدلة M4 — R-040 (التحقق من بناء Docker وتشغيل الحاوية)

> **SHA عند بدء الدفعة**: `491fda0` (main بعد دمج PR #47)
> **التاريخ**: 2026-10-12 — الدفعة M4/1

## ماذا يتحقق هذا البند (R-040)

معيار القبول: `docker build` ينجح، الحاوية تبدأ، `/health` يرد 200، سجل مرفق.

## خط الأساس المحلي (قبل الدفعة)

| الفحص | النتيجة | الدليل |
|---|---|---|
| pytest tests/ | 75/75 ناجح، exit 0 | `logs/01_pytest_baseline_r040.log` |
| ruff (F,E722) على المسارات الحية | صفر أخطاء، exit 0 | `logs/02_ruff_baseline_r040.log` |
| بناء web/ | ناجح، exit 0 | `logs/03_web_build_r040.log` |
| CI على main | نجاح (تشغيل 38281756588) | `gh run list` |
| Docker محلياً | غير متوفر في بيئة التحرير | `logs/04_local_env_note_r040.log` |

ملاحظة صادقة: أول تشغيل لـ pytest في بيئة التحرير أعطى 17 فاشلاً بسبب غياب
`pytest-asyncio` (فشل بيئة، لا فشل مستودع — مفصل في `logs/04`). بعد تثبيته:
75/75 كما في CI.

## آلية التحقق من Docker (أين وكيف)

بيئة التحرير لا توفر Docker، لذا أُضيفت job `docker` إلى `.github/workflows/ci.yml`
(R-040): تبني الصورة من `Dockerfile` الفعلي (بعد `quality` و`test`)، تشغل الحاوية
على منفذ محلي 18000→8000، تنتظر بدءًا محدود المدة (حتى 30 ثانية)، وتتحقق من:

1. `docker build` ينجح (rc=0) وتُسجَّل السجلات كاملة.
2. الحاوية تبقى عاملة (خروج مبكر = فشل).
3. `/health` يعيد HTTP 200 تحديداً (ليس مجرد أي نجاح curl).
4. جسم الاستجابة يحتوي `"status"` (تأكيد أنه JSON الصحة لا fallback للواجهة).
5. سجلات الحاوية و`docker ps` و`docker inspect` تُجمع دائماً (نجاحاً أو فشلاً)
   وتُرفع كـ artifact باسم `docker-verification-logs` من كل تشغيل.

## أدلة التشغيل عن بُعد (تحققت — تشغيلان ناجحان على رأسي PR #48)

| البند | الحالة | الدليل |
|---|---|---|
| بناء الصورة | نجح (الصورة ~177MB) | `logs/05_docker_build_ci.log` |
| تشغيل الحاوية | نجح (`docker ps` يظهرها Up) | `logs/09_docker_ps_ci.log`، `logs/08_docker_inspect_ci.json` |
| استجابة `/health` | نجح: HTTP 200 في المحاولة الثانية (الأولى قبل اكتمال بدء uvicorn) | `logs/06_docker_runtime_ci.log` |

### قراءة صادقة للنتيجة
جسم `/health`: `status: degraded` — تخزين الذاكرة بدل Supabase (لا بيانات اعتماد في CI)، 1000 وكيل نشط، وuvicorn سجّل `GET /health 200`. `degraded` سلوك متوقع وموثق (D-012: degraded يعني 200 لأن بدائل الذاكرة تعمل؛ unhealthy وحده يعني 503).

التشغيل الأول: https://github.com/reeveero-tech/All-hand/actions/runs/38285632672 (job `docker`، pass، 17 ثانية، head_sha `f02ed61286010da5dfb5bd6fd470c2337709c820`). التشغيل النهائي على رأس PR بعد إرساء الأدلة: https://github.com/reeveero-tech/All-hand/actions/runs/38285911497 (pass، head_sha `13fc37d6e4f1d8f4eea7bda75510acf671a9c160`). الأصل: artifact `docker-verification-logs`. السجلات الخمسة (`05`..`09`) مُرتكزة من التشغيل الأول؛ التشغيل النهائي أعاد كل الفحوص (quality/test/build/docker/governance) خضراء على الرأس النهائي.

### تصحيح منهجية (سجل 10)
أكواد الخروج في السجلات المحلية `01..03` التُقطت أصلًا بعد pipe (tail)؛ أُعيد القياس في `logs/11..13` بأخذ rc من الفحص نفسه (كلها 0). لا تغيير في النتائج — 75/75 وصفر أخطاء وبناء ناجح.

## حد معروف (للأمانة)

- الفحص يتم في بيئة GitHub Actions (ubuntu-latest) لا في بيئة تشغيل الإنتاج؛
  هو تحقق من بناء الصورة وخدمة `/health` فقط — ليس نشراً حياً ولا إثبات
  جاهزية إنتاج (تبقى R-041..R-045).
- لا يتحقق هذا البند من تكامل Supabase الحي (R-042 blocked) ولا من واجهة
  `web/` مع API (R-043).

---

## R-041 (دفعة M4/3) — أدلة إضافية

| الفحص | النتيجة | الدليل |
|---|---|---|
| pytest tests/ (75 + 3 جديدة) | 78/78، exit 0 | `logs/14_r041_pytest.log` |
| ruff (F,E722) | صفر أخطاء، exit 0 | `logs/15_r041_ruff.log` |
| بناء web/ | ناجح، exit 0 | `logs/18_r041_web_build.log` |
| توصيل `AGENTS_PER_SPEC=3` | 60 وكيلاً (20×3) | `logs/16_r041_wiring_proof.log` |
| `GET /docs` | 200 | `logs/17_r041_docs_route.log` |

الاختبارات الجديدة (`tests/test_agents_per_spec.py`): 3 اختبارات بعمليات مستقلة — بلا متغير → 1000 وكيل؛ بـ3 → 60؛ صفر/سالب/نص → رفض برسالة بدء واضحة.
