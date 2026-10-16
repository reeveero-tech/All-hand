# الجودة والاختبارات (QUALITY)

> **آخر تحديث**: 2026-10-16 — **R-146b (خمس جولات مراجعة SEC-05: +13 ثم +3 ثم +15 ثم +2 ثم +7 → 63 اختباراً في `tests/test_sec05_dns_rebinding.py` — الحزمة 822+4+4 (`260`؛ الجولات السابقة 795/798/813/815 في `256`/`257`/`258`/`259`))**. قبلها **R-146 (منع DNS rebinding — SEC-05: 23 اختباراً حتمياً محلياً في `tests/test_sec05_dns_rebinding.py` — الحزمة 782+4+4 (`255`)؛ المسبار قبل/بعد `253`/`254`)**. قبلها **R-145f (إتمام تصحيح دقة الأدلة — أربعة مواضع في R-145e لم تُستبدل فعلياً صُححت؛ الواجهة 31 تُنسب إلى `249` ووسمها R-145c)**. قبلها **R-145e (تصحيح دقة الأدلة — دفعة توثيقية: سجل 246 مستعاد أصلياً + سجل 249 + روابط CI فعلية + صياغة اللقطات والعدّ)**. قبلها **R-145d (تثبيت اختبار SEC-06 المتقطع — `client` fixture بعميل بسياق كامل؛ تطبيق اكتشاف D-060)**: فشل متقطع واحد على main (تشغيل 38862435979 — «task did not finish in time» لنفس التزام PR الناجح)؛ الاستقرار مثبت: 5 تششيلات متتالية 43/43 والحزمة 759+4+4 (`248`). قبلها **R-145c (فحص بصري + إصلاح خللين حقيقيين في واجهة SEC-06، D-061)**: الفحص البصري (playwright محلياً على خادم حقيقي) كشف أن تحذير R-145 يسحق حقل المحادثة — أُصلح (`flex-wrap` + صف داخلي، لقطات خام `247`)؛ وبقاء الموافقة عند التنقل بين نماذج المهام أُصلح؛ +2 اختبار انتقال (`web/tests/ui_consent.test.mjs` صار 10)؛ الحزمة 759+4+4 والواجهة 31 (`249`). قبلها **R-145b (جولة مراجعة SEC-06، D-060)**: +2 اختبارات بايثون (مستأجران عبر المسارات؛ حاجز الخلفية) + `web/tests/ui_consent.test.mjs` (8 اختبارات سلوك واجهة حقيقية في jsdom — المكونات الحقيقية مع api.js وfetch وهمي؛ jsdom/esbuild اعتماديتا تطوير فقط، صفر ثغرات)؛ الحزمة 759+4+4 والواجهة 29 (`246`)؛ لا فحص أُزيل أو خُفّف. قبلها **R-145 (SEC-06)**: `tests/test_sec06_public_llm_consent.py` (41 اختباراً: اختيار المزود في عمليات جديدة، بوابة النقل بعدّاد استدعاءات، حارس طبقة الشبكة، المسارات الحية الثلاثة، الخلفي، الطلبات المتزامنة، مسار OpenRouter، تنقية السجلات، عزل إعادة اختبار self_developer، عدم التسرب، العقد) + `web/tests/api.test.mjs` محدّث (21)؛ المجموعة 757+4+4 (`244`/`245`)؛ لا فحص أُزيل أو خُفّف. قبلها R-139 (SEC-03): خطوة docker جديدة «Verify production start is refused without credentials (SEC-03)» قبل «Run container»: تشغيل الصورة بلا مفاتيح يجب أن يخرج برمز ≠0 (وليس مهلة 124) مع «production start refused: no credentials configured»، وتشغيلها بـ`AGOS_ENV=development RENDER=true` يجب أن يُرفض بـ«not allowed on a deployed platform»؛ السجل `docker_startup_refusal.log` ضمن artifact. `tests/conftest.py` يضبط `AGOS_ENV=test` صراحةً؛ `tests/test_sec03_production_startup.py` (56 حالة). قبلها R-138 (SEC-02): خطوة docker «Verify runtime safety policy (R-100)» صارت تثبت أيضاً أن المجهول يُرفض (401 على POST/GET `/api/tasks`) ثم ترسل `test_runner` كمستأجر CI عشوائي لكل تشغيل (`AGOS_TENANT_KEYS`، دور user، بلا `AGOS_API_KEY`) وتثبت أن الكود لا يُنفَّذ؛ الوظيفة `storage` تطبّق `docs/sql/002_tenant_isolation.sql` وتفحص العزل حياً. عزل الاختبارات: fixture تلقائي `_fresh_rate_limit_window` في `tests/conftest.py` يفرغ نافذة rate limit قبل كل اختبار (كانت مشتركة بين الملفات — 95/100 على main، 429 متقطع في CI — `docs/audit/M7/logs/raw/224`). لا فحص أُزيل أو خُفّف. قبلها 2026-10-16 — R-136 (تصحيح قسم «الحالة الحالية»: كان ظل عند أرقام الدورة السادسة R-093 — 230+3+3 — ولم يُحدّث في الدورات 7–9؛ حُدّث إلى القيم المقيسة). قبلها 2026-10-12 — بعد دفعة M4/1 (R-040)

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
- بوابة الحوكمة (`governance-gate.yml`) تعمل على كل PR إلى main (R-020)؛ **مفروضة فعلياً منذ R-024** (حماية الفرع `main` مفعلة: quality/test/build/docker/governance/audit، strict). راجع GOVERNANCE_GATE.md.
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

## تحديث R-042b (M7/7)
- `pytest tests/` الافتراضي: 190 + 3 متخطاة (+1 في `tests/test_storage_payloads.py`: أولوية `SUPABASE_SERVICE_KEY`). محلياً 3 فشل شبكة `researcher` (كما في M7/6 — logs/23)؛ CI هو الحَكَم.
- التحقق الحي على Supabase مستضافة: 22/22 + 3/3 (logs/22). CI job `storage` بلا تغيير في ci.yml؛ السكربت صار يستخدم `service_role` ويتحقق أن `anon` مرفوض.

## تحديث R-068 (دورة M6 الثانية/2)
- `pytest tests/`: **192 + 3 متخطاة** (+2 في `tests/test_security.py`: `/health` 503 عند unhealthy، و200 عند degraded — كان فرع 503 بلا اختبار). مميِّز: حذف الفرع مؤقتاً يُفشل الاختبار (logs/31).
- القياسات الدورية كلها rc=0: R-051 20/20، R-053 1000/1000، R-055 3/3، R-042 22/22 + 3/3 (logs/29).
- RISK-019: اختبارات `researcher` تعتمد على DuckDuckGo — قاعدة "CI هو الحَكَم" عند فشل شبكي محلي؛ الإصلاح R-072.

## تحديث R-070
- **CI job `build`**: أضيفت للجذر (تطبيق TanStack Start في `src/`) ثلاث خطوات: `npm ci`، `npx eslint . --max-warnings=0`، `npm run build`. قبلها لم يكن CI يفحص الجذر ولا يبنيه، فبقي `npm run lint` معطلاً (rc=2) دون أن يُلاحظ.
- `eslint.config.js` جديد (flat config بالتبعيات الموجودة)؛ 18 ملفاً، 0 أخطاء، 0 تحذيرات؛ ضابط سلبي يلتقط خطأً حقيقياً (logs/34).

## تحديث R-071
- **CI job `audit`**: خطوة `npm audit --audit-level=high` الكاملة في web/ صارت **مانعة** (أزيل `continue-on-error: true` وأضيف `set -o pipefail`) — كانت معلوماتية منذ R-045 بسبب ثغرتي dev اللتين أغلقهما R-071.
- تحقق المتصفح `scripts/run_r071_web_verify.py`: 8/8 قبل وبعد؛ خادم التطوير 6/6 (logs/36). pytest 192 + 3؛ عقد الويب 10/10 (logs/37).

## تحديث R-072 (D-040)
- `pytest tests/` الافتراضي: **198 + 3 متخطاة + 3 مستبعدة** — لا يلمس الإنترنت (حماية `tests/conftest.py`). بلا شبكة إطلاقاً: 198/198 (كان 5 فشل — logs/38، logs/39).
- **CI job `network` جديد** (needs quality، **غير إلزامي**): `pytest tests/ -m network` — الوكلاء الثلاثة على الإنترنت الحقيقي.
- قاعدة "CI هو الحَكَم" لفشل `researcher` المحلي (M7/6، R-068) لم تعد لازمة للحزمة الافتراضية.

## تحديث R-076
- CI job `build`: خطوات الجذر على **Node 22** (`setup-node` ثانٍ) و`npm ci --engine-strict` — تعارض `engines` صار فشلاً لا تحذيراً. خطوة `web/` على Node 20 كما هي.
- الأدلة: Node 20 + engine-strict rc=1 (قبل وضابط سلبي)، Node 22 rc=0 + lint/build rc=0 (logs/44، logs/45).

## تحديث R-077
- CI job `audit`: خطوتان جديدتان مانعتان لتبعيات npm في الجذر (Node 22، `set -o pipefail`). على main: صفر؛ ضابط سلبي rc=1 (logs/46).

## الحالة الحالية (محدّثة بعد R-145، 2026-10-16)
> الأقسام أعلاه وأدناه سجل زمني؛ هذا القسم هو المرجع الحالي. (تصحيح R-136: الأرقام السابقة 230+3+3 كانت متقدمة على الواقع. بعد R-146b: 822+4+4 — `822 = 759 + 63 اختبار SEC-05` (`docs/audit/M7/logs/raw/260`)؛ بعد R-146: 782+4+4 — `782 = 759 + 23` (`255`)؛ بعد R-145c: 759+4+4 — `759 = 716 + 43 اختبار SEC-06` والواجهة 31 (21 + 9 سلوكية + تنظيف jsdom) مقيسة على 3.14.3 (`docs/audit/M7/logs/raw/249`)؛ بعد R-145b: 759 = 716 + 43 والواجهة 29؛ بعد R-145: 757 = 716 + 41 (`244`).)
- `pytest tests/`: **759 + 4 متخطاة + 4 مستبعدة** (`network`)، متصلاً ومعزولاً عن الإنترنت (R-072/R-078)، ومع/بلا `web/dist` (R-083)؛ محلياً على 3.14.3 (`docs/audit/M7/logs/raw/244`)؛ السابق 716+4+4 بعد R-144 على 3.14.3 و3.11.15 و3.12.13 وفي CI على 3.11 و3.12 (`242`)؛ `pytest -m network`: 4/4 حية في الدورة العاشرة (`201`) وفي CI job `network` (تشغيل main ‏38723795049: 4 passed).
- ruff `--select F,E722`: صفر — نطاق CI الحرفي `server.py agent_civilization agos_kernel tests` (`213`) ونطاق أمر التسليم مع استثناء specialists (`181`).
- Node: **22 في كل مكان** — الجذر وweb/ بـ`npm ci --engine-strict` (R-076، R-089)، وطور بناء Docker `node:22-slim` (D-042)؛ `npm run build` ناجح في الجذر وweb/ (`205`/`215`).
- بيئة CI: `runs-on: ubuntu-26.04` صراحةً (D-041)؛ كل الـActions على v7 (`node24`، صفر تنبيه — R-087).
- audit: pip-audit صفر؛ npm audit إنتاج وكامل صفر في web/ والجذر — كلها مانعة في CI (R-077).
- R-053 1000/1000 (1.271s، R-092)؛ R-051 20/20؛ R-055 3/3؛ R-042 22/22 + 3/3؛ `/metrics` محدود تحت 10000 طلب (`scripts/run_r085_metrics_load.py`).
- تشغيل الاختبارات محلياً بنفس تبعيات CI (`httpx2`): وصفة في OPERATIONS (R-091).

## درس R-083 — نسخة Python في التحقق المحلي
- CI يشغّل Python 3.11؛ صياغة 3.12+ (مثل `\` داخل تعبير f-string) تمر محلياً على نسخة أحدث وتفشل في CI. التحقق المحلي قبل الدفع: `python3.11 -m compileall -q server.py agent_civilization agos_kernel tests scripts` + pytest على 3.11 (logs/63b).
- كذلك job `test` لا يبني `web/` — مسار SPA (`/{full_path:path}`) غير مسجّل فيه؛ الاختبارات التي تعتمد عليه يجب أن تكيّف توقعها (R-083، logs/63c).

## R-088 — صورة المشغّل `ubuntu-26.04` (D-041)
- كل jobs في `ci.yml` و`governance-gate.yml`: `runs-on: ubuntu-26.04` صراحةً (كانت `ubuntu-latest` = ubuntu-24.04، logs/69). مختبرة: الـ7 jobs + governance ناجحة على الصورة 20260901.588، Python 3.11.16، Node 22.23.3 (logs/70). `publish` غير مُجرّب.
- تغيير الصورة لاحقاً = commit صريح مع تشغيل كامل (لا ترحيل صامت).

## R-087 — إصدارات GitHub Actions (Node 24)
- `actions/checkout@v7`، `actions/setup-node@v7`، `actions/setup-python@v7`، `actions/upload-artifact@v7` (كانت v4/v4/v5/v4 على Node 20، logs/71). صفر تنبيه "Node.js 20 is deprecated" في الـ8 jobs (logs/72).
- مراجعة دورية: تنبيهات CI جزء من فحص التبعيات في كل دورة (نمط R-084).

## R-089 — `web/` على Node 22 (D-042)
- jobs `build` و`audit`: `web/` على Node 22 بـ`npm ci --engine-strict` (كان Node 20 + `npm install`). كل خطوات Node في CI الآن Node 22 (logs/74).

## R-090 — Python 3.12 في job `test` (D-043)
- `test` يشغّل الحزمة مرتين: 3.11 (مع التغطية) ثم 3.12 (نسخة الإنتاج). اسم الفحص ثابت. باقي jobs Python على 3.11.

## R-100 — اختبارات أمان الأدوات وفحص الحاوية (D-044)
- **الافتراض في الاختبارات**: fixture تلقائي في `tests/conftest.py` يبدأ كل اختبار من الوضع الإنتاجي الافتراضي (يحذف `AGOS_API_KEY` و`AGOS_ALLOW_CODE_EXEC` و`AGOS_ALLOW_PRIVATE_NETWORK`) فيُرفض `test_runner`. الاختبارات التي تثبت التنفيذ الحقيقي تحمل `@pytest.mark.code_exec` (6 اختبارات قائمة + 3 جديدة) — الإذن نفسه الذي يعطيه المشغّل، مسجّل في `pytest.ini`.
- **النقل الوهمي الشبكي (R-072)**: صار يجيب `OpenerDirector.open` (يستخدمه `safety.safe_urlopen`) و`getaddrinfo` (يُرجع TEST-NET بدل الرفض) — DNS الحقيقي ما زال لا يُستعلم، والاتصال الخارجي الحقيقي ما زال يُرفض.
- **`tests/test_r100_runtime_safety.py`** (44): مصفوفة السياسة، عدم التسريب عبر API، عزل البيئة/المجلد/`-I`، حدود CPU والمهلة والذاكرة، منع قراءة `/proc/<ppid>/environ`، 12 عنواناً محظوراً و4 مسموحة، المخططات، خادم داخلي حقيقي على 127.0.0.1 (hits=0 افتراضياً، 1 مع العلم)، إعادة التوجيه، الوكيل الخارجي، SQLite (ATTACH، VACUUM INTO، CTE جامح، SQL عادي).
- **CI job `docker`**: خطوة جديدة «Verify runtime safety policy in the container (R-100)» على الحاوية المبنية فعلاً — `id -u` ليس 0، ومهمة `test_runner` مجهولة تُرفض بـ«disabled in open mode».
- العدد: 274 + 3 skipped + 3 deselected على 3.11 و3.12 (`logs/raw/44..45`؛ قبل المراجعة الذاتية 267 — `raw/29..30`). المراجعة الذاتية أضافت 7 اختبارات (NAT64، `randomblob` ضخم، UTF-8 غير صالح، حفيد هارب) — كل منها فشل على الكود السابق (`raw/43`).
- **تذبذب خارجي مسجّل**: R-053 بالإذن (1000 مهمة متزامنة، 50 منها `researcher` على DuckDuckGo الحي) أعطى 999/1000 (`raw/34`) و996/1000 (`raw/48`) ثم 1000/1000 (`raw/37`، `raw/38`، `raw/50`)؛ كل الإخفاقات `researcher` بخطأ SSL من الخدمة. مقارنة 50 طلباً متزامناً بالمسار القديم والجديد: 50/50 لكليهما في جولتين (`raw/49`) — لا دليل على أن حارس العناوين سببه.
- فحص الحاوية أُثبت على PR #108 (`raw/42`: `container_uid=10001`، `PASS: non-root container; open-mode code execution refused`).

## R-101 — اختبارات عميل الواجهة (Node) وعقد الوضع المصادَق
- **CI job `build`**: خطوة جديدة `Test web API client` = `cd web && npm test` (`node --test tests/*.test.mjs` — بلا تبعيات جديدة؛ القفل بلا تغيير). `web/tests/api.test.mjs` (10) يشغّل `web/src/api.js` الحقيقي بـ`fetch`/`sessionStorage` وهميين: إرسال المفتاح، دمج الترويسات، رسائل 401/403، الترميز، تعطل التخزين. على `api.js` السابق: 7/10 تفشل.
- **pytest**: `tests/test_r101_web_auth_contract.py` (13) — يمر على الكود السابق أيضاً لأن الخادم لم يتغير: يثبت نصف العقد الذي تعتمد عليه الواجهة الجديدة. ملاحظة: `TestClient` بلا `with` لا يُبقي حلقة الأحداث بين الطلبات، فمهمة تنتظر خيطاً (`test_runner`) لا تكتمل — الـfixture يستخدم `with` كما تحت uvicorn.
- **متصفح حقيقي** (Chromium عبر playwright، محلياً، ليس في CI): `raw/54` (مصادَق) و`raw/55` (مفتوح)؛ لقطات في `raw/r101_screenshots/`.
- العدد: pytest 287 + 3 + 3 على 3.11 و3.12 (`raw/56..57`).

## R-106 — توجيه المحادثة
- `tests/test_r106_chat_routing.py` (13): اقتراحات الواجهة الخمسة (يُقرأ نصها من `Chat.jsx` نفسه)، إجابة المحلل الحقيقية، فوز LLM وسقوطه إلى الموجّه، النية من التعليمة. على الكود السابق 10/13 تفشل (`raw/60`). العدد: 300 + 3 + 3 (`raw/61..62`).

## R-102 — معالجات تطابق ادعاءاتها (D-045)
- `tests/test_r102_honest_handlers.py` (29): كلها عبر `real_dispatcher.dispatch` عدا اختبار صيغة التباين (يستدعي `craft.contrast_ratio` مباشرة)؛ تمييزية (مدخلان مختلفان ← ناتجان مختلفان)، وتتحقق من مطابقة الإجابة للناتج، والكود المولَّد يُنفَّذ في الاختبار فعلاً، وفحوص WCAG تُعاد حسابها مستقلةً. على الكود السابق 28/29 حالة تفشل (`raw/65`). العدد: 329 + 3 + 3 (`raw/68..69`).

## R-103 — الذاكرة والتعلم (D-046)
- `tests/test_r103_memory_learning.py` (17): عبر `POST /api/tasks` الحقيقي — استرجاع المشابه وعدم استرجاع غيره، الفشل يُتذكر، التعلم السابق، بديل `researcher` وحدوده، التنفيذ `specialists`، النجاة من إعادة التشغيل عبر الملف، الحد الأعلى، التعطيل، فشل الطبقة لا يكسر المهمة، مسح الذاكرة بمفتاح، وإصلاحات `memory_system`. على الكود السابق 16/17 تفشل (`raw/73`). `tests/conftest.py`: ذاكرة فارغة لكل اختبار. العدد: 346 + 3 + 3 (`raw/75..76`).

## R-104 — إصلاح بالتوصيل لا حذف — `DataWorkerAgent` (D-047)

## R-107 — إعادة محاولة `researcher` للأخطاء العابرة فقط (D-048)

### R-104b/R-107b — إصلاح نواقص (دفعات تصحيح)

### R-104c/R-107c — جولة تصحيح ثانية

### R-104c2/R-107c2 — جولة ثالثة
- `test_r104_data_worker.py` (16): قيمة تظهر في `data` فقط تصل **حد `LLMClient.complete` نفسه** (تسجيل مباشر) وتغيير `data` يغيّر الحمولة (مقارنة الحمولتين مباشرة)؛ عقد المستهلك الحقيقي (`TaskDispatcherAgent`: `task_complete` يخفض الحمل بغض النظر عن `result.ok`)؛ الحزم الثلاث لها مستهلك إنتاجي فعلي (`specialist_runtime`/`_researcher`/`_data_processor`) — جواسيس على دوال الحزم نفسها + مسار البناء الفعلي عبر واجهة `communication/`؛ اختبار `runtime` بنقل متحكم به (لا شبكة حقيقية داخل المجموعة العادية). الاختبارات القديمة صارت حتمية ببدائل اختبارية.
- `test_r107_researcher_retry.py` (15): `SSLV3_ALERT_HANDSHAKE_FAILURE` صلب؛ اختبار التعليق يميّز الإصلاح القديم (حد 1.2ث يفشل مع انتظار 1.7ث).
- العدد: **377 ناجحاً، 3 متجاوزة، 3 مستبعدة** (375 قبل + صافي 2) + فحص الشبكة **3 ناجحة** (`docs/audit/M6/logs/raw/104_r104c2_r107c2_evidence_pack.log`). ملاحظة: فشل شبكة واحد لوحظ في الجلسة ثم نجح لاحقاً — السبب غير مشخّص.
- `test_r107_researcher_retry.py` (+3): `WRONG_VERSION_NUMBER` صلب لا يُعاد؛ `URLError` مجهول السبب صلب؛ نقل يعلق أطول من الميزانية يعيد المعالج خلالها (زمن مقاس < 1.3ث بميزانية 0.7ث).
- `test_r104_data_worker.py` (+4): التوقيع الصحيح (`LLMClient.complete(self, system, prompt)`) مع تحقق النص المستلم؛ نجاح حتمي بديل اختباري مع فحص العدادات؛ مهمة API بـ`prompt` + `data` فعلية (نصية) تصل LLM فعلاً؛ عقد `task_complete` موثق (النتيجة تحمل `ok`).
- العدد: 371 + 3 + 3 (`docs/audit/M6/logs/raw/102_r104c_r107c_evidence_pack.log` — خام كامل بلا اقتطاع وبلا `--silent`).
- `test_r104_data_worker.py` (+3، صارمة): فشل LLM يظهر `ok=False` + `status=failed` + `error` حقيقي (لا يقبل عيب ما قبل R-104)؛ `_execute_work` لا يزيد `tasks_completed` إلا عند نجاح؛ عبر TestClient مهمة نصية تصل `process_text` فعلاً وفشلها في النتيجة المخزنة.
- `test_r107_researcher_retry.py` (+4): خطأ شهادة لا يُعاد؛ HTTP 404 لا يُعاد (صريح)؛ ميزانية 0.52ث تمنع المحاولة الثانية (عدد فعلي 1)؛ العدد يعكس المحاولات المنفذة.
- العدد: 364 + 3 + 3 (`docs/audit/M6/logs/raw/101_r104b_r107b_evidence_pack.log` — خام كامل).
- `tests/test_r107_researcher_retry.py` (6): نقل وهمي يفشل مرة ثم ينجح (`retried: true, attempts: 2`)؛ SSL EOF عابر يُعاد؛ خطأ صلب (رفض السياسة) لا يُعاد؛ فشل متكرر يُعلن `transient, 2 attempts`؛ استعلام فارغ لا يُعاد؛ المصنّف يغطي كل الأخطاء المذكورة (ConnectionReset/SSLEOF/handshake-timeout عابرة؛ HTTP 404/PermissionError/ValueError صلبة). العدد: 357 + 3 + 3 (`docs/audit/M6/logs/100_r107_researcher_retry.log`). **الاختبار الحي `test_researcher_live_duckduckgo` يمر الآن** (نفس الاختبار الذي كان يتذبذب — راجع RISK-019).
- `tests/test_r104_data_worker.py` (5): قابلية الوصول من سلسلة الاستيراد الحية؛ انتشار فشل LLM (`_process_data` يعيد `error` عند `ok: False` — قبل R-104 كان يخفيه)؛ تفويض `_data_processor` للنص إلى `process_text()`؛ المدخل الرقمي حتمي وغير متغير؛ الحزم الفارغة الثلاث تُستورد سليمة. العدد: 351 + 3 + 3 (`docs/audit/M6/logs/99_r104_data_worker.log`). حد: `researcher` تفشل مع `-m network` بخطأ SSL من DuckDuckGo (نفس خطأ R-107 — ليس انحداراً لهذه الدفعة؛ `api_integrator` و`network_client` يمرّان).
### R-105 — توحيد specialists
- `test_specialists_package.py` (34): مطابقة الـ1,000 لخط الأساس (أسماء + agent_id + create_aid)؛ هوية الاستيراد عبر الجذر/الحزمة/الملف الفردي؛ 50 صنفاً مميزاً لكل تخصص؛ الوحدات الرقيقة بلا منطق مكرر؛ 20 قالباً؛ star-import = 1000؛ تنفيذ كل التخصصات الـ20 بنتيجة مؤكدة. `test_specialist_runtime.py` (10): `AGOS_AGENT_IMPL=specialists` = 1000/1000 **تنفيذاً** (بـAGOS_ALLOW_CODE_EXEC=1 وفق R-100).
- العدد: **385 ناجحاً، 3 متجاوزة، 3 مستبعدة** + فحص الشبكة **3 ناجحة** (`docs/audit/M6/logs/raw/105_r105_evidence_pack.log` + `106_r105_scale_1000.log` بمؤقتين منفصلين).
### R-108 — اختبار الإجهاد طويل الأمد
- `test_r108_sustained_load_script_smoke`: السكربت قابل للاستيراد بواجهته الموثقة + تشغيل أدنى ناجح. **R-112 (M6/50)**: أُعيد كتابته — monkeypatch لـ sys.argv + `--out` إلى tmp_path + تأكيد بصمة SHA256 للملف المتعقّب لا تتغير (لم يعد يستبدل أدلة الجريات السابقة؛ أضيف اختبار `test_r112_write_report_refuses_overwrite` — O_EXCL يرفض الاستبدال).
- الجريدة: `docs/audit/M6/logs/raw/108_r108_sustained_load_10min.log` — 828 موجة، 82,800/82,800، صفر فشل، الكمون p95=0.257ث، والذاكرة استقرت (~850MB بعد ~40 ألف مهمة).
- **توصيف صريح (R-112)**: الـ JSON المتعقّب `r108_sustained_load_report.json` تشغيل دخان 3 ثوانٍ لا أكثر — كل الست نسخ المتعقّبة في تاريخ git تُظهر duration_s_target=3 (فُحصت بالفعل). الدليل الكامل الوحيد هو السجل الخام أعلاه.