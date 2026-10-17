# خط أساس اختبارات الملفات — R-158 (D-070)

> البند: R-158 من [P1_REPOSITORY_REVIVAL_AR.md](../roadmap/P1_REPOSITORY_REVIVAL_AR.md). اللقطة: **`573793c1f36afd77f3b79247ab9e2d978a397b62`** (main بعد دمج PR #171 / R-157).
> الأسس السابقة: الجرد [inventory/](../inventory/README.md) (R-155)، الخرائط وعقود القدرات [maps/](../maps/README.md) (R-156/R-157).

هذا الملف (README) مكتوب يدوياً ويضم: طريقة إعادة الإنتاج، والبصمة، ومراجعة الثغرات، وقائمة ملفات الدفعة. أما النتائج نفسها فيولّدها المولّد ولا تُحرَّر يدوياً:

| الملف | المحتوى |
|---|---|
| [`TEST_BASELINE_573793c.json`](TEST_BASELINE_573793c.json) | المصدر الآلي الكامل: البيئة والإصدارات، المجموعات، الخطوات (الأمر، سبب الملاءمة، exit، العدّادات pass/fail/skip/xfail/xpass/deselected/errors، القسم في السجل الخام)، الفئات unit/contract/integration/live بالوضعين، حالة كل ملف متعقب، ونتيجة CI على اللقطة |
| [`TEST_BASELINE_573793c.md`](TEST_BASELINE_573793c.md) | التقرير البشري، مولَّد من JSON نفسه (اختبار يتحقق من التطابق حرفياً) |
| [`TEST_BASELINE_GROUPS_573793c.csv`](TEST_BASELINE_GROUPS_573793c.csv) | صف لكل خطوة، يبدأ بأعمدة [`test_evidence_template.csv`](../templates/test_evidence_template.csv) ثم أعمدة إضافية |
| [`TEST_BASELINE_FILES_573793c.csv`](TEST_BASELINE_FILES_573793c.csv) | صف لكل ملف من الملفات المتعقبة الـ1917 عند اللقطة: مجموعته، والفئة، والحالة (pass/fail/integrity-only)، والفحوص، وتغطية الأسطر |

المولّد: [`scripts/build_test_baseline.py`](../../../../../scripts/build_test_baseline.py). اختباراته: [`tests/test_r158_test_baseline.py`](../../../../../tests/test_r158_test_baseline.py).

## إعادة الإنتاج

```bash
# Python 3.12 + الأقفال (كما في CI)
pip install --require-hashes -r requirements.lock -r requirements-dev.lock
# Node >= 22.13 (المستخدم: v22.22.0)، PostgreSQL 17 (المستخدم: 17.11 من conda-forge عبر micromamba في /tmp/pgenv)،
# PostgREST 12.2.3 (ثنائي رسمي، تم التحقق من checksum)
gh run view 39005652192 --repo reeveero-tech/All-hand \
  --json databaseId,headSha,conclusion,event,url,jobs > ci.json      # = docs/audit/M8/logs/raw/087_*.json
python scripts/build_test_baseline.py --repo . --sha 573793c1f36afd77f3b79247ab9e2d978a397b62 \
  --out OUT --raw-log RAW.log --node-bin <node>/bin --pg-bin <pg>/bin --postgrest <postgrest> --ci-json ci.json
# تشغيل في نسخة نظيفة: --repo <clone> --checkout <clone> (يرفض المولّد نسخة فيها تغييرات أو HEAD مختلف)
# إعادة توليد التقرير/CSV من JSON فقط (بدون تشغيل أي أمر): --render-from OUT/TEST_BASELINE_573793c.json --out OUT
```

يعمل المولّد افتراضياً داخل `git worktree` جديد عند SHA، ويثبّت المتغيرات `TZ=UTC` و`LANG/LC_ALL=C.UTF-8` و`PYTHONHASHSEED=0` و`CI=1`. يشغّل pytest مرتين على الأقل، مرة لكل وضع: `AGOS_AGENT_IMPL=real` مع التغطية، و`AGOS_AGENT_IMPL=specialists`. ويشغّل اختبارات `-m network` كخطوة منفصلة. لا يضيف المولّد أي skip أو xfail أو استثناء، ولا يغيّر أي ملف متعقب.

## التشغيلات الثلاثة والبصمة

| التشغيل | الطريقة | السجل الخام | البصمة (الجزء الحتمي) |
|---|---|---|---|
| 1 | `git worktree` جديد | [088](../../../../audit/M8/logs/raw/088_r158_baseline_run1_worktree.log) | `d2523d8c…69c33474` |
| 2 | `git worktree` جديد | [089](../../../../audit/M8/logs/raw/089_r158_baseline_run2_worktree.log) | `d2523d8c…69c33474` |
| 3 | `gh repo clone` جديد + `checkout --detach`، تم التحقق من نظافته (0 تغييرات) — **مصدر الملفات الملتزمة** | [090](../../../../audit/M8/logs/raw/090_r158_baseline_run3_clean_clone.log) | `d2523d8c…69c33474` |

البصمة الكاملة: `d2523d8ca38443c6e8fa84be37445f5dc5362accdd41caa4d292737969c33474`. المقارنة في [091](../../../../audit/M8/logs/raw/091_r158_baseline_reproducibility.log): لا يوجد أي فرق في الجزء الحتمي بين التشغيلات الثلاثة، بما في ذلك تغطية الأسطر لكل ملف.

- **الحقول غير الحتمية، وهي مستبعدة من البصمة عمداً:** `raw_log` و`checkout`، والخطوات من نوع `local-live-internet` (أي `pytest -m network`، لأنها تعتمد على خدمات خارجية). المدد والطوابع الزمنية والمسارات المؤقتة لا تُكتب في JSON إطلاقاً، وتوجد في السجلات الخام فقط.
- **فرق التقرير:** كتب التشغيل 3 تقرير `.md` بترتيب إدراج المفاتيح، بينما يُخزَّن JSON مرتب المفاتيح. كشف اختبار التطابق هذا الفرق، فأصبح المولّد يرتّب المفاتيح قبل الكتابة، وأُعيد توليد التقرير من JSON نفسه. JSON وملفا CSV بقيت متطابقة بايتاً ببايت، ولم يُعد تشغيل أي أمر ([093](../../../../audit/M8/logs/raw/093_r158_report_rerender.log)). استخدمت التشغيلات 088–090 المولّد بـ sha256 `c630d8c6…`، والفرق الوحيد عن النسخة الحالية `52d3fbd9…` مسجّل في 093.

## ملخص النتائج عند `573793c` (محلي)

| المجال | النتيجة المحلية | CI على SHA نفسه (run [39005652192](https://github.com/reeveero-tech/All-hand/actions/runs/39005652192)) |
|---|---|---|
| pytest `real` (+تغطية) | 1056 passed, 4 skipped, 4 deselected — exit 0 | `test` success |
| pytest `specialists` | **3 failed**, 1053 passed, 4 skipped, 4 deselected — exit 1 (الاختبارات الثلاثة من R-149، انظر G-157-5) | لم يكن هذا الوضع مشغَّلاً في CI |
| فئات TEST_MATRIX (real/specialists) | contract 146/146 (9 ملفات)، integration 529/526+3 failed (25 ملفاً)، unit 381/381 (15 ملفاً)، live 4 skipped | — |
| `-m network` | 3 passed, **1 failed** (`test_live_keyless_llm_and_translation`: أعادت خدمة MyMemory خطأ HTTP) | `network` success |
| التخزين: PostgreSQL 17.11 + PostgREST 12.2.3 (تحقق تشغيلي محلي) | 4 passed | `storage` success |
| compile / import | 1194 ملف `.py` / 1092 وحدة: كلها OK | — |
| ruff | نطاق CI: 0؛ ملفات `docs/audit` خارج النطاق: **5** (F401×4، F541×1) | `quality` success |
| `web/` | npm ci + build + test 31/31 | `build` success |
| `src/` | npm ci + eslint + build OK؛ **`tsc --noEmit` = exit 2 (4 أخطاء)** | `build` success (لا يشغّل tsc) |
| Docker/Render/CI | YAML يُقرأ بنجاح، والفحص الذاتي لخريطة نقاط الدخول ينجح | `docker` success |
| الحوكمة | بوابات الحالة وعدم الحذف وatomic-v2 تنجح على اللقطة | governance gate success (PR #171) |
| أدلة التدقيق | 2 ملفات `.json` ليست JSON، وسجل واحد ليس UTF-8 | — |
| docs | روابط 60/60 ملف `.md` سليمة | — |

**تنبيه عن حالات الملفات:** حالة الملف مشتقة من فحوص مجموعته.
- ملفات `frontend-root` الـ25 كلها `fail` لأن خطوة `tsc` على مستوى المجموعة فشلت، رغم أن الأخطاء في 3 ملفات فقط: `src/routes/__root.tsx` و`src/server.ts` و`src/start.ts`.
- خطوة `ruff:py-scripts` تشمل أيضاً سكربتات `docs/audit`. لذلك نتيجة المجموعة `fail` مع أن ملفات `scripts/` كلها `pass`، والملفات الثلاثة المعنية `fail` في مجموعة `evidence-scripts`.

## مراجعة ثغرات R-157 (G-157-1..6)

| الثغرة | هل يكشفها خط الأساس؟ | الاختبار الناقص | الإجراء |
|---|---|---|---|
| **G-157-1** `/api/system/status` مع حضارة معطوبة يعيد 500 عاماً، لا خطأً منظماً | لا. لم يكن أي اختبار يغطيها | أُضيف [`test_g157_1_system_status_500_has_no_internal_text`](../../../../../tests/test_r158_gap_regressions.py): يثبت أن الـ500 لا يحمل نص الاستثناء ولا traceback، وأن `/health/ready` يعيد 503 بسببه الثابت | **مفتوحة**: تحويلها إلى خطأ منظم يغيّر عقد الواجهة، فيُترك لبند العقود اللاحق |
| **G-157-2** `check_civilization` القديم المستقل يعيد `str(e)` | لا (لا يُستدعى من الخادم: خريطة R-156) | اختبار يثبت أن المسار غير قابل للوصول من الخادم موجود ضمنياً في خريطة نقاط الدخول، ولا يوجد اختبار للدالة نفسها | **مفتوحة، موثقة**: لا تغيير قبل قرار إزالة/توحيد المسار القديم. **تحديث R-160 (D-072)**: التسريب نفسه أُصلح — الرسالة صارت `Error: <Type>` واختبار للدالة نفسها في `tests/test_r160_g159_1_log_redaction.py`؛ قرار إزالة/توحيد المسار القديم ما زال مفتوحاً |
| **G-157-3** سياسات اختيار الوكيل مختلفة بين المسارات | نعم: `test_c5_*` تثبّت السلوك الحالي | لا يوجد اختبار يقيس جودة السياسة (بند قياس، لا وحدة) | **قرار المالك أو قياس P6**: لم تتغير السياسة (ممنوع تغييرها لتحسين النتائج) |
| **G-157-4** `src/lib/api.ts` لا يرسل مفتاح المستأجر | جزئياً: build وeslint ينجحان، وtsc يفشل (G-158-2). لا يوجد اختبار مصادقة لعميل `src/` | اختبار عقد المصادقة لعميل `src/` | **مرتبطة بـQ-002**: لم يُعدَّل `api.ts`، ولم تُختر واجهة رئيسية |
| **G-157-5** 3 اختبارات R-149 تفشل في وضع `specialists` | **نعم**: `pytest:specialists` = 3 failed | — | **محلولة في هذه الدفعة**. السبب مثبت في [092](../../../../audit/M8/logs/raw/092_r158_g157_5_root_cause.log): الحقن رقّع الربط `real_agents._dispatch` وحده، بينما قالب المتخصص يستدعي `real_dispatcher.dispatch`، فبقي الانهيار المحقون معطَّلاً. الإصلاح: المساعد `_patch_handler_dispatch` يرقّع الربطين، والتأكيدات بلا تغيير. يفشل الاختبار المُصلَح على `228b956` (قبل محوّل R-157) مع تسرّب النص، ويقتل طفرة إلغاء المحوّل. أُضيفت خطوة `AGOS_AGENT_IMPL=specialists` إلى وظيفة CI `test`، واختبار انحدار `test_g157_5_*` |
| **G-157-6** `run_real` بلا مستهلك | لا يوجد سلوك لتكشفه، والتغطية مسجلة لكل ملف | — (غير مستخدم) | **موثقة**: إن اعتُمد لاحقاً فيجب أن يمر عبر `execute` |

## ثغرات جديدة كشفها خط الأساس

| المعرّف | الثغرة | التكرار | الإجراء التالي |
|---|---|---|---|
| G-158-1 | `test_live_keyless_llm_and_translation` يفشل محلياً لأن خدمة MyMemory الخارجية أعادت خطأ HTTP، بينما نجحت وظيفة CI `network` على SHA نفسه | 088–090، قسم `pytest:network` | تبعية خارجية غير حتمية، مستبعدة من البصمة. تُقاس في P6 (سياسة التبعيات الخارجية) |
| G-158-2 | `tsc --noEmit` في `src/` بعد build: 4 أخطاء (`__root.tsx` و`server.ts` و`start.ts`). لا يشغّل CI أداة tsc | القسم `frontend-root:tsc` | مرتبطة بـ**Q-002**. لم يُعدَّل `src/`. الإجراء بعد قرار المالك: إصلاح الأنواع ثم إضافة tsc إلى CI |
| G-158-3 | ruff خارج نطاق CI: 5 تشخيصات في سكربتات أدلة تاريخية (`docs/audit/M6/...`) | القسم `ruff:py-scripts` | أدلة غير قابلة للتعديل. تُسجل فقط |
| G-158-4 | `docs/audit/M6/logs/raw/09_npm_outdated_root.json` و`10_..._web.json` نصوص أوامر بامتداد `.json` | القسم `data:parse` | أدلة غير قابلة للتعديل. تُسجل، ويُتجنب هذا النمط لاحقاً |
| G-158-5 | `docs/audit/M6/logs/79_r086_before.log` ليس UTF-8 صالحاً (حرف متعدد البايتات مقطوع عند البايت 2120) | القسم `integrity` | سجل خام غير قابل للتعديل. يُسجل |
| G-158-6 | لا يُبنى Docker ولا تُشغَّل الحاوية محلياً، ولا يوجد أي تحقق من نشر Render | — | Docker مثبت في CI فقط (`docker` success). Render غير مختبر |
| G-158-7 | لا يوجد مدقق أنواع لـPython في الأقفال | — | قرار أداة، لا يُضاف دون بند |
| G-158-8 | سكربتات الفحص الحي (`scripts/run_r*.py` ونحوها) تُترجَم وتُفحص بـruff فقط، ولا تُشغَّل | `compile`/`ruff:py-scripts`، والتغطية في FILES CSV | تحتاج خادماً حياً. تُغطى في بنود التشغيل الحي |

## ما لم يُختبر محلياً (صراحةً)

- لم يُبنَ Docker ولم تُشغَّل الحاوية.
- لم يُنشر على Render.
- لم تُستدعَ خدمات LLM مدفوعة.
- لم يُشغَّل Supabase سحابي.
- لم يُطبَّق أي حمل أو سعة. لا يوجد ادعاء جاهزية إنتاج.
- الخدمات الخارجية في `-m network` تخضع لحالة الشبكة.

## ملفات الدفعة R-158 (خارج اللقطة)

اللقطة هي الملفات الـ1917 عند `573793c` فقط. الملفات التالية جديدة في هذه الدفعة وليست جزءاً منها، ويتحقق اختبار من أن كل ملف موجود وغير موجود في اللقطة. الملفات المعدَّلة، أي الوثائق و`ci.yml` و`tests/test_r149_security_qa_gaps.py`، مذكورة في CHANGELOG وEVIDENCE.

<!-- batch-files -->
- `scripts/build_test_baseline.py`
- `tests/test_r158_test_baseline.py`
- `tests/test_r158_gap_regressions.py`
- `docs/governance/roadmaps/revival-scale/baseline/README.md`
- `docs/governance/roadmaps/revival-scale/baseline/TEST_BASELINE_573793c.json`
- `docs/governance/roadmaps/revival-scale/baseline/TEST_BASELINE_573793c.md`
- `docs/governance/roadmaps/revival-scale/baseline/TEST_BASELINE_GROUPS_573793c.csv`
- `docs/governance/roadmaps/revival-scale/baseline/TEST_BASELINE_FILES_573793c.csv`
- `docs/audit/M8/logs/raw/085_r157_acceptance_main_ci.log`
- `docs/audit/M8/logs/raw/086_r158_r157_closure_check.log`
- `docs/audit/M8/logs/raw/087_r158_base_ci_run_573793c.json`
- `docs/audit/M8/logs/raw/088_r158_baseline_run1_worktree.log`
- `docs/audit/M8/logs/raw/089_r158_baseline_run2_worktree.log`
- `docs/audit/M8/logs/raw/090_r158_baseline_run3_clean_clone.log`
- `docs/audit/M8/logs/raw/091_r158_baseline_reproducibility.log`
- `docs/audit/M8/logs/raw/092_r158_g157_5_root_cause.log`
- `docs/audit/M8/logs/raw/093_r158_report_rerender.log`
- `docs/audit/M8/logs/raw/094_r158_pytest_both_modes.log`
- `docs/audit/M8/logs/raw/095_r158_local_gates.log`
- `docs/audit/M8/logs/raw/096_r158_pr_ci_shallow_checkout.log`
- `docs/audit/M8/logs/raw/097_r158_pytest_gates_after_fix.log`
<!-- /batch-files -->
