# الجودة والاختبارات (QUALITY)

> **آخر تحديث**: 2026-10-12 — بعد دفعة M4/1 (R-040)

## وضع الاختبارات (الخط الأساس، Python 3.14.3)

`pytest tests/`: **34 ناجح، 9 فاشل، 18 خطأ إعداد** ([log 02](../audit/M0/logs/02_pytest_tests.log)).

- الـ18 خطأ إعداد كلها في `test_api.py` وتحدث في مرحلة setup عند استيراد `server.py` (خطأ `utc`) — ليست أخطاء جمع.
- الإخفاقات التسع بأربعة أسباب جذرية ([log 03](../audit/M0/logs/03_pytest_infra.log)):

| السبب | الاختبارات |
|---|---|
| `CircuitBreaker` لا يقبل `failure_threshold` مباشرة | 6 |
| `metrics.py:243` تصدير Prometheus | 1 |
| `health` رسالة الاستثناء | 1 |
| `redis_queue.py:406` `len()` على Queue | 1 |

### تجربة تشخيصية (ليست خط الأساس)
بعد تعديل مؤقت لسطر الاستيراد: 51 ناجح، 10 فاشلة. أُعيد الملف لأصله. لم تُتتبع أسباب الإخفاقات العشرة فيها؛ تُحسم عند تنفيذ R-001. بنود R-003a-d مبنية على تحليل سجل 03 (اختبارات البنية التحتية) وحده.

## وضع Lint

| النطاق | الأخطاء | الدليل |
|---|---|---|
| المسارات الحية (بدون stubs) | **0** (محدث R-041) | [log 15](../audit/M4/logs/15_r041_ruff.log) |

- الأرقام القديمة (8,961 على `agos-kernel/`، 525 على المسارات الحية) تخص خط أساس M0 التاريخي؛ المسارات الحية صُلحت في M1/M2، و`agos-kernel/` أُرشف وحُذف لاحقاً (D-026).
- `mypy` و`black` و`isort`: لم تُشغَّل — فجوة موثقة في M0.

## وضع الاختبارات (محدث R-041)

- `pytest tests/`: **78/78 ناجح** (75 سابقة + 3 جديدة في `tests/test_agents_per_spec.py` — اختبار توصيل `AGENTS_PER_SPEC`: بلا متغير → 1000 وكيل؛ بـ3 → 60؛ صفر/سالب/نص → رفض). الدليل: [log 14](../audit/M4/logs/14_r041_pytest.log) (rc من الفحص مباشرة).

## وضع CI

- أهداف `ci.yml` الحالية: quality (ruff F/E722 على المسارات الحية) + test (pytest tests/) + build (web/) + docker (R-040، جديد: بناء الصورة من Dockerfile الفعلي + تشغيل الحاوية + تحقق `/health` 200) + publish (معطل إلا بـ`[publish]` — D-011).
- بوابة الحوكمة (`governance-gate.yml`) تعمل على كل PR إلى main (R-020)؛ غير مفروضة دون حماية الفروع (R-024 blocked).
- job `docker` الجديدة تجمع سجلات البناء والتشغيل والحاوية وترفعها كـ artifact `docker-verification-logs`؛ نجاحها الأول يُوثق بأدلة مرتكزة في `docs/audit/M4/logs/` (خطوة ما بعد أول تشغيل للـ PR).

## Definition of Done

- البند موجود في ROADMAP وحالته محدثة.
- PROJECT_STATE محدث.
- الوثائق المتأثرة محدثة.
- دليل التحقق مرفق.
- القرارات موثقة إن وجدت.
- كل ذلك في نفس الدفعة.

## قاعدة الدفعة الذرية

راجع [GOVERNANCE_GATE.md](GOVERNANCE_GATE.md). أي دفعة (كود/إعدادات/بيانات/تحليل/قرار/توثيق) تتضمن التغيير + الخارطة + الحالة + الوثائق المتأثرة + الأدلة معاً.

## فجوات الاختبار

1. لا اختبارات للمصادقة وCORS وrate limiting (المصادقة مغطاة جزئياً في test_security.py بعد R-004).
2. لا اختبارات واجهة `web/`.
3. ~~لا تحقق حي من Supabase~~ — التخزين مُتحقق حياً على PostgreSQL + PostgREST في CI job `storage` (R-042، M7/6)؛ Supabase المستضافة نفسها غير مختبرة (R-042b).
4. ~~فحص Docker داخل CI جديد لم يثبت نجاحه بعد~~ — تحقق وناجح (R-040 ✓ M4/1 — تشغيلان ناجحان على رأسي `f02ed61` و`13fc37d`).

## بوابة الحوكمة (R-020)

- `.github/workflows/governance-gate.yml` يعمل على كل PR إلى `main`.
- يفشل إذا لم يُحدَّث في نفس الـ PR: ROADMAP وPROJECT_STATE وCHANGELOG (دائماً، بما فيها التوثيق الخالص)، وأدلة `docs/audit/` عند تغيير الكود، وAPI.md عند تغيير `server.py` أو مسارات civilization، وDATA_MODEL.md عند تغيير التخزين، وQUALITY.md عند تغيير workflows.
- الحد المعروف: يفحص دفعة الـ PR مجمّعة لا كل commit، ولا يفرض شيئاً دون حماية فروع.
- اختبار محلي: `docs/audit/M3/logs/gate_test_harness.sh` (حالتان إيجابيتان وأربع سلبيات).

> تحديث M3/11: الأرقام التاريخية أعلاه عن `agos-kernel/` (8,961 خطأ ruff، 4 أخطاء جمع، انتهاء المهلة) تخص دليلاً حُذف من main في R-033 (D-026) ومحفوظ في `archive/agos-kernel-v1`. لا يغطي أي هدف CI حالياً.

## تحديث R-051 (M5/1)

- `pytest tests/`: **84/84 ناجح** (78 + 6 جديدة في `tests/test_real_dispatch_audit.py` — حماية من انحدار القياس).
- سكربت القياس `scripts/run_r051_audit.py` مشمول في فحص ruff (صفر أخطاء).

## تحديث R-032a (M5/3)

- `pytest tests/`: **141/141 ناجح** (117 + 24 جديدة في `tests/test_specialists_package.py` — حماية واجهة حزمة specialists).
- **أُزيل استثناء `agent_civilization/agents/specialists` من فحص ruff في CI**: الحزمة كانت تحمل 3,020 خطأ F401/F403 كامناً؛ أُصلحت (إعادة تصدير صريحة + `__all__` + إزالة استيرادات غير مستخدمة) وصار فحص CI يغطيها (صفر أخطاء — دليل `../audit/M5/logs/09`).

## تحديث R-045 (M6/7)

- **CI job `audit` جديد** (`.github/workflows/ci.yml` — needs quality+test، قبل publish):
  - `pip-audit -r requirements.txt` — مانع (يكسر CI عند أي ثغرة معروفة في تبعيات Python).
  - `npm audit --omit=dev --audit-level=critical` (web/) — مانع لثغرات critical/high في تبعيات **الإنتاج** فقط (react/react-dom).
  - `npm audit` الكامل — معلوماتي (`continue-on-error: true`) لأن تبعيات dev (vite/esbuild) لا تصل إلى الإنتاج.
- القياسات الحالية: pip-audit صفر ثغرات (logs/19)؛ npm --omit=dev صفر (logs/20)؛ الثغرتان dev (esbuild/vite) موثقتان ومبررتان في SECURITY.md.
- الاختبارات: `pytest tests/`: **174/174 ناجح** (بعد R-055).

## تحديث R-032 (M7/4)
- `pytest tests/`: **184/184** (175 + 9 في `tests/test_specialist_runtime.py`: المساران، فشل القيمة الخاطئة، 1000 صنف مستخدم فعلاً، التخصصات العشرون، نقل الفشل في BaseAgent (مميِّز — يفشل قبل الإصلاح، logs/07)، خط المعالجة، الرسائل عبر hub مشترك، مسارات الخادم الحية).

## تحديث R-065 (M6/5)

- **تصحيح أمر CI `audit`**: `npm audit --omit=dev --audit-level=critical` → `--audit-level=high` (والمسار الكامل `--audit-level=high` أيضاً) — الأمر كان يخالف معيار R-060 المكتوب في ROADMAP وتعليق job نفسه؛ لا قرار D مستقل عن العتبة، فالتصحيح أعاد الأمر للمطابقة مع المعيار (لا العكس — خفض المعيار ليوافق الأمر ممنوع).
- **سكربت القياس `scripts/run_r053_scale_audit.py`**: صار يضبط رمز خروج غير صفري (1) عند نجاح أقل من 100% — القياس يفشل عند الفشل. اختبار mock يثبت السلوك (logs/25). الفحص الحالي يُخرج 1000/1000 برمز 0.
- القياسات الحالية: pytest **184/184**، ruff صفر، البناءان rc=0، pip-audit صفر، npm audit --omit=dev --audit-level=high صفر (الجذر وweb/)، scale audit 1000/1000 (rc=0). الدليل: logs/25.

## تحديث R-042 (M7/6)
- **CI job `storage` جديد** (`.github/workflows/ci.yml` — needs quality+test، مانع): خدمة `postgres:17`، PostgREST v12.2.3 بتحقق sha256، ثم `scripts/r042/run_local_stack.sh`: تطبيق المخطط، ضابط سلبي (JWT غير صالح → 401)، `scripts/run_r042_storage_verify.py` (22 فحصاً؛ رمز خروج 1 عند أي فشل)، و`tests/test_r042_live_storage.py` (3 اختبارات خادم مع التخزين).
- `pytest tests/` الافتراضي: 189 ناجحاً + 3 متخطاة (الاختبارات الحية تتطلب `R042_LIVE=1`) = 184 سابقة + 5 في `tests/test_storage_payloads.py` (مميِّزة: 5/5 تفشل على الكود السابق — logs/14).
- محلياً في هذه الدفعة فشلت 3 اختبارات للباحث `researcher` بسبب مهلة SSL إلى DuckDuckGo (غير قابل للوصول من بيئة الوكيل — `curl` → 000)؛ تكرر الفشل نفسه على الشجرة قبل التغيير (logs/16). الحَكَم: job `test` في CI.
