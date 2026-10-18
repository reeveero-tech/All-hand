# أدلة M8 — برنامج «إحياء AGOS والاختبار القصوى» (D-063)

> المرجع: [`docs/governance/roadmaps/revival-scale/`](../../governance/roadmaps/revival-scale/README_AR.md). هذا الملف يجمع أدلة كل دفعات M8 (R-151..R-184) بترتيبها؛ السجلات الخام في `logs/raw/` مرقمة تصاعدياً بدءاً من `001`.

## R-151 — إدخال الخطة (2026-10-17، دفعة m8/r151-revival-plan-integration — توثيقية فقط)

### فحص الواقع قبل العمل
- main = `8457ad3b1a8860b1f0985937e58796258ea84f94` (دمج PR #161)، CI على main ناجح (التشغيل 38978799351)، لا PRs مفتوحة — `logs/raw/001_r151_base_state.log`.
- آخر بند قائم في ROADMAP هو R-150 ⇒ لا تصادم مع أرقام الحزمة R-151..R-184. آخر قرار D-062c، آخر سؤال Q-022، آخر خطر RISK-029.
- حالة الحزمة: 24 ملفاً قُرئت كلها (README_AR، BUILDER_AGENT_ORDER_AR، MASTER_ROADMAP_AR، P0..P7، TEST_MATRIX، ATOMIC_BATCH_RULE، NO_DELETE_FILE_AUDIT، 10K_TOOLS_SPEC، MILLION_TASK_CAPACITY، LLM_QUALITY_HALLUCINATION، REFERENCES، المواصفة والقوالب الأربعة، MANIFEST).

### ما تغيّر
- **مضاف (A)**: الحزمة كما هي في `docs/governance/roadmaps/revival-scale/` (24 ملفاً)، و`docs/audit/M8/` (هذا الملف + السجلات).
- **معدّل (M)**: ROADMAP (قسم M8 + الترتيب + رأس آخر تحديث)، PROJECT_STATE، CHANGELOG، DECISIONS (D-063)، OPEN_QUESTIONS (Q-023، Q-024)، RISKS (RISK-030، RISK-031)، PROJECT_CHARTER، QUALITY، OPERATIONS، HANDOFF_PROMPT، README (فهرس).
- **لا كود، لا اختبارات، لا CI، لا إعدادات مستودع** — دفعة توثيقية بأمر المالك («ابدأ بإدخال الخارطة كدفعة توثيقية مستقلة»).

### الأدلة
| الفحص | النتيجة | السجل |
|---|---|---|
| بصمات SHA-256 وأحجام ملفات الحزمة مقابل MANIFEST.json | 23/23 مطابقة، صفر فشل (MANIFEST نفسه غير مدرج فيه — متوقع) | `logs/raw/002_r151_manifest_sha256.log` |
| `git diff --cached --name-status -M origin/main` | كل الإدخالات A أو M؛ **صفر D وصفر R** | `logs/raw/003_r151_diff_name_status.log` |
| الروابط النسبية في ROADMAP وREADME وCHARTER وQUALITY وملفات الحزمة | 81 سليمة، 0 مكسورة | `logs/raw/004_r151_relative_links.log` |
| عدّاد الخارطة (`scripts/run_r075_roadmap_audit.py 8457ad3..HEAD R-151`) | 179 بنداً، صفر تكرار، صفر بلا حالة؛ 145 done + 1 awaiting_merge + 33 planned؛ rc=0 | `logs/raw/005_r151_roadmap_audit.log` |
| pytest tests/ | 847 ناجح + 4 متخطى + 4 مستبعد، rc=0 (مطابق لخط الأساس R-150) | `logs/raw/006_r151_pytest.log` |
| ruff F,E722 | صفر، rc=0 | `logs/raw/007_r151_ruff.log` |
| `web/`: npm ci + build | rc=0 / rc=0 | `logs/raw/008`، `009` |
| الجذر: npm ci + build | rc=0 / rc=0 | `logs/raw/010`، `011` |
| البيئة | 2 CPU، ~8GB RAM، بلا swap، Python 3.14.3، Node v20.20.2 | `logs/raw/012_r151_environment.log` |

### حدود وملاحظات صادقة
- الفحوص المحلية على Python 3.14.3 وNode 20.20.2؛ CI يشغّل Python 3.11/3.12 وNode 22 (الجذر يعلن `>=22.12`؛ بناؤه المحلي على Node 20 نجح بلا `--engine-strict` — ليس بديلاً عن job `build` في CI). نتيجة CI على الـPR وعلى SHA الدمج تُسجل في دفعة R-152 (حين يتحول R-151 إلى done).
- لا شيء من أهداف M8 (10k وكيل، 1M مهمة، 10k أداة) محقق أو مقيس بعد. البيئة الحالية (2 CPU/~8GB) قد لا تكفي لسلالم P6 — RISK-031/Q-023.
- لم تُغيَّر حماية الفرع ولا الأسرار ولا الصلاحيات.

### قبول R-151 (سُجل داخل دفعة R-152)
- PR #162 مدموجة بـ`--merge`؛ SHA الدمج `646e453cffee2f57b1e901abde236c7748618a2c`؛ فحوص الـPR كلها pass وpublish متخطى (D-011)؛ CI على main عند SHA الدمج نفسه (التشغيل 38985115300): test/quality/network/docker/audit/storage/build = success؛ بصمات الحزمة 23/23 مطابقة على الـblobs المدموجة — `logs/raw/013_r151_acceptance_main_ci.log`. (job ‏`governance` يعمل على أحداث PR فقط بالتصميم، فلا يظهر في تشغيل main.)

## R-152 — بوابة عدم الحذف (2026-10-17، دفعة m8/r152-no-delete-gate، D-064)

### ما تغيّر
- **مضاف**: `scripts/check_no_delete.py`، `tests/test_r152_no_delete_gate.py` (13 اختباراً).
- **معدّل**: `.github/workflows/governance-gate.yml` (خطوة «No-delete check (R-152)»؛ منطق «Atomic batch check» بلا تغيير + `if: !cancelled()`)، ROADMAP، PROJECT_STATE، CHANGELOG، DECISIONS (D-064)، GOVERNANCE_GATE، QUALITY.
- **لم يتغير**: حماية الفرع وأي إعداد للمستودع.

### الأدلة
| الفحص | النتيجة | السجل |
|---|---|---|
| اختبار الطفرة: سكربت يتجاهل الحذف | 6 اختبارات سالبة تفشل، 7 تنجح (الموجبة/العقد) ⇒ الاختبارات تميّز | `logs/raw/014_r152_mutation_negative_tests_fail.log` |
| السكربت على حذف تاريخي حقيقي (R-138 ‏`.coverage`) | rc=1 مع اسم الملف؛ وعلى دمج R-151 rc=0 | `logs/raw/015_r152_historical_deletion_detected.log` |
| **PR تجريبية حية سالبة** #164 (مسودة، تحذف ملف قالب واحداً) | governance = **fail** عند «No-delete check» مع المسار؛ «Atomic batch check» في الـjob نفسه نجح ⇒ الفشل سببه الحذف وحده؛ أُغلقت بلا دمج | `logs/raw/016_r152_live_trial_negative.log` (والتشغيل الأول قبل إصلاح نص الرسالة: `016a`) |
| **PR الدفعة موجبة** #163 | governance = pass (الخطوتان)، وكل الفحوص pass | `logs/raw/017_r152_batch_pr_positive.log` |
| pytest tests/ | 860 ناجح + 4 متخطى + 4 مستبعد، rc=0 (847 + 13) | `logs/raw/018_r152_pytest.log` |
| ruff F,E722 (+ السكربت الجديد) | صفر، rc=0 | `logs/raw/019_r152_ruff.log` |
| بناء web/ والجذر | rc=0 ×4 | `logs/raw/020`..`023` |
| عدّاد الخارطة + فحص ذاتي لعدم الحذف | 179 بنداً، 146 done + 1 awaiting_merge + 32 planned؛ صفر D | `logs/raw/024_r152_roadmap_audit_selfcheck.log` |

### اكتشاف أثناء التنفيذ (أُصلح في الدفعة نفسها)
- في التشغيل الحي الأول (`016a`) أزال GitHub قيمة `file=` من نص السجل فظهر «deleted» بلا مسار. أُضيف المسار إلى نص الرسالة نفسه، وحُدّثت الاختبارات، وأُعيد اختبار الطفرة (`014`) والحزمة (`018`)، وأعيد التشغيل الحي (`016`) فظهر المسار.

### حدود صادقة
- لا استثناء داخل المستودع: الحذف المصرّح يحتاج قرار مالك منفصلاً (D-064). المالك يستطيع تقنياً التجاوز الإداري (`enforce_admins=false` — حد قائم، لم يُغيَّر).
- الفحص على الـdiff الصافي للـPR، لا على كل commit منفرداً، ولا على الدفع المباشر إلى main (محمي بحماية الفرع).
- R-152 يتحول إلى done داخل دفعة R-153 بعد التحقق من CI على SHA الدمج.

### قبول R-152 (سُجل داخل دفعة R-153)
- PR #163 مدموجة بـ`--merge`؛ SHA الدمج `7378357deb13ba2dc1c6c54ba43d1313c4752d07`؛ فحوص الـPR كلها pass؛ CI على main عند SHA الدمج (التشغيل 38987448882) كل الوظائف success؛ PR التجريبية #164 مغلقة بلا دمج — `logs/raw/027_r152_acceptance_main_ci.log`.

## R-153 — فحص الذرية v2 (2026-10-17، دفعة m8/r153-atomic-batch-v2، D-065)

### ما تغيّر
- **مضاف**: `scripts/check_atomic_batch.py`، `tests/test_r153_atomic_batch_v2.py` (54).
- **معدّل**: `.github/workflows/governance-gate.yml` (خطوة «Atomic batch check v2 (R-153)» مضافة فقط؛ v1 وعدم الحذف بلا تغيير)، ROADMAP، PROJECT_STATE، CHANGELOG، DECISIONS (D-065)، GOVERNANCE_GATE، QUALITY.

### الأدلة
| الفحص | النتيجة | السجل |
|---|---|---|
| إعادة تشغيل v1 وv2 على كل دمجات main (161) | v1 يرفض 35 وv2 يرفض الـ35 نفسها (كلها PR #1..#36 قبل وجود البوابة)؛ **صفر رفض جديد** | `logs/raw/025_r153_historical_replay.log` |
| طفرات السكربت (R0، إضافات R1، R5، R6، خطأ git = نجاح) | 5/5 مقتولة؛ الجولة الأولى كشفت اختباراً ضعيفاً فأُصلح (M2b: 2 ⇒ 19 فشلاً) | `logs/raw/026_r153_mutation_tests.log` |
| pytest tests/ | 914 ناجح + 4 متخطى + 4 مستبعد (860 + 54)، rc=0 | `logs/raw/028_r153_pytest.log` |
| ruff F,E722 (+ السكربتان) | صفر | `logs/raw/029_r153_ruff.log` |
| بناء web/ والجذر | rc=0 ×4 | `logs/raw/030`..`033` |
| عدّاد الخارطة + فحص ذاتي (عدم الحذف + v2) | 179؛ 147 done + 1 awaiting_merge + 31 planned؛ الفحصان rc=0 | `logs/raw/034_r153_selfcheck.log` |
| **PR تجريبية حية ناقصة** #166 (scripts/ + workflow + الوثائق بلا `docs/audit`) | عدم الحذف pass، **v1 pass**، **v2 fail (R1)** ⇒ v2 أشد فعلاً؛ أُغلقت بلا دمج | `logs/raw/035_r153_live_trial_incomplete.log` |
| **PR الدفعة الكاملة** #165 | governance pass بالخطوات الثلاث؛ كل الفحوص pass | `logs/raw/036_r153_batch_pr_positive.log` |

### أخطاء أثناء التنفيذ (موثقة)
- اختبار R1 الأول كان ضعيفاً (ملفات مساعدة تحت `tests/` و`web/` تُطلق R1 بنفسها) — كشفته الطفرة M2، أُصلح في الدفعة نفسها.
- الدفعة الأولى من الفرع التجريبي التقطت سجل `034` من شجرة العمل (كان سيُرضي R1) — أُزيل من الفرع التجريبي قبل التشغيل المسجل؛ التشغيل على الرأس المصحح `2c9d082`.

### حدود صادقة
- يثبت **وجود** اختبارات/أدلة في الدفعة لا **كفايتها**؛ `src/` بلا إطار اختبار بعد (R-158)؛ الفحص على diff الـPR الصافي.
- R-153 يتحول إلى done داخل دفعة R-154 بعد التحقق من CI على SHA الدمج.

### قبول R-153 (سُجل داخل دفعة R-154)
- PR #165 مدموجة بـ`--merge`؛ SHA الدمج `a34dc77c6eca7912f2e6e4258ecd5a1a081a3e0a`، أبوه الثاني = رأس الـPR المختبر `805ce218b9e219e337932af3c240747750b7e2a0`؛ فحوص الـPR كلها pass؛ CI على main عند SHA الدمج (التشغيل [38989224464](https://github.com/reeveero-tech/All-hand/actions/runs/38989224464)) كل الوظائف success؛ PR التجريبية #166 مغلقة بلا دمج — `logs/raw/037_r153_acceptance_main_ci.log`. كان هذا التناقض («مدموجة ولا تزال awaiting_merge») هو ما عالجته R-154.

## R-154 — تعريف حالات العمل وشروط قبول الأدلة (2026-10-17، دفعة m8/r154-status-definitions، D-066)

### ما تغيّر
- **مضاف**: `docs/governance/STATUS_DEFINITIONS.md`، `scripts/check_status_evidence.py`، `tests/test_r154_status_evidence.py` (53).
- **معدّل**: `.github/workflows/governance-gate.yml` (خطوة «Status evidence check (R-154)» مضافة؛ الثلاث السابقة بلا تغيير)، ROADMAP (حقول دليل R-151..R-153، R-153 done، R-154 awaiting_merge، فقرة الحالات)، PROJECT_STATE (جدول «حالة بنود M8»)، CHANGELOG، DECISIONS (D-066)، GOVERNANCE_GATE، QUALITY، README، HANDOFF_PROMPT.

### الحالة قبل/بعد
| البند | قبل | بعد |
|---|---|---|
| R-151 | done (أدلة نثرية) | done + حقول مهيكلة متحققة (PR #162، `646e453`، CI 38985115300) |
| R-152 | done (أدلة نثرية) | done + حقول مهيكلة متحققة (PR #163، `7378357`، CI 38987448882) |
| R-153 | **awaiting_merge رغم الدمج** (تناقض) | done + حقول مهيكلة متحققة (PR #165، `a34dc77`، CI 38989224464) |
| R-154 | planned | awaiting_merge (PR #167) |

### الأدلة
| الفحص | النتيجة | السجل |
|---|---|---|
| الفحص الجديد على حالة main قبل الدفعة | rc=1، 23 مخالفة (حقول R-151/R-152 الناقصة، R-153 بلا pr/branch، جدول PROJECT_STATE غائب) | `logs/raw/038_r154_check_before.log` |
| محاكاة تناقض R-153 الأصلي (worktree على `a34dc77` + التزام لاحق) | `CONTRADICTION — PR #165 is already merged`؛ وعلى التزام الدمج نفسه صفر تناقض (الاستثناء) | `logs/raw/039_r154_r153_contradiction_simulation.log` |
| طفرات (12: كل قاعدة + خطأ git = نجاح) | 12/12 مقتولة | `logs/raw/040_r154_mutation_tests.log` |
| pytest tests/ | 967 ناجح + 4 متخطى (R-042 live storage — job ‏storage) + 4 مستبعد (network — job ‏network)، rc=0 | `logs/raw/041_r154_pytest.log` |
| ruff F,E722 (+ سكربتات البوابة الثلاثة) | صفر | `logs/raw/042_r154_ruff.log` |
| بناء web/ والجذر | rc=0 ×4 | `logs/raw/043`..`046` |
| فحص ذاتي + عدّاد الخارطة | الفحص الجديد pass؛ 179 بنداً: 148 done + 1 awaiting_merge + 30 planned | `logs/raw/047_r154_selfcheck.log` |
| **PR تجريبية حية مخالفة** #168 (إعادة تناقض R-153) | عدم الحذف وv1 وv2 pass، **Status evidence check fail (CONTRADICTION)**؛ أُغلقت بلا دمج | `logs/raw/048_r154_live_trial_contradiction.log` |
| **PR الدفعة** #167 | governance pass بالخطوات الأربع، كل الفحوص pass | `logs/raw/049_r154_batch_pr_positive.log` |

### حدود صادقة
- البوابة لا تستعلم GitHub API: نتيجة `ci_run` تُثبت بمحتوى سجل القبول المرفوع (يمكن نظرياً كتابة سجل كاذب — يكشفه المراجع بـ`gh run view`؛ ربط git `merge_sha^2 = tested_sha` يُتحقق منه فعلاً).
- لا حكم على كفاية الاختبارات علمياً (للمراجعة والطفرات)؛ بنود R-001..R-150 تُفحص مفرداتها فقط؛ النص النثري في PROJECT_STATE لا يُفحص (المرجع جدول M8).
- `production-ready` غير مُعلن ولا يُعلن قبل R-184 وقرار المالك.
- R-154 يتحول إلى done في الدفعة التالية؛ القاعدة 8 ستفرض ذلك آلياً.

### قبول R-154 (سُجل داخل دفعة R-155)
- PR #167 مدموجة بـ`--merge`؛ SHA الدمج `f8b2d4828ee5aba3dc097665199f1fdf6450c2eb`، أبوه الثاني (git) = رأس الـPR المختبر `141e3f8bb4f41509646d24f43337c89e46075aa2`؛ فحوص الـPR كلها pass (publish متخطى D-011)؛ CI على main عند SHA الدمج (التشغيل [38993839404](https://github.com/reeveero-tech/All-hand/actions/runs/38993839404)) كل الوظائف success — `logs/raw/050_r154_acceptance_main_ci.log`.
- الاختبارات: `041` يذكر 967 passed, 4 skipped, 4 deselected؛ وأُعيد إنتاج الرقم نفسه مستقلاً في worktree نظيف عند `f8b2d48` (`053`). المتخطاة الأربعة = `tests/test_r042_live_storage.py` (تحتاج `R042_LIVE=1`) يشغّلها job ‏`storage` عبر `scripts/r042/run_local_stack.sh`؛ المستبعدة الأربعة = علامة `network` يشغّلها job ‏`network` بـ`pytest tests/ -m network` — كلاهما success في 38993839404.
- غير مختبر: الجاهزية الإنتاجية (R-184 + قرار المالك)، أهداف السعة 10,000/1,000,000/10,000، استعلام API من البوابة.
- فحص الحالات بعد التحويل إلى `done` وقبل أي عمل جرد: pass — `logs/raw/051_r155_r154_closure_check.log`.

## R-155 — جرد 100% من الملفات المتعقبة (2026-10-17، دفعة m8/r155-file-inventory، D-067)

### اللقطة
- SHA الأساس `f8b2d4828ee5aba3dc097665199f1fdf6450c2eb` (= main بعد دمج R-154). `git ls-files -z` في worktree نظيف = **1867** = `git ls-tree -r` عند الـSHA؛ المجموع حسب الدليل الأعلى 27+2+1+1080+9+628+1+35+18+50+16 = 1867؛ 24,471,939 بايت (`052`).

### النتيجة
| البعد | القيم |
|---|---|
| الصفوف | 1867 (= ls-files، مجموعة المسارات متطابقة، 0 تكرار، 0 `DELETE`) |
| الحالة | 131 active، 36 shared، 1000 legacy-compatible، 563 generated، 59 test-only، 61 docs-only، **17 unresolved** |
| حُسم دوره بدليل آلي | 1850 (ليست قراءة بشرية لكل ملف ولا إثباتاً لصحة السلوك) |
| المخاطر | high 57، medium 117، low 1693 |
| القرار (بلا DELETE) | ADAPTER 1000، KEEP 765، DOCUMENT 78، UPGRADE 24 (سكربتات تحقق يدوية بلا اختبار مباشر) |
| غير المحسوم | 17 = كل `src/` غير README (Q-002) — الأسماء والسبب والخطوة في `INVENTORY_SUMMARY_f8b2d48.md` |

### الأدلة
| الفحص | النتيجة | السجل |
|---|---|---|
| إحصاء اللقطة القابل لإعادة الإنتاج | 1867؛ ls-files = ls-tree؛ worktree نظيف (0 ملف متغير) | `logs/raw/052_r155_snapshot_stats.log` |
| pytest + تغطية في worktree نظيف عند الأساس | 967/4/4، rc=0؛ 1146 ملفاً بتغطية؛ 87.3% دون `tests/` | `logs/raw/053_r155_pytest_coverage_base.log`، `053_r155_coverage_files.json` |
| التوليد + الحتمية | self_check=PASS؛ sha256 متطابق ×3 (منها worktree نظيف)؛ مجموعة المسارات = ls-files؛ ملفات الدفعة داخل الجرد = 0 | `logs/raw/054_r155_inventory_generation.log` |
| طفرات المولّد (13) | 13/13 مقتولة | `logs/raw/055_r155_mutation_tests.log` |
| pytest tests/ | 984 ناجح (967 + 17 جديدة) + 4 متخطى + 4 مستبعد، rc=0 | `logs/raw/056_r155_pytest.log` |
| ruff F,E722 (+ سكربتات البوابة والمولّد) | صفر | `logs/raw/057_r155_ruff.log` |
| بناء web/ والجذر | rc=0 ×4 | `logs/raw/058`..`061` |
| بوابات الحوكمة محلياً + عدّاد الخارطة | انظر السجل | `logs/raw/062_r155_local_gates.log` |

### مرفقات الدفعة
مسرودة في [`inventory/README.md`](../../governance/roadmaps/revival-scale/inventory/README.md#مرفقات-الدفعة-ليست-ضمن-لقطة-الأساس) — ليست ضمن لقطة الأساس ولا صفوفاً في الجرد.

### حدود صادقة
- الأدوار مستنتجة آلياً من AST/git/CI/تغطية؛ أنماط تحميل تُبنى وقت التشغيل من مصادر خارجية قد تفوتها (RISK-032) — R-156 يتحقق تشغيلياً.
- لا يُعلن فهم كامل للمستودع (17 غير محسوم). لا أهداف سعة مقيسة ولا جاهزية إنتاجية.
- R-155 يتحول إلى done في الدفعة التالية (القاعدة 8)؛ لا يبدأ R-156 قبل ذلك.

## قبول R-155 (سُجل داخل دفعة R-156، 2026-10-17)
- PR #169 حالتها MERGED (2026-10-17T03:02:06Z)؛ SHA الدمج `caab69b2d9aa59614856923512f2e10ccca0954f`، أبوه الثاني = الرأس المختبر `54a1fb9429f1d08b6484dfbae76cf67e993f1abd` (من GitHub ومن `git rev-parse`).
- CI على main عند SHA الدمج: التشغيل 38997223656 (push، headSha `caab69b`) success — quality/test/build/docker/audit/network/storage success، publish متخطى (D-011). job ‏test: ‏984 passed/4 skipped/4 deselected على نسختي Python؛ storage: ‏4 passed (`test_r042_live_storage`)؛ network: ‏4 passed، 988 deselected. الحوكمة للـPR: 38996889506 success.
- السجلان: `logs/raw/063_r155_acceptance_main_ci.log` (الحقول + مقتطفات CI الخام) و`064_r156_r155_closure_check.log` (فحص أدلة الحالة بعد التحويل إلى done، قبل أي عمل R-156).
- **حدود**: نجاح CI لا يثبت جاهزية الإنتاج ولا أهداف السعة، ولا يحسم مسألة نشر `src/` خارجياً (Q-002). الحالة `done` لا `accepted` (القبول قرار المالك).

## R-156 — خريطة نقاط الدخول والمسارات (2026-10-17، دفعة m8/r156-entrypoint-map، D-068)

### اللقطة والمخرجات
- اللقطة `caab69b2d9aa59614856923512f2e10ccca0954f` (1887 ملفاً متعقباً)؛ المخرجات في `docs/governance/roadmaps/revival-scale/maps/` (JSON + تقرير عربي + README). التوليد حتمي: sha256 متطابق ×3 منها worktree نظيف (`065`).

### النتيجة
| البعد | القيمة |
|---|---|
| نقاط الدخول | 60: server 2 (Docker CMD، Render start → `server:app`)، cli 26، cli-toplevel 14، tests 2، ci-job 9، frontend-web 2، frontend-root 4 (اصطلاح، Q-002)، shell 1 |
| حواف الاستيراد | 7007: top 6634، lazy 292، dynamic 71، conditional 10 |
| إغلاق الخادم | 34 ملفاً (top) / 90 (كل الأنواع) |
| المسارات | 24 (منها 2 شرطيان على `web/dist`، و3 مسجلة داخل دالة) |
| متغيرات البيئة | 57 |
| غير محلَّل/غير محسوم | 30: 7 صور، 4 SQL، 2 shell، 17 `src/` (Q-002) — بسبب وإجراء تالٍ لكل منها |
| مشكلات الفحص الذاتي | 0 |

### ما ثبت بالتشغيل (لا بالرسم وحده)
- `066`: مسارات التطبيق الحقيقي 24 = مسارات الخريطة 24 (لا زيادة ولا نقص)؛ `import server` حمّل 41 وحدة من المستودع: كل الإغلاق الأعلى (34) محمّل ولا وحدة خارج الإغلاق الكامل (90) — الباقي كسول/شرطي/ديناميكي يحتاج تمريناً وقت التشغيل وليس ميتاً؛ 20 حزمة تخصص حُمّلت بـ`impl='specialists'` كلها ضمن 21 هدفاً ديناميكياً متوقعاً؛ 39 متغير بيئة قرأها كود المستودع وقت التشغيل، 39 منها في الخريطة.
- **اكتشاف**: قبل الإصلاح كشف تتبع البيئة 9 متغيرات غائبة (`AGOS_SSE_*` ×8 عبر `_LIMITS`، و`RENDER` عبر `PLATFORM_MARKERS`) — أُصلح المولّد وغُطي باختبار وحدة.
- `067`: 22 اختبار smoke/تكامل — smoke HTTP لكل مسار بلا 5xx (عدا تيار SSE اللانهائي المغطى في اختبارات R-144)، إقلاع uvicorn حقيقي لهدف Docker/Render و`/health/live` ‏200، `run.py` عبر stdin، استيراد كل CLI محروس و`--help` لكل argparse، ترجمة كل سكربت مستوى أعلى، تجميع pytest ملفاً بملف = الخريطة؛ والواجهة `web/` ‏31/31.
- `068`: طفرات المولّد 11/11 مقتولة.

### فحوص الأساس
- `069` pytest ‏1006 passed/4 skipped/4 deselected (= 984 + 22)؛ `070` ruff صفر؛ `071` بناء `web/`؛ `072` بناء الجذر (`src/` — لم يُعطَّل)؛ `073` البوابات المحلية.

### غير مختبر / غير محسوم
- دور نشر `src/` (Q-002)؛ الجاهزية الإنتاجية؛ أهداف السعة؛ التجميع المعلَن للأنظمة الفرعية (مراجعة بشرية)؛ الوحدات الكسولة التي لم تُمرَّن؛ سكربتات المسابير التي تحتاج خادماً حياً (ترجمة/استيراد فقط)؛ بناء Docker (يُثبت في job ‏docker في CI لا محلياً)؛ التخزين الحي ومزودات LLM المدفوعة.

## قبول R-156 (سُجل داخل دفعة R-157، 2026-10-17)
- PR #170 حالتها MERGED (2026-10-17T04:24:57Z)؛ SHA الدمج `228b9560f74f8ef9035a57ce1e460362ac9d3a2c`، أبوه الثاني = الرأس المختبر `72c573f676033cf7e58a3075c1e3ed7a7dbd40d8` (من GitHub ومن `git rev-parse`).
- CI على main عند SHA الدمج: التشغيل 39002479809 (push، headSha `228b956`) **success** — quality/test/network/docker/build/audit/storage success، publish متخطى (D-011). job ‏test: ‏1006 passed/4 skipped/4 deselected على نسختي Python (= `069_r156_pytest.log`). **الاختبارات المتخطاة الأربعة** هي `tests/test_r042_live_storage.py` (تحتاج `R042_LIVE=1`) ويُتحقق منها عبر job ‏storage: ‏4 passed في التشغيل نفسه. **المستبعدة الأربعة** هي علامة `network` في مجموعة pytest الافتراضية ويُتحقق منها عبر job ‏network: ‏4 passed، 1010 deselected. الحوكمة للـPR: 39002012148 success بخطواتها الأربع (no-delete، atomic v1، atomic v2، status evidence).
- السجلان: `logs/raw/074_r156_acceptance_main_ci.log` (الحقول + مقتطفات CI الخام) و`075_r157_r156_closure_check.log` (فحص أدلة الحالة بعد التحويل إلى done، قبل أي عمل R-157).
- **حدود**: نجاح CI لا يثبت جاهزية الإنتاج ولا أهداف السعة، ولا يحسم نشر `src/` (Q-002). الحالة `done` لا `accepted` (القبول قرار المالك).

## R-157 — توحيد عقود القدرات وواجهات التوافق (2026-10-17، دفعة m8/r157-contracts-parity، PR #171، D-069)

### الأساس والمخرجات
- الأساس `228b9560f74f8ef9035a57ce1e460362ac9d3a2c`؛ المصفوفة `docs/governance/roadmaps/revival-scale/maps/CAPABILITY_CONTRACTS_228b956.md` (سبع قدرات C1..C7).
- كود: `agent_civilization/agents/real/runtime_contract.py` (جديد)، `specialist_runtime.py` (المحوّل `_run_inner`)، `real_agents.py` (القاعدة المشتركة، مخرجات مطابقة)، `agos_kernel/infrastructure/health.py` (`set_civilization_provider`، `civilization_status`)، `server.py` (تسجيل المزوّد، `/health/ready` بالقاعدة المشتركة). لا ملف محذوف أو منقول أو معاد تسميته.

### الدليل قبل التعديل (الفرق حقيقي لا اسمي)
- `076`: على كود التشغيل دون تعديل، 3 اختبارات C1 تفشل — تحت `specialists` يعود استثناء المعالج `"RuntimeError: internal secret …"` (تسريب يخالف R-149) و`UnsafeURLError` بصيغة `"UnsafeURLError: …"`، ويُخزَّن الفشل في ذاكرة المستأجر وتعلّمه، بينما `real` يعيد النوع فقط ولا يخزن شيئاً. الحالات المرجعية العشرون وحالات الرفض متطابقة أصلاً.
- `077`: `/health` يعلن «Civilization active with 1000 agents» مع `AGENTS_PER_SPEC=2` بينما `/health/ready` و`/api/system/status` يعلنان 40؛ وفي حضارة فارغة أو بلا وكلاء يقول `/health` healthy و`/health/ready` ‏503. (فشل اختبارين في `077` سببه غياب الواجهة الجديدة — AttributeError — ولا يُحتسب دليلاً سلوكياً.)

### ما وُحّد فعلياً وما لم يوحَّد
| القدرة | النتيجة | الدليل |
|---|---|---|
| C1 وقت تشغيل الوكيل | وُحّد — متكافئ على 20 تخصصاً + 5 حالات حدّية + الأخطاء + الآثار الجانبية؛ فرقان مقصودان موثقان (`impl`، رسالة «لا معالج») وسلوك قديم محفوظ (عدّ الفئة المغلّفة لنتيجة بلا `ok`) | `083`، `080` |
| C2 صحة الحضارة | وُحّد — الآراء الثلاثة متطابقة على الحضارة الحية، وحالات عدم الجاهزية 503 في `/health` و`/health/ready` معاً، بلا تسريب | `083`، `080` (40 = 40 = 40 على الوقتين) |
| C3 الذاكرة والتعلّم | مثبت بلا تعديل (طبقات) | `083` |
| C4 الرسائل | مثبت (إعادة تصدير، هوية الكائن) | `083` |
| C5 اختيار الوكيل | غير موحّد — G-157-3 | `083` (تثبيت السياسات الثلاث) |
| C6 `src/`/`web/` | غير محسوم — Q-002، G-157-4 | `082`، `083` |
| C7 hub مقابل pipeline | خارج التوحيد (عقدان بلا مستهلك مشترك) | المصفوفة |

### الاختبارات (محلي — Python 3.12.13)
- `083`: الحزمة الكاملة **1056 passed, 4 skipped, 4 deselected** (= 1006 + 50 جديداً)، منها `tests/test_r157_contract_parity.py` ‏50/50. المتخطاة الأربعة = التخزين الحي (job ‏storage في CI)، المستبعدة الأربعة = `network` (job ‏network في CI).
- `078`/`079`: 13 طفرة دلالية (القاعدة، المحوّل، الصحة، الخادم، سياسة الاختيار). الجولة 1: 12/13 — نجت M6 (افتراضي `ok` حين يغيب المفتاح) فأُضيف `test_c1_result_without_ok_key_matches_and_keeps_legacy_inner_count`؛ الجولة 2: **13/13**. القتل يُحتسب فقط بفشل assertion بعد ترجمة ناجحة، لا بخطأ صياغة أو استيراد.
- `081`: مجموعات API/الأمن/الذاكرة/التوجيه مع خادم مبني على `AGOS_AGENT_IMPL=specialists`: 398 ناجحاً و3 فاشلة في `test_r149_security_qa_gaps.py` — تفشل الثلاثة نفسها على الأساس `228b956` (تجربة ضابطة)، لأنها ترقّع ربط `real` وحده (G-157-5)؛ ليست انحداراً.
- `082`: لا تغيير في `src/` و`web/`؛ `web/` ‏npm ci + build + ‏31/31، والجذر npm ci + eslint + build — نجحت على Node 22.22.0.
- `084`: ruff (نطاق CI) صفر، ترجمة الملفات المعدلة، وبوابات no-delete وatomic v1 وv2 وstatus evidence محلياً.

### التحقق التشغيلي المباشر (`080`)
- عملية uvicorn حقيقية لكل وقت (`AGENTS_PER_SPEC=2`، مفتاح API عشوائي غير مسجل): `/health` و`/health/ready` و`/api/system/status` = 40؛ `/api/agents` ‏40 بمفاتيح العقد (+`impl` في specialists)؛ ثلاث مهام عبر `POST /api/tasks` (نجاح analyst، رفض validator، رفض SSRF) بالنتائج نفسها على الوقتين، و`/api/memory` ‏3 لكل منهما.

### نتائج CI
- تُسجَّل بعد الدمج في دفعة قبول R-157 (SHA المختبر، SHA الدمج، رقم التشغيل). لا يُعاد استخدام نتائج R-156 كفحص لهذه الدفعة.

### غير مختبر / غير محسوم
- حقن استثناء معالج في عملية خادم حية (مغطى داخل العملية فقط)؛ تشغيل CI على `specialists` (CI يشغّل `real`)؛ التخزين الحي ومزودات LLM المدفوعة؛ بناء Docker محلياً (job ‏docker في CI).
- الفجوات G-157-1..G-157-6 (المصفوفة)؛ Q-002 مفتوح؛ لا جاهزية إنتاجية ولا أهداف سعة.
- R-157 تبقى `awaiting_merge` حتى التحقق من الدمج وCI على SHA الدمج؛ لا يبدأ R-158 قبل ذلك.

## قبول R-157 (سُجل داخل دفعة R-158، 2026-10-17)
- PR #171 حالتها MERGED. SHA الدمج `573793c1f36afd77f3b79247ab9e2d978a397b62`، وأبواه `228b956…` و`1e4d6fa035bcf2262345e239c997b731df4a2974` (الرأس المختبر)، بحسب GitHub و`git rev-parse`.
- CI على main عند SHA الدمج: التشغيل 39005652192 **success**. نجحت quality وtest وnetwork وdocker وbuild وaudit وstorage، وتُخطّي publish (D-011). الحوكمة للـPR: 39005317063 success.
- السجلات:
  - `logs/raw/085_r157_acceptance_main_ci.log`.
  - `086_r158_r157_closure_check.log`: فشل التشغيل الأول لأن مسار دليل كان خارج `docs/audit/`. صُحح الحقل ونجحت إعادة التشغيل، والتشغيلان محفوظان.
- الحالة `done` لا `accepted`.

## R-158 — خط أساس اختبارات لكل ملف (2026-10-17، دفعة m8/r158-test-baseline، PR #172، D-070)

### اللقطة والمولّد
- اللقطة `573793c1f36afd77f3b79247ab9e2d978a397b62`، والمخرجات في `docs/governance/roadmaps/revival-scale/baseline/`.
- المولّد `scripts/build_test_baseline.py`. استخدمت التشغيلات 088–090 النسخة sha256 `c630d8c6…`. النسخة الملتزمة `52d3fbd9…` تختلف عنها في ترتيب التقرير ووضع `--render-from` فقط (`093`).
- `087`: JSON تشغيل CI ‏39005652192 على SHA نفسه، وهو مُدخَل `--ci-json`.

### إعادة الإنتاج
- `088` و`089`: worktree جديد. `090`: `gh repo clone` جديد، تم التحقق من نظافته (0 تغييرات)، ومنه الملفات الملتزمة.
- `091`: البصمة نفسها `d2523d8ca38443c6e8fa84be37445f5dc5362accdd41caa4d292737969c33474` في التشغيلات الثلاثة، و0 فرق في الجزء الحتمي. الفروق الكاملة محصورة في `raw_log` و`checkout`.
- اختبار الشبكة الخارجي فشل في التشغيلات الثلاثة بالاختبار نفسه، وهو مستبعد من البصمة بطبيعته.

### النتائج عند اللقطة (محلي — لا تُقرأ كنتيجة CI)
- **pytest**:
  - `real` مع التغطية: 1056 passed, 4 skipped, 4 deselected (exit 0).
  - `specialists`: 3 failed, 1053 passed, 4 skipped, 4 deselected (exit 1).
  - الفئات (real/specialists): contract 146/146، integration 529/526 مع 3 failed، unit 381/381، live 4 skipped.
- **network**: 3 passed، 1 failed (`test_live_keyless_llm_and_translation`، خطأ HTTP من MyMemory، G-158-1).
- **storage** (تحقق تشغيلي محلي، PostgreSQL 17.11 + PostgREST 12.2.3): 4 passed.
- **compile وimport**: compile ‏1194/1194، وimport ‏1092/1092.
- **ruff**: نطاق CI 0، وخارجه 5 (G-158-3).
- **الواجهتان**:
  - `web/`: ci وbuild وtest ‏31/31.
  - `src/`: ci وeslint وbuild ناجحة، و`tsc` exit 2 بـ4 أخطاء (G-158-2).
- **Deploy وCI**: الفحص الذاتي للخريطة وقراءة YAML ناجحان.
- **الحوكمة**: البوابات الثلاث ناجحة على اللقطة.
- **الأدلة**: 2 `.json` ليست JSON (G-158-4)، وسجل واحد ليس UTF-8 (G-158-5).
- **docs**: روابط 60/60، وPNG ‏7/7.

### G-157-5 (السبب، الإصلاح، الدليل)
- `092`: الاختبارات الأصلية تفشل تحت `specialists` على `573793c` وعلى `228b956` بالتأكيد نفسه (`'no code to run' == 'RuntimeError'`)، أي أن الحقن لم يُنفَّذ.
- الاختبارات المُصلحة تنجح في الوضعين على `573793c`، وتفشل على `228b956` تحت `specialists` مع النص الخام والعلامة السرية، أي التسرب الذي أصلحه R-157.
- طفرة إلغاء المحوّل في الفرع: قتلها الاختبار المُصلح واختبار `test_g157_5_*`.
- لم يُعدَّل أي كود تشغيلي، ولم تتغير التأكيدات.

### ثغرات
- مراجعة G-157-1..6 والثغرات الجديدة G-158-1..8، مع التكرار والإجراء لكل منها: `baseline/README.md`.

### ما يثبته الفرع (محلي) وما سيثبته CI
- `094`: الحزمة كاملة على رأس الفرع بالوضعين.
- `095`: ruff (نطاق CI) والبوابات الأربع.
- `096`: أول CI للـPR (39012932109 على `6d424c2`) **فشل** في `test` (1 failed, 1097 passed). السبب أن `checkout` بعمق 1 لا يحوي commit اللقطة، والاختبار استدعى `git ls-tree <sha>`، وهو عيب في تصميم الاختبار. صار الاختبار يقارن تطابقاً تاماً حين يوجد الـcommit، وإلا يتحقق من أن اللقطة مجموعة جزئية مرتبة بلا تكرار من ملفات HEAD (بوابة عدم الحذف). لا skip. الإصلاح الأول ترك متغيراً غير معرّف، فكُشف في clone بعمق 1 وصُحح، والتسلسل كاملاً في السجل. سبقه تشغيل على `9cc76bc` فشل لأن حقل `pr=#173` كان مؤقتاً قبل وجود رقم الـPR، فرفضه فحص الحالة كما هو مصمم.
- `097`: الحزمة بالوضعين والبوابات بعد الإصلاح.
- CI على الـPR وعلى SHA الدمج يُسجَّل بعد الدمج في دفعة القبول التالية، ولا يُفترض مسبقاً.
- **لم يُختبر**: بناء Docker محلياً، وRender، وLLM مدفوع، وSupabase سحابي، والحمل أو السعة. لا ادعاء جاهزية إنتاج.

## قبول R-158 (سُجل داخل دفعة R-159، 2026-10-17)
- PR #172 حالتها MERGED. SHA الدمج `7aa5ed6d179b6bce0358e0912b8f353bead3bfd5`، وأبواه `573793c…` و`ed078cef1ec4f1c7bf1269295272fc8cfcd3fc4d` (الرأس المختبر). لا توجد PR أحدث.
- CI على main عند SHA الدمج: التشغيل 39013981294 **success**. نجحت quality وtest وnetwork وaudit وbuild وstorage وdocker، وتُخطّي publish. أسطر pytest الحرفية في `logs/raw/098_r158_acceptance_main_ci.log`:
  - test: ‏1098 passed, 4 skipped, 4 deselected على Python 3.11.17، والعدد نفسه على 3.12.15، والعدد نفسه في خطوة `AGOS_AGENT_IMPL=specialists`.
  - network: ‏4 passed، وstorage: ‏4 passed.
- PR CI ‏39013490808 والحوكمة 39013490844 ناجحان.
- `099`: فحص أدلة الحالة ناجح بعد التحويل إلى `done`، وجدول M8 مطابق لـROADMAP (34 بنداً، 0 تعارض).
- نتائج اللقطة `573793c` (088–091) نتائج خط أساس، لا نتائج CI لهذا الدمج.

## R-159 — رصد موحد وآمن (2026-10-17، دفعة m8/r159-observability، PR #173، D-071)

### ما أُضيف
- `agent_civilization/core/telemetry.py` ونقاط رصد في المواقع الفعلية:

| النوع | الموضع | الحقول الإضافية |
|---|---|---|
| http | `TelemetryMiddleware` (server.py) | method، route (قالب)، status |
| task | `create_task` (queued)، `_run_task` (run)، `chat` (recorded) | specialization |
| workflow | `execute_plan`: كل مرحلة | stage، stage_index، stage_count، specialization |
| agent | `RealSpecialistAgent`، `SpecialistBackedAgent`، `ExternalAgent` | impl، agent، specialization |
| tool | `real_dispatcher.dispatch` | tool |
| memory | `run_with_experience` (recall/record) | recalled، stored، reason |
| llm | `LLMClient.complete` | provider، model، http_status، reason |
| storage | `SupabaseStorage` (8 عمليات) | http_status، reason |

### أنواع الأدلة
- **وحدات** (محلي و CI): مخطط السجل، والتنقيح، والاسم المستعار، والإلغاء، والربط، والإيقاف، وحدود `op`.
- **تكامل محلي بـmocks** (محلي و CI؛ fake OpenRouter وfake PostgREST على loopback):
  - سلسلة واحدة من `/api/chat` إلى http ثم llm ثم agent (self_developer) ثم memory ثم tool ثم workflow (إعادة اختبار هدف فاشل محفوظ) ثم agent ثم tool، ثم storage. كل spans في trace واحد وشجرة واحدة جذرها http، وspans التخزين تحمل task_id.
  - المهمة الخلفية، وأخطاء المزود (500 وقطع النقل)، ورفض التخزين 401، واستثناء الأداة، وفشل الذاكرة.
  - عدم توريث السياق بعد طلب فاشل أو ملغى، و`/metrics` بلا معرّفات.
- **تكامل محلي حقيقي** (`103`، محلي فقط): PostgREST 12.2.3 وPostgreSQL 17.11 على loopback، في الوضعين.
  - مفتاح صحيح: 10 spans تخزين `ok`، و4 صفوف فعلية في الجدول.
  - مفتاح معدَّل: كل span ‏`fail`/401.
  - لا تسريب للـJWT ولا الـprompt ولا المستأجر.
- **حي**: لا شيء. لا LLM مدفوع، ولا Supabase سحابي، ولا Render.

### الطفرات (`101`)
- 15 طفرة على الكود الفعلي: التنقيح، والاسم المستعار، ونص الاستثناء، وtask_id كوسم، و`op` غير محدود، وعدم إعادة الضبط، وربط الأب، و`bind_task`، والإلغاء، وسجل الذاكرة، ونتيجة التخزين، وحالة المزود، وربط المهمة، وspan المرحلة، ونتيجة الأداة.
- أول تشغيل: 14/15. نجت M13 لأنها مكافئة على مسار `create_task`، فأضيف `test_run_task_binds_its_own_task_id_whatever_the_caller`. إعادة التشغيل: **15/15**.

### الكلفة (`102`)
- 5 جولات متداخلة × 4 متغيرات، 2000 طلب مقيس بعد 300 إحماء، داخل العملية، عميل واحد، بيئة sandbox مشتركة.
- النتائج (الوسيط):

| المتغير | زمن الطلب | الفرق عن الأساس |
|---|---|---|
| base (‏`7aa5ed6`) | 3.109 مللي ثانية | — |
| `off` | 3.138 | ضمن تذبذب الأساس (3.079..3.146) |
| spans ومقاييس بلا كتابة سجل | 3.472 | **+0.363 (+11.7%)** |
| مع كتابة سجل ملفي | 3.676 | **+0.567 (+18.2%)**، و1999.6 بايت و6 سجلات لكل طلب |

- maxrss: فرق أقل من 1 ميغابايت.
- **ليست مهملة نسبةً لهذا المسار الداخلي**. لم يُقس مسار فيه I/O حقيقي، ولا يُعلن اجتياز معيار.

### إخفاقات وجدت وعولجت
- `100`: الاختبار الجديد أخفق 1/30 تشغيلاً بسبب الـfake (جسم مع 204). أُصلح، و0/30 بعده.
- اختبارات كتبتها أخفقت أولاً لأسباب في الاختبار نفسه: حقل `message` لا `prompt`، والمسار catch-all `/{full_path}`، وسياق trace تُرك مفتوحاً في اختبار المقاييس. صُححت قبل الالتزام، دون skip.

### ثغرات مفتوحة
- **G-159-1**: سجلات `{e}` خام خارج المكونات المرصودة (SECURITY.md).
- **G-159-2**: الكلفة مقيسة على مسار داخلي فقط.
- **G-159-3**: لا مُصدِّر traces (OTLP أو غيره)، ولا يُعاد `trace_id` للعميل. كلاهما قرار مالك أو عقد (R-160).
- **G-159-4**: الاسم المستعار ثابت داخل العملية فقط ما لم يُضبط `AGOS_TELEMETRY_TENANT_KEY`، وضبطه في الإنتاج قرار مالك.
- **G-159-5**: لا تكامل حي.
- G-157-1/2/3/4/6 لم تُغلق بالرصد، وQ-002 مفتوح.

### الحزمة والبوابات
- `104`: الحزمة كاملة على الفرع بالوضعين.
- `105`: ruff والبوابات الأربع.
- `106`: أول CI للـPR (39017672731 على `0a26c88`) **فشل** في `test` (1 failed, 1129 passed). السبب أن `test_consecutive_requests_never_share_or_inherit_a_trace` افترض وجود المسار catch-all `/{full_path:path}`، وهو لا يُسجَّل إلا بوجود `web/dist`: موجود محلياً وغائب في CI. عيب في تصميم الاختبار. صار التوقع مشتقاً من مسارات التطبيق الفعلية. النتيجة 32 passed بوجود `web/dist` وبغيابه. طفرة «المسار الخام كوسم» تُقتل في الحالة المشابهة لـCI. سبقه تشغيل على `ae182d9` فشل لأن `pr=#PRNUM` كان مؤقتاً.
- `107`: الحزمة بالوضعين دون `web/dist` (مثل CI)، مع البوابات.
- CI للـPR وعلى SHA الدمج يُسجَّل بعد الدمج في دفعة القبول التالية.

## قبول R-159 (سُجل داخل دفعة R-160، 2026-10-17)
- PR #173 حالتها MERGED (2026-10-17T08:49:28Z). SHA الدمج `49faab68fefee582b83f14fc64808e309adb7522`، وأبواه `7aa5ed6…` و`c8f722f91858c78d1105d1523297cd8d68c9b333` (الرأس المختبر). لا PR مفتوحة وقت التسجيل.
- CI على main عند SHA الدمج: التشغيل 39018809541 (push، headSha = SHA الدمج) **success**. نجحت quality وtest وnetwork وstorage وbuild وaudit وdocker، وتُخطّي publish (D-011 — ليس نشراً ولا يُحتسب نجاحاً). أسطر pytest الحرفية في `logs/raw/108_r159_acceptance_main_ci.log`:
  - test: ‏1130 passed, 4 skipped, 4 deselected على 3.11 (مع live suite)، والعدد نفسه على 3.12، والعدد نفسه في خطوة `AGOS_AGENT_IMPL=specialists`.
  - 4 skipped = `tests/test_r042_live_storage.py` (تحتاج R042_LIVE=1) — نُفذت في job ‏storage على PostgreSQL+PostgREST محليين داخل CI (4 passed). 4 deselected = علامة network (addopts) — نُفذت في job ‏network (4 passed).
- PR CI ‏39018252604 والحوكمة 39018252594 ناجحان.
- `109`: فحص أدلة الحالة: التشغيل الأول rc=1 (سجل 109 نفسه لم يكن موجوداً)، ثم rc=0 بعد إنشائه دون تغيير آخر؛ جدول M8 مطابق لـROADMAP (34 بنداً، 0 تعارض: 9 done، 1 awaiting_merge، 24 planned).

## R-160 — عقود قابلة للتحقق (2026-10-17، دفعة m8/r160-contracts، PR #174، D-072)

### ما أُضيف
- `agent_civilization/core/contracts.py`: 22 عقداً + 4 نماذج طلب قائمة، بإصدار `v1` لكل منها؛ `scripts/export_contract_schemas.py` (كتابة/`--check` مع تصنيف BREAKING/ADDITIVE واشتراط بقاء الإصدار السابق)؛ `docs/governance/contracts/` (26 مخططاً، 4 أمثلة مشتركة، README).
- `scripts/scan_raw_exception_logs.py` (G-159-1)، و`web/src/taskView.js`.

### تغطية حالات P2

| الحالة | أين تُختبر |
|---|---|
| valid | كل مسارات API المذكورة، و20/20 تخصصاً بالوضعين، وأطر SSE، وأجسام التخزين، والطابور |
| invalid | 7 طلبات ⇒ 422، وJSON مشوه، و400/401/404/409، وhandler_result بأنواع خاطئة |
| unknown fields | طلبات بحقول زائدة (تُتجاهل، tenant_id لا يُنفَّذ)، واستجابات صارمة تكشف حقلاً مضافاً، وقارئ الطابور والواجهة يتجاهلان الأحدث |
| malformed outputs | completion (LLM بمسارين، وكيل خارجي)، وقراءات تخزين، وحمولة طابور، ومعالج يرفع استثناءً، وسجلات مشوهة في الواجهة |
| version drift | الكود مقابل المخطط المُلتزم، و9 أنواع تغيير، وانحراف مُحاكى BREAKING وADDITIVE، ورفع إصدار دون السابق، والأمثلة مقابل الاستجابات الحية |
| cancelled request | إلغاء التشغيل الخلفي، واستثناء التشغيل، واستثناء execute، وقطع SSE (داخل العملية وعبر uvicorn)، وقطع العميل لطلب بطيء (uvicorn) |
| duplicate request | مهام وchat مكررة (داخل العملية وعبر uvicorn)، وحذف وكيل خارجي مرتين، وenqueue مكرر في وضعي الطابور |

### أنواع الأدلة
- **وحدات / mock**: النماذج، ومدقق التوافق، وRedis وهمي، ونقل LLM مُرقَّع، والتطبيق داخل العملية.
- **تكامل محلي**: fake PostgREST وfake OpenAI-compatible على 127.0.0.1 عبر العملاء الحقيقيين؛ والخادم الحقيقي تحت uvicorn عبر TCP (`112`).
- **حي**: لا شيء.

### قبل/بعد
- `110` (G-159-1): الماسح على main ‏38 موقعاً، 37 غير مراجَع (rc=1)؛ بعدها 3 مراجَعة و0 غير مراجَع (rc=0). اختبارات الإخفاء: main ‏34 فشل/5 نجاح ⇒ 39/39.
- `111` (العقود): main ‏27 فشل/62 نجاح (84 + 5 uvicorn) ⇒ 89/89. مخططات main المولّدة = المُلتزمة: شكل الاستجابات العامة لم يتغير، والذي تغيّر قيم داخلها (ok/status/error) موثقة في API.md. الواجهة: main يقرأ `agent_name`/`elapsed_s` غير الموجودين في سجلات الذاكرة.

### إخفاقات وجدت وعولجت (لا skip ولا xfail)
- أول تشغيل للحزمة على venvs المقفلة: خطأ جمع — `httpx` غير موجود؛ التبعيات المقفلة تحمل `httpx2` (عميل اختبار Starlette 1.8). صار الاستيراد `httpx2` مع رجوع إلى `httpx` كما تفعل Starlette، دون تبعية جديدة (`113`).
- تشغيل `specialists`: فشل `test_shared_examples_validate_and_match_live_shapes` لأن هذا الوضع يضيف `impl` الموثق إلى outcome؛ صار الاختبار يتوقع هذا المفتاح صراحة في هذا الوضع فقط (`113`).
- اختبار `/health` كان معتمداً على الترتيب (مسار المزود المسجَّل)، فثُبِّت المسار المستقل في الاختبار.
- مدقق التوافق لم يكشف enum موسَّعاً (pydantic يضع `type` مع `enum`)؛ صار يقارن القيم أيضاً.
- اختبار uvicorn قرأ مقياساً خاطئاً بمطابقة البادئة؛ صحح قبل الالتزام (`112`).
- بعد الالتزام الأول فشل `tests/test_r158_test_baseline.py::test_every_tracked_file_at_head_is_assigned`: 30 ملفاً جديداً في `docs/contracts/` لا تنتمي لأي مجموعة في خط أساس R-158. نُقلت (داخل الفرع، وهي إضافات لا ملفات متعقبة على main — D-064 لا ينطبق) إلى `docs/governance/contracts/` حيث تفحصها مجموعة governance-data (تحليل JSON)، بدل تعديل مولّد خط الأساس الذي يغيّر بصمته المعاد إنتاجها. السجلات `110`–`112` تذكر المسار القديم كما كان وقت التشغيل (`113`).

### ثغرات مفتوحة
- **G-160-1**: نص الاستثناء الخام في **نتائج** مهام مرئية للمستأجر (llm.py نتيجة النقل، real_dispatcher، agent_factory، last_err، OSError عند تسجيل وكيل خارجي). G-159-1 أُغلق للسجلات و`/health` فقط.
- **G-160-2**: صف التخزين لا يُعاد كتابته عند إلغاء التشغيل (يبقى `running` في قاعدة البيانات).
- **G-160-3**: سجل execute ‏`completed` بغض النظر عن نجاح المراحل — قاعدة التجميع غير محسومة.
- **G-160-4**: لا مفتاح idempotency — الطلب المكرر مهمتان (موثق كعقد v1).
- **G-160-5**: لا `id:` ولا Last-Event-ID في SSE — لا إعادة تشغيل.
- **G-160-6**: `src/` خارج العقود (Q-002).
- حزمة `redis` ليست في `requirements.lock`، فوضع Redis مختبر بعميل وهمي فقط.
- G-157-3، ومفتاح الاسم المستعار (G-159-4)، ومُصدِّر traces وإرجاع trace_id (G-159-3) غير محسومة، وQ-002 مفتوح.

### الحزمة والبوابات
- `113`: الحزمة كاملة بالوضعين على Python 3.11 و3.12 (تبعيات مقفلة).
- `114`: ruff، وعدم الحذف، والذرية v1/v2، وأدلة الحالة، و`--check` العقود، والماسح، و`git diff --check`، و`web/` (test + build)، والبناء الجذري.
- CI للـPR وعلى SHA الدمج يُسجَّل بعد الدمج في دفعة القبول التالية.
- **لم يُختبر**: Supabase سحابي، وLLM مدفوع، وRender، وRedis حقيقي، وبناء Docker محلياً، والحمل أو السعة. لا ادعاء جاهزية إنتاج.

## قبول R-160 (سُجل داخل دفعة R-161، 2026-10-17)
- PR #174 حالتها MERGED. SHA الدمج `dadbba1d1fb01cdf17a7a01c54bbb088f583b086`، وأبواه `49faab6…` و`33d10bdcba57b9c2ec87e38a57089e9d05306e50` (الرأس المختبر). `115`.
- CI على main عند SHA الدمج: التشغيل 39059547216 (push، headSha = SHA الدمج) **success**. نجحت quality وtest وnetwork وstorage وbuild وaudit وdocker، وتُخطّي publish (D-011؛ ليس نجاحاً ولا نشراً).
  - test: خطوات pytest الثلاث — Python 3.11.17 (الخطوة اسمها «live test suite» لكنها الحزمة العادية بلا شبكة مع التغطية)، و3.12.15، و3.12.15 بـ`AGOS_AGENT_IMPL=specialists` — كلٌّ منها 1258 passed, 4 skipped, 4 deselected، والمقتطفات حرفية من سجل التشغيل.
  - 4 skipped = `tests/test_r042_live_storage.py` — نُفذت في job ‏storage على PostgreSQL 17 + PostgREST 12.2.3 داخل CI (26/26 فحصاً، و4 passed). 4 deselected = علامة network — نُفذت في job ‏network (4 passed).
- PR CI ‏39058963368 والحوكمة 39058963375 ناجحان على الرأس المختبر.
- `116`: فحص أدلة الحالة (انظر أدناه). R-160 ‏`done` (لا `accepted`).
- **لم يُختبر في PR #174**: تكامل حي، ووضع Redis بحزمة Redis حقيقية. G-160-1..6 مفتوحة.

## R-161 — تكامل اختياري آمن ومصنف (2026-10-17، دفعة m8/r161-live-integration، PR #175، D-073)

### ما أُضيف
- `scripts/r161_live_integration.py`: مشغل الأهداف storage وllm وexternal وmcp. يعيد استخدام `SupabaseStorage` و`LLMClient` و`ExternalAgentAdapter` وعقود `task_row` و`llm_result` و`agent_outcome`.
- `.github/workflows/live-integration.yml`: التشغيل الحي اليدوي.
- `tests/test_r161_live_integration.py` (46).
- `LLMClient(url=None)`، وتشغيل المشغل في نهاية `scripts/r042/run_local_stack.sh` (job ‏storage) مع رفع تقريره.

### الفئات والنتائج في هذه الدفعة

| الهدف | mock | local_integration | live_integration |
|---|---|---|---|
| storage | PASS (`117`، `118`) — خدمات وهمية على 127.0.0.1 | **PASS** في CI job ‏storage لـPR #175 (التشغيل 39067354247) على PostgreSQL 17 + PostgREST 12.2.3 حقيقيين على loopback: 9/9 فحوص والتنظيف ok (`121`)؛ في بيئة العمل NOT_RUN (لا PostgreSQL ولا Docker) | **NOT_RUN** |
| llm | PASS (`117`، `118`) | لا خادم LLM حقيقي محلي — لا شيء | **NOT_RUN** |
| external | PASS (`117`، `118`) | لا شيء | **NOT_RUN** |
| mcp | BLOCKED | BLOCKED | BLOCKED — لا عميل MCP |

- **لماذا لم يُشغَّل live_integration**: لا بيئة `r161-staging` ولا بيانات اعتماد اختبار مؤقتة مقدمة من المالك، والتوجيه يمنع إنشاء بيانات اعتماد أو موارد سحابية. لا يُعلن «live integration: PASS».
- job ‏`network` يصل إلى نقاط عامة بلا مفتاح؛ هذا ليس تكاملاً مع حساب اختبار ولا يُحتسب لـR-161.

### متطلبات الاختبار ومكانها

| المتطلب | الاختبار |
|---|---|
| المحلي/الوهمي يعمل بلا أسرار إنتاج | كل اختبارات storage/llm/external بمفاتيح اصطناعية |
| الحي معطل افتراضياً | `test_live_is_refused_unless_explicitly_enabled_allowlisted_and_temporary` (8)، و`test_endpoint_policy` (6) |
| الإعداد الناقص = BLOCKED/NOT_RUN | `test_missing_configuration_is_blocked_by_variable_name_only`، و`test_nothing_requested_is_not_run_and_not_success` |
| الرفض/المهلة/الاستجابة المشوهة ≠ نجاح | `test_storage_provider_failures_are_failures`، و`test_llm_provider_failures_are_failures` (4)، و`test_external_failures_are_failures` (4)، و`test_*timeout*`، و`test_connection_refused_is_a_failure`، و`test_a_pass_needs_a_response`، و`test_live_endpoint_without_network_is_a_failure_never_a_pass` |
| الأسرار غائبة حتى مع الاستثناءات | `test_secrets_never_reach_report_stdout_or_logs_even_on_exceptions` (CLI كامل: استثناء يحمل السر، ومزود يردد المفتاح، وسجلات العميل)، و`test_redactor` |
| التنظيف يحدث وفشله ظاهر | `test_storage_probe_passes_and_cleans_up_only_its_rows`، و`test_cleanup_failure_turns_a_passing_probe_into_a_failure` (2)، و`test_storage_timeout_is_a_failure_and_cleanup_still_runs` |
| الإلغاء | `test_cancelled_run_cleans_up_and_is_reported_as_failure` |
| التقارير تفصل الفئات | `test_report_separates_evidence_classes_and_cli_writes_it`، و`test_exit_codes` |
| الـworkflow | `test_workflow_is_manual_only_and_least_privilege`، و`test_workflow_secrets_only_in_the_runner_step_and_artifact_is_checked`، و`test_ci_never_receives_live_secrets_and_storage_job_runs_the_runner_locally` |

### قبل/بعد
- `117`: على main لا يوجد المشغل ولا الـworkflow، و`LLMClient(url=…)` ‏TypeError، والاختبارات خطأ جمع. على الفرع 46/46 على 3.14.3 و3.11.15 و3.12.13 وفي `specialists`. اختبارات R-159/R-160 المتأثرة بتغيير `LLMClient` ناجحة (121).

### إخفاقات وجدت وعولجت (لا skip ولا xfail)
- اتصال مرفوض كان يُسجل `connected=True` لأن فحص «المستأجر الآخر لا يقرأ» صحيح بلا خادم؛ صار الاتصال يتطلب إجابة فعلية.
- **تسريب**: استثناء غير متوقع داخل probe أسقط المشغل بـtraceback يحمل قيمة السر؛ صار FAIL بنوع الاستثناء فقط، مع معالج أخير منقح في `main()`.
- افتراض خاطئ في اختبار: DNS الوهمي في conftest يُعامل كعام، فقُسم إلى اختبار رفض DNS (BLOCKED) واختبار حي بلا شبكة (FAIL، بلا اتصال).
- تفاصيل أخرى في `117`.

### الحالات المرصودة في تشغيلات CLI (`118`)
- بلا أهداف: NOT_RUN ورمز 3. كل الأهداف بلا إعداد: BLOCKED ورمز 2. نقطة عامة بلا `R161_LIVE`، أو مضيف خارج القائمة، أو بلا انتهاء، أو ببيانات اعتماد في الـURL: BLOCKED قبل أي اتصال.
- وهمي سليم: storage/llm/external ‏PASS (mock) وmcp ‏BLOCKED، فالرمز 2 لا 0. وهمي يرفض بـ401 مع ترديد المفتاح: FAIL ورمز 1، والسر غائب. تنظيف لا يحذف: FAIL «rows of this run are still present after cleanup».

### ثغرات مفتوحة
- **G-161-1**: لم يُنفذ تشغيل حي (لا بيئة ولا بيانات اعتماد).
- **G-161-2**: لا عميل MCP في المستودع؛ الهدف BLOCKED.
- **G-161-3**: عملاء التخزين وLLM يفحصون DNS قبل الاتصال فقط (preflight)، ولا يثبتون العنوان عند الاتصال كما يفعل `ExternalAgentAdapter` (R-146) — نافذة DNS rebinding في التشغيل الحي.
- **G-161-4**: تاريخ الانتهاء إقرار من المشغّل لا يتحقق منه المشغل لدى المزود.
- **G-161-5**: إن شُغِّل الـworkflow قبل أن ينشئ المالك البيئة، ينشئها GitHub بلا حماية (وبلا أسرار، فكل هدف BLOCKED).
- **G-161-6**: التكامل المحلي PostgREST لا Supabase المستضاف (مصادقة/RLS خاصة بـSupabase لم تُختبر)، ولا خادم LLM أو أداة حقيقي محلي (mock فقط).
- **G-160-1..6** لم تُغلق بهذه الاختبارات ولم يُعالج أيٌّ منها. حزمة Redis غير مضافة. Q-002 وG-157-3 ومفتاح الاسم المستعار ومُصدِّر traces وإرجاع trace_id بلا تغيير.

### الحزمة والبوابات
- `116`: فحص أدلة الحالة.
- `119`: الحزمة كاملة بالوضعين على Python 3.11 و3.12 (تبعيات مقفلة).
- `120`: ruff، وعدم الحذف، والذرية v1/v2، وأدلة الحالة، و`--check` العقود، والماسح، و`git diff --check`، و`web/` (test + build)، والبناء الجذري.
- `121`: CI الـPR ‏39067354247 (رأس `de67c53`) success، والحوكمة 39067354159 success: job ‏storage — R-042 ‏26/26 و4 passed، ثم المشغل storage ‏PASS بفئة local_integration (9/9، تنظيف ok، رمز 0)؛ وخطوات pytest الثلاث 1304 passed, 4 skipped, 4 deselected. تعذر تنزيل artifact التقرير من بيئة العمل (HTTP 401)، فالدليل هو مخرج المشغل في سجل الـjob.
- **لم يُختبر**: تكامل حي مع أي مزود أو مشروع، وSupabase المستضاف، وMCP، وRedis حقيقي، والحمل أو السعة. لا ادعاء جاهزية إنتاج.

## R-161b — تحصين R-161 وتسجيل المانع (2026-10-18، دفعة m8/r161b-dns-pinning-env-gate، D-074)

### ما بعد دمج PR #175
- PR #175 حالتها MERGED. SHA الدمج `fcc869b5c51d775cab5bc548a70ccda55e0f76a7`، وأبواه `dadbba1…` والرأس المختبر `c732c75a736b6dec078adc5bc59406bf4bb4165c`.
- CI main ‏39068752136 على SHA الدمج **success**. نجحت quality وtest وnetwork وstorage وbuild وaudit وdocker، وتُخطّي publish (D-011، ليس نشراً).
  - خطوات pytest الثلاث: 1304 passed, 4 skipped, 4 deselected.
  - job ‏storage: المشغل ← `storage PASS class=local_integration host=127.0.0.1 connected=True cleanup=ok`.
- **ليس قبولاً حياً**: live_integration ‏NOT_RUN، وMCP بلا عميل. لذلك R-161 ‏`blocked` (blocker=Q-025)، لا `done`.

### G-161-3 — تثبيت DNS عند الاتصال (مغلقة)
- **الآلية**: `guarded_session_kwargs` في `external_adapter.py`، وتعيد استخدام `GuardedResolver` من SEC-05. يستخدمها `SupabaseStorage(guarded=True)` و`LLMClient(guarded=True)` وجلسة التنظيف في المشغل.
- **قبل** (`122`، main + ملف الاختبار): الفحص المسبق أجاب بعنوان عام، والاتصال حلّ من جديد إلى المستمع الداخلي. تلقى المستمع **8** اتصالات TCP من storage و**1** من LLM. في الملف كله: 22 failed و1 passed (اختبار `GuardedResolver` القائم).
- **بعد**: 23/23 على 3.14.3 و3.11.15 و3.12.13:
  - المستمع الداخلي 0 اتصالات، والهدف FAIL بفئة live_integration بلا اتصال.
  - الحالة الموجبة: حلّ واحد، واتصال واحد بالمستمع المرتبط بالعنوان المفحوص وحده، وHost الأصلي.
  - إجابات مختلطة أو داخلية مرفوضة كاملةً، وفشل الحل مغلق، ولا ذاكرة DNS.
  - التحويلات مرفوضة (الهدف 0 اتصالات).
  - TLS: SNI بالاسم الأصلي، وشهادة لاسم آخر تفشل بلا طلب.
- **إخفاقات أثناء الكتابة** (محفوظة في `122`): 5 حالات في التشغيل الأول. أربع منها لم تُرسل أي طلب أصلاً، لأن مساعد الاختبار أعاد متغيرات التخزين مبكراً، فلم تكن تختبر شيئاً. أُصلحت دون skip أو xfail.
- **النطاق**: الخادم بلا تغيير (`guarded=False` افتراضياً)؛ نقطة التخزين وLLM فيه إعداد مشغّل لا مدخل مستأجر.

### G-161-5 — بوابة staging (مخففة، غير مغلقة)
- **التصميم**:
  - `gate` بلا بيئة ولا أسرار (`contents: read` + `actions: read`، ورمز الوظيفة المؤقت) تشغّل `scripts/r161_staging_gate.py`.
  - الشروط: main، و`vars.R161_STAGING_READY == owner-verified`، وGET يثبت وجود `r161-staging` بمراجعين وسياسة فرع `main` فقط.
  - `live` تحتاج `gate`، ومشروطة بمخرجها وبـmain، واسم بيئتها `${{ needs.gate.outputs.environment }}`.
- **الأدلة** (`123`):
  - المستودع الآن بلا بيئات وبلا متغيرات (GET).
  - البوابة على المستودع الحقيقي CLOSED (exit 1، بلا مخرج) في الحالات الثلاث: فرع غير main، وبلا تصريح، وبتصريح محاكى محلياً (البيئة 404).
  - بعد الـGET ما زالت البيئات 0.
  - 26/26 اختبار وحدة وبنية على ثلاثة إصدارات Python.
- **لماذا لا تُغلق**:
  - وثائق GitHub لا تذكر سلوك الوظيفة المتخطاة، ولم يُشغَّل الـworkflow (خارج التفويض، وقد يُنشئ مورداً).
  - من يملك صلاحية الكتابة يستطيع تشغيل نسخة معدلة من فرع آخر.

### المانع Q-025 والحالة
- **Q-025**: البيئة المحمية ومراجعوها، ومشروع اختبار معزول وبيانات مؤقتة بصلاحيات دنيا، ونطاق MCP. كان الرقم غير مستخدم (`123` §7).
- **فحص الحالة**: قبل تعديل الوثائق rc=1 ‏(CONTRADICTION: PR #175 مدموجة والحالة awaiting_merge)، وبعده rc=0. جدول M8 مطابق (34 بنداً).
- **العدّ**: 155 done + 1 blocked + 23 planned = 179. R-162 ‏planned.
- **مفتوحة بلا تغيير**:
  - G-161-1 (لا تشغيل حي)، وG-161-2 (لا عميل MCP).
  - G-161-4: الانتهاء المعلن إقرار، لا دليل على أن المزود يفرضه.
  - G-161-6: PostgREST لا Supabase المستضاف، وLLM والأداة mock فقط محلياً.
  - G-160-1..6، وQ-002، وG-157-3.

### الحزمة والبوابات
- `124`: الحزمة كاملة بالوضعين على 3.11 و3.12، وruff، وعدم الحذف، والذرية v1/v2، وأدلة الحالة، و`--check` العقود، والماسح، و`git diff --check`، وweb (test + build)، والبناء الجذري.
- **لم يُختبر**: أي تكامل حي، وتشغيل الـworkflow على GitHub، وSupabase المستضاف، وMCP. لا ادعاء جاهزية إنتاج.

## R-161c — أدلة التشغيل الحي 39082180770 وإصلاح تأكيد `update_task` (2026-10-18، دفعة m8/r161c-live-run-evidence-update-task، D-075)

### التشغيل (`125` السجل الأصلي، `126` التشخيص)
- **من وأين**: workflow_dispatch من المالك `reeveero-tech` على main عند `457a0c1`، المحاولة 1، 00:16:50Z.
- **البوابة**: OPEN. البيئة `r161-staging` أُنشئت 23:48:31Z قبل التشغيل، بمراجع إلزامي وسياسة `main` فقط.
- **وظيفة `live`**: بدأت 00:33:29Z بعد موافقة المراجع، ثم فشلت بخطوة «Fail unless every requested target passed» ‏(runner_rc=1).
- **السجل الأصلي** (`125`): نُزّل 00:52:58Z بـ`gh run view 39082180770 --log`، 545 سطراً، sha256 `68adcb50…7b1e`. محفوظ كما هو دون تعديل.

| الهدف | النتيجة | الفئة | رمز HTTP | السبب (قراءة فقط) |
|---|---|---|---|---|
| storage | FAIL | live_integration | 400 لكل عملية تذكر `tenant_id`، والتنظيف `DELETE events -> HTTP 400` | المشروع بلا عمود `tenant_id` (001 دون 002) |
| llm | FAIL | live_integration | 404 | `meta-llama/llama-3.3-70b-instruct:free` غير موجود في فهرس OpenRouter |
| external | FAIL | live_integration | 404 | المعرّف نفسه |
| mcp | NOT_RUN | — | — | غير مطلوب في التشغيل |

- **الملخص كما طُبع**: `live_integration: FAIL=3; unclassified: NOT_RUN=1`، و`exit_code=1`.
- **لا أثر على البيانات** (`126` §F، تجميع فقط بلا قراءة محتوى):
  - tasks 4، وevents 6، وagent_stats 2، وصفر صف أُنشئ أو عُدّل منذ 00:30Z، وآخر نشاط 2026-10-13.
  - المشروع هو الوحيد في الحساب، وفيه بيانات سابقة، فلا يصلح مشروع اختبار معزولاً.
- **الأسرار**: لا أسرار. GitHub قنّع 9 قيم، وفحص الأنماط أعطى 0، وخطوة الـworkflow طبعت «report checked: no secret value present».
- **ما تعذّر الحصول عليه**:
  - artifact التقرير 12119114952 (HTTP 401). لم يُكتب تقرير بديل.
  - أجسام أخطاء المزودين، وهي لا تُسجَّل أصلاً (R-149).
  - سجلات طلبات Supabase.
  - تنزيل ثانٍ للسجل (HTTP 403، حد الطلبات).

### العيب الذي كشفه التشغيل: `update_task` (`127`)
- **السلوك على main**: فحص «update_task accepted» ظهر `[ok]` بلا صف محدّث. PATCH بمعرّف عشوائي لم يطابق شيئاً، وPostgREST أجاب 204، و`update_task` كان يقبل (200, 204).
- **الإصلاح**: في `SupabaseStorage.update_task`، فيستفيد الخادم والمشغل معاً:
  - الطلب يحمل `Prefer: return=representation` و`select=task_id`.
  - النجاح يتطلب 200 وصف `task_id` المستهدف.
  - غير ذلك فشل بسبب ثابت: `HTTP <status>` أو `malformed response` أو `no row updated`.
- **الاختبارات** (`tests/test_r161c_update_task_confirmation.py`، 16 اختباراً):
  - قبل الإصلاح (كود main): 9 فشل و7 نجاح.
  - بعده: 16/16.
  - تشمل الحالات الثلاث المطلوبة (موجودة، وغير موجودة حتى مع 204، وفشل الخادم أو استجابة غير صالحة)، وإعادة إنتاج 39082180770 دون شبكة.
- **التوافق**:
  - 8 اختبارات قائمة فشلت أولاً، كلها بسبب fakes تجيب PATCH بـ204 مهما طُلب.
  - عُدّلت الـfakes لتطابق PostgREST، وصار توقع R-149 الصادق «no row updated» للتحديث بلا صف.
  - PostgREST الحقيقي يُختبر في job ‏`storage` في CI.
- **أثر جانبي (G-161-7، مفتوحة)**: في 39082180770 لم يجعل `connected=True` للتخزين إلا الفحص الكاذب. بعد الإصلاح كان سيظهر False مع أن الخادم أجاب 400. التعريف لم يُغيَّر.

### الحالة
- **R-161**: يبقى `blocked` (Q-025 محدث بشروط الإعادة).
- **G-161-5**: مخففة، غير مغلقة. التشغيل يثبت فتح البوابة وانتظار الموافقة حين توجد البيئة، لا سلوك الوظيفة المتخطاة حين تغيب.
- **G-161-1**: مفتوحة، فلا تشغيل حي ناجح بعد.
- **Q-026 جديدة** لانحرافات لم تُصلح في هذه الدفعة:
  - معرّف النموذج نفسه في `render.yaml`.
  - `PYTHON_VERSION` في OPERATIONS.
  - سكربت R-082 لم يعد يصل إلى التخزين.
- **لم يُنفذ**: لا إعادة تشغيل حي، ولا كتابة في مشروع Supabase، ولا تغيير في البيئة أو الأسرار أو حماية الفرع. R-162 لم يبدأ.

### الحزمة والبوابات (`127`)
- **الحزمة**: 1369 passed, 4 skipped, 4 deselected بالوضعين على 3.11.15 و3.12.13 (= 1353 + 16).
- **البوابات**: نجحت ruff، وعدم الحذف، والذرية v1/v2، وأدلة الحالة، و`--check` العقود، والماسح، والويب (اختبار وبناء)، والبناء الجذري.
- **`git diff --check`** أعطى rc=2 على سطر واحد (411) في `125`، وهو مسافة زائدة في مخرج GitHub نفسه.
  - لم يُعدَّل السطر حفاظاً على السجل الأصلي، وبصمته sha256 مطابقة للتنزيل.
  - باقي الملفات rc=0.
  - لا وظيفة CI تشغّل هذا الفحص.

## R-161d — تعريف `connected` لهدف التخزين (G-161-7، D-076، 2026-10-18)
الفرع `m8/r161d-storage-connected-definition`، الأساس main ‏`8f8488f`. فئة كل الأدلة هنا **mock** (خدمات وهمية على 127.0.0.1)؛ لا تشغيل حي ولا اتصال خارج loopback.

### خط الأساس قبل العمل
- main ‏`8f8488f` طابق PROJECT_STATE: pytest ‏1369 passed, 4 skipped, 4 deselected (Python 3.14.3)، وruff صفر، وبناء `web/` (اختبارات 39/39) والجذر ناجحان، وCI main ‏39091701511 success. لا فروقات.
- R-162..R-184 كلها تعتمد على R-161 المحجوب بـQ-025، فالعمل القابل للتنفيذ دون المالك هو الثغرة المسجلة G-161-7.

### الفجوة والإصلاح (`128`)
- **الفجوة**: `connected` للتخزين كان يُحسب من الفحوص الناجحة فقط؛ خادم يجيب HTTP 400/401/403 لكل طلب كان يُبلَّغ `connected=False`، بخلاف llm وexternal.
- **قبل** (مشغل main، اختبارات الفرع): 4 فشل و6 نجاح في `tests/test_r161d_storage_connected.py` — شكل 39082180770 (400)، ورفض 401، وإجابة التنظيف وحدها (403)، وإجابات الخطوات وحدها.
- **بعد**: 10/10، و121/121 مع كل ملفات اختبار R-161 (10 + 46 + 23 + 26 + 16).
- **طفرات**: 4/4 مقتولة — المراقب لا يسجل، وإجابة التنظيف مُتجاهلة، والمسجّل الأصلي لا يُعاد، والاستثناءات تُحتسب إجابة. عنصر التحكم 10/10، وبصمة الملف بعد الإعادة مطابقة.
- **لم يتغير**: PASS/FAIL، ورموز الخروج، وقاعدة «لا PASS بلا اتصال»، والخادم، و`supabase_store`. التقرير لا يحمل جسماً ولا مفتاحاً (اختبار مخصص بجسم 400 يعيد المفتاح).

### الحالة
- **G-161-7**: مغلقة.
- **R-161**: يبقى `blocked` بـQ-025؛ G-161-1/2/4/5/6 كما هي. لا إعادة تشغيل حي، ولا تغيير في البيئة أو الأسرار أو حماية الفرع.

### الحزمة والبوابات (`129`)
- **الحزمة**: 1379 passed, 4 skipped, 4 deselected بالوضعين (`real`، `specialists`) على 3.11.15 و3.12.13 من الأقفال (= 1369 + 10).
- **البوابات**: انظر `129` (ruff، عدم الحذف، الذرية v1/v2، أدلة الحالة، `--check` العقود، الماسح، الويب، البناء الجذري).

### ما تحقق بعد دمج R-161d (`132`، سُجّل في دفعة R-161e)
- PR #178 مدموجة بـ`--merge`: SHA ‏`2b036a2dc5c7eb0ab8846fea8749854e248ca430`، وأبوه الثاني الرأس المختبر `bc386c3001a58cec92c8f5415067a49a5452927c`.
- CI main ‏39097462754 على SHA الدمج: success في quality وtest وnetwork وbuild وaudit وstorage وdocker؛ publish متخطى (D-011).
- pytest ‏1379 passed, 4 skipped, 4 deselected في خطوات test الثلاث (3.11.17، 3.12.15، 3.12.15 specialists). job ‏storage: `storage PASS class=local_integration connected=True`.

## R-161e — مسبار R-082 وانحراف `PYTHON_VERSION` (Q-026، D-077، 2026-10-18)
الفرع `m8/r185-r082-probe-and-ops-drift`، الأساس main ‏`2b036a2`. فئة الأدلة **mock** (fake PostgREST على 127.0.0.1 داخل المسبار، والخادم الحقيقي عبر TestClient في عملية جديدة).

### الفجوة والإصلاح (`130`)
- **قبل** (نسخ main): المسبار بلا `AGOS_ENV` يرفض الإقلاع (`production start refused`)، ومع `AGOS_ENV=development` يُرفض كل طلب 401 قبل التخزين فتطبع المراحل الثلاث `storage=healthy` و`last_ok=None`. و`OPERATIONS.md` يذكر `PYTHON_VERSION=3.12.0` و`render.yaml` يضبط `3.12.15`. 5/5 اختبارات تفشل.
- **بعد**: POST ‏202 / GET ‏200 في كل مرحلة؛ طلبات تصل التخزين 0 ← 5 ← 4؛ والصحة: degraded ‏(ClientConnectorError) ← degraded ‏(HTTP 401) ← healthy. 5/5.
- **طفرات**: 4/4 مقتولة (عدّ الطلبات، `Prefer: return=representation`، الخروج 1، ضبط بيانات الاعتماد). بصمة الملف بعد الإعادة مطابقة.
- **الأمان**: المفتاح يُولَّد وقت التشغيل ولا يُطبع؛ الاختبار يتحقق من غياب `key_sha256` و`AGOS_TENANT_KEYS` و`sb_secret_` من المخرج.

### الحالة
- Q-026: الفرقان 2 و3 مُصلحان؛ معرّف النموذج في `render.yaml` مفتوح (قرار المالك، لا تغيير في إعداد النشر).
- R-161 يبقى `blocked` بـQ-025.

### الحزمة والبوابات (`131`)
- 1384 passed, 4 skipped, 4 deselected بالوضعين على 3.11.15 و3.12.13 (= 1379 + 5). البوابات في `131`.

## R-161f — التكامل الحي على مشروع الاختبار الجديد وميزانية مسبار llm (D-078، 2026-10-18)
الفرع `m8/r161f-live-run-llm-probe-budget`، والأساس main ‏`d8068b5`. فئات الأدلة: **live_integration** للتشغيلات (`134`، `135`)، و**mock** لاختبارات الإصلاح (`133`).

### الإعداد (`135`)
- المشروع `tecghrfscighyahufmga` ("geg"، ap-northeast-1، أُنشئ 2026-10-17T22:05:14Z) فارغ. طُبّق عليه `agos_001_init` ثم `agos_002_tenant_isolation`، وبصمتا الملفين في `135` §B. `tenant_id text` موجود في `tasks` و`events`، وRLS مفعّل بلا سياسات.
- متغيرات `r161-staging` قبل التغيير وبعده في `135` §E. الأسرار: أسماء وتواريخ فقط.
- فهرس OpenRouter وقت التشغيل: 471 نموذجاً، و17 معرّفاً مجانياً. `meta-llama/llama-3.3-70b-instruct:free` غائب.

### التشغيلات (`134`، كلها `simulated=False` على main ‏`d8068b5`)
| التشغيل | الأهداف | النتيجة |
|---|---|---|
| 39102327940 | storage,llm,external | storage ‏FAIL (401 لكل طلب، مفتاح صدر 2026-10-12 قبل وجود المشروع)؛ llm وexternal ‏FAIL ‏HTTP 429 |
| 39102435807 | llm,external (`openrouter/free`) | **external ‏PASS**؛ llm ‏FAIL (إكمال فارغ) |
| 39102564675 | llm (`liquid/lfm-2.5-2.6b:free`) | llm ‏FAIL (إكمال فارغ) |
| 39103597302 | storage (بعد تبديل المالك للسر) | **storage ‏PASS**: تسعة فحوص `[ok]`، و`cleanup=ok`، و11 طلباً 2xx في سجلات Supabase، والجداول 0 صفوف |

- في كل تشغيل: «report checked: no secret value present»، والأسرار مقنّعة `***`. لا تطابق لـ`sb_secret_` أو `sk-or-` أو `eyJhbGci` في السجلات الأربعة.
- MCP ‏NOT_RUN في كل تشغيل (غير مطلوب، D-078).

### الإصلاح (`133`)
- السبب: مسبار llm كان يرسل `max_tokens=16`، بينما مسبار external لا يرسل max_tokens، ونجح على المضيف والنموذج نفسيهما.
- الإصلاح: الميزانية الافتراضية لـ`LLMClient` (1024)، أي شكل استدعاء الإنتاج.
- النتائج: قبل الإصلاح 2/3 فشل، وبعده 3/3، والطفرات 3/3 مقتولة. والإكمال الفارغ بأي ميزانية ما زال FAIL.

### قبول R-161e (`136`)
- PR #179 مدموجة (SHA `d8068b5`، وأبوه الثاني `3876c07`). CI main ‏39099187783 ‏success، و1384/4/4 في خطوات test الثلاث.

### الحالة
- R-161 يبقى `blocked` بـQ-025، لأن llm لم يجتز حياً بعد.
- التالي: دمج هذه الدفعة، ثم تشغيل `storage,llm,external`، ثم دفعة قبول، ثم إبطال المفاتيح.

### الحزمة والبوابات (`137`)
- 1387 passed, 4 skipped, 4 deselected بالوضعين على 3.11.15 و3.12.13 (= 1384 + 3).

## R-161g — قبول R-161 (`done`، D-079، 2026-10-18)
الفرع `m8/r161g-acceptance`، والأساس main ‏`ecbbf45`. لا تغيير في الكود.
- **R-161f مدموجة**: PR #180، وSHA الدمج `ecbbf456a3b4f78606b385e73ebcb213789bac92`، والمختبر `2d9b0f3a61626910d22277ccd7759b0b574bcbc0`. CI الـPR ‏39104780707 وgovernance ‏39104780530 ناجحان. CI main ‏39105405500 ‏success بكل وظائفه (publish متخطى، D-011)، و1387 passed, 4 skipped, 4 deselected في خطوات test الثلاث (`138`).
- **التشغيل الحي للقبول** 39105410408 (`139`، سجل الوظيفتين حرفياً):
  - live_integration PASS=3:
    - storage: تسعة فحوص، و`cleanup=ok`.
    - llm: ‏`liquid/lfm-2.5-2.6b:free`.
    - external: ‏`openrouter/free`.
  - mcp ‏NOT_RUN، و`simulated=False`، و`exit_code=0`، و«no secret value present».
  - الموافقة مسجلة باسم reeveero-tech، بتعليق صريح.
- **Supabase بعد التشغيل**: الجداول 0/0/0. سجلات edge المستوعبة 8 من 11 طلباً. الغائبة PATCH، وGET events، وDELETE events، وسُجّلت قيداً كما هي.
- **الأسرار** (`140`): حذف ثلاثة أسرار (204 لكلٍّ منها)، والعدد الآن 0. المفاتيح ما زالت صالحة لدى المزودين بقرار المالك «لا تُبطل الآن» (Q-027).
- **الحالة**: R-161 ‏`done`. فحص الحالة والحوكمة في `141`.

## R-161h — قبول المالك الرسمي (`accepted`، D-080، 2026-10-18)
- **نص المالك**: حرفياً في D-080.
- **النطاق**: storage وllm وexternal ‏PASS ‏live_integration في 39105410408 (`139`). MCP ‏NOT_RUN مقبول، وليس نجاحاً لـMCP.
- **ما بعد دمج PR #181** (`142`):
  - SHA الدمج `c8d1805e9da6cdb454b8d0cda21b0bcdcbcaa6e9`، والمختبر `4610a479`.
  - CI main ‏39107652431 ‏success بكل وظائفه (publish متخطى، D-011)، و1387 passed, 4 skipped, 4 deselected في خطوات test الثلاث.
  - فحص الحالة نجح على `c8d1805`.
- **إقفال Q-027** (`144`):
  - تحقق مستقل: أسرار البيئة 0، ومشروع `tecghrfscighyahufmga` غائب من Supabase، ومضيفه لا يُحلّ.
  - تأكيد المالك فقط: مفتاح OpenRouter واحد مشترك وأُبطل.
- **سلسلة أدلة R-161 كاملة** (`133`–`144`). لم يُكرَّر أي تشغيل حي. الحوكمة والحزمة في `143`.

## R-162 — سجل الوكيل المعياري (`awaiting_merge`، D-081، 2026-10-18)
الفرع `m8/r162-agent-registry`، والأساس main ‏`e85e093`، والـPR ‏#183 (لا تُدمج إلا بقرار المالك).
- **ما بعد دمج PR #182** (`145`): SHA الدمج `e85e093477d8398c49f4614b71b34f50b88c0cca`، والمختبر `94c24c098fbc575c6dfb6f8faf6bfc1b987e7214`. فحوص الـPR كلها ناجحة قبل الدمج، وCI main على SHA الدمج مسجل في السجل. أدلة R-161 الحية تبقى 39105410408، وMCP ‏NOT_RUN (D-079/D-080).
- **قبل/بعد/طفرات** (`146`):
  - قبل (main): `tests/test_r162_agent_registry.py` خطأ جمع (`cannot import name 'agent_registry'`).
  - بعد: 26/26 على 3.11 و3.12.
  - الطفرات 15/15 مقتولة. منها ما يحرس عيوباً كشفتها الاختبارات في المسودة وأُصلحت: نتيجة متأخرة كانت تعيد اليتيم إلى `completed` (M7)، والعملية بعد إيقاف lifespan كانت ترفض كل مهمة بـ`worker not available` (M12، M13)، ومنفذ بلا اسم كان يكسر الإقلاع (M14).
- **القياس 10k** (`147`، `scripts/r162_registry_scale.py`): 10,000 هوية من الحضارة الحقيقية (20 × 500)، وإيجار لكل هوية على 10 عمال × 1000، والتشبع 1.0 ثم `Saturated`. بعد إيقاف/حذف العمال: 10,000 يتيماً فريداً = الإيجارات بالضبط، و0 إيجارات. خيوط النظام 1 ← 1، والعمليات الفرعية 0 ← 0. الأرقام كاملة في السجل.
- **الحزمة والبوابات**: `148`.
- **حدود (D-081)**: النبض داخل العملية ذاتي، و`reap()` غير مجدول، وعامل واحد لكل عملية في الذاكرة، وصف التخزين لليتيم لا يُعاد كتابته.

## R-162 — قبول الدمج (`done`، دفعة m8/r162-acceptance، 2026-10-18)

- **الدمج**: PR ‏#183 مدموج بحساب المالك (`mergedBy=reeveero-tech`) في 2026-10-18T09:26:35Z — SHA الدمج `5bfd46bb93b7f1d3d1a92ef965fd14f5b84cfd89`، والمختبر (رأس الـPR) `b489d9253d729ebb57b9f56f34b45d6a62636244` (الأب الثاني للدمج ✓).
- **CI main على SHA الدمج نفسه**: التشغيل 39115348507 نجاح بكل وظائفه — test ‏1413 passed, 4 skipped, 4 deselected في خطواته الثلاث (3.11 و3.12 و3.12-specialists)، وstorage ‏26/26 فحص تخزين + 4 passed (فئة local_integration)، وnetwork ‏4 passed، وquality وdocker وaudit وbuild نجاح، وpublish ‏skipped بحكم D-011. التفاصيل الخام في `logs/raw/149_r162_acceptance_main_ci.log`.
- **تحقق محلي مستقل على SHA الدمج** (Python 3.14.3): pytest الكاملة `1413 passed, 4 skipped, 4 deselected` (مطابق لـCI)، وruff F,E722 صفر، وسكربتات البوابة الثلاثة (status evidence / no-delete / atomic) كلها rc=0.
- **لا تغيير كود** في هذه الدفعة — قبول وتوثيق فقط. الحالة حُوّلت `awaiting_merge` ← `done` بحقول الدليل كاملة في ROADMAP وجدول بنود M8 في PROJECT_STATE.
- **الحدود المؤجلة (D-081)** كلها لـR-163 وما بعده ولم تبدأ.

## R-163 — طابور مهام دائم (`awaiting_merge`، دفعة m8/r163-durable-queue، D-082، 2026-10-18)

- **الفرع**: `m8/r163-durable-queue` — رأسه `048944c` فوق `bc04603` (main بعد قبول R-162). كود + وثائق + عقود + أدلة في دفعة ذرية واحدة (فحص الذرية v2 على الفرق الكامل: نجاح R0–R6).
- **التنفيذ (D-082)**: قاموس حالة عشر حالات في `agent_civilization/core/task_queue.py` بانتقالات مقيّدة وربط عام لا يغيّر قاموس الـAPI؛ ترحيل `docs/sql/003_task_queue.sql` (رمز سياج/عقد/محاولات/حمولة/مفتاح عنصر متكرر بفهرس فريد جزئي `(tenant_id, idempotency_key)` وفهرسي مسح)؛ عمليات تخزين مُسيّجة في `supabase_store.py` (مطالبة/بدء/تحرير/تجديد/إنهاء/انتقال مشروط بمرشح انتهاء الصلاحية)؛ في `server.py`: وضع متين اختياري `AGOS_TASK_QUEUE=1`، قبول بعد الحفظ (503 صريح عند فشل التخزين)، حلقة إرسال عادلة بين المستأجرين (الأقل جرياً ثم الأقدم)، محصّل استرداد (تجديد عقد الحيّ واسترداد المنتهي بمحاولات محدودة ثم `dead_letter`)، إلغاء `POST /api/tasks/{task_id}/cancel` بمساءلة مستأجر (404 عبر المستأجرين)، ومفتاح `idempotency_key` اختياري (≤128) في `POST /api/tasks` مع إعادة تشغيل متطابقة `duplicate: true`.
- **العقود**: `task_accepted` v2 (ملف v1 محفوظ) و`task_cancelled` v1 جديد — 27 ملف عقود؛ `tests/test_r160_contracts.py` حُدّث لسيناريو الإصدار الجديد (84 اختباراً في الملف).
- **الاختبارات المحلية (Python 3.14.3)**: الحزمة الكاملة `1449 passed, 6 skipped, 4 deselected` (السجل `150_r163_durable_queue_suite.log`) — +36 عن خط أساس R-162: 34 في `tests/test_r163_durable_queue.py` (آلة الحالة، سياج التخزين عبر PostgREST وهمي مخدوم بفلترة eq./in./lt. ورد 409، المسارات المتينة، **fail-closed لSEC-06** في المهمة المستردة، الإلغاء المتطاير أثناء الجري، النتيجة المتأخرة المرفوضة) و2 في `tests/test_r163_restart.py` (خادم حقيقي بعملية فرعية: استرداد `queued` بعد القتل، وقتل عامل بعقد منتهي = محاولة ثانية، وإعادة المفتاح المتكرر عبر إعادة التشغيل). لا فحص أُزيل أو خُفّف.
- **الاختبارات الحية**: `tests/test_r163_live_queue.py` خلف `R163_LIVE=1` تُشغَّل على المخزن المحلي ضمن `scripts/r042/run_local_stack.sh` (يطبّق 003) في CI job `storage` — جولات PostgREST حقيقية (سياج المطالبة، فهرس فريد 409، استرداد بمرشح lt.).
- **البوابات**: ruff F,E722 صفر؛ اختبارات الواجهة 39/39؛ بناء web وبناء الجذر ناجحان؛ no-delete rc=0 وatomic-batch v2 rc=0 (السجل `150_r163_gates.log`)؛ status-evidence يفشل وحده على `pr=#PR_NUMBER` — يُستبدل برقم الـPR الفعلي في التزام الدفع.
- **الحدود المعلنة (D-082، ليست ادعاء)**: تكرار أثر جانبي بعد موت عامل محتوم حتى `max_attempts`؛ موافقة SEC-06 fail-closed للمهمة المستردة (لا تُخزَّن ولا تنجو من إعادة الجدولة)؛ أحداث SSE للعملية الميتة تُفقد؛ الوضع المتين بعامل واحد في العملية (الاسترداد ليس موزعاً).

### R-163 — إصلاح فشل CI الأول (commit إضافي على الفرع نفسه، 2026-10-18)

- **الفشل**: خطوة CI ‏`Run tests on Python 3.12 with AGOS_AGENT_IMPL=specialists` فشلت في اختباري `test_r163_restart.py` («server did not become healthy in 60s») — وباقي خطوات test (3.11 و3.12) نجحت، مع نجاح quality وnetwork وgovernance.
- **السبب**: خادم الاختبار كان `stdout=PIPE` بلا قارئ؛ في وضع specialists يكتب الخادم ما يفوق سعة مخزن الأنبوب المؤقت فيُوَقَّف كتابةً ولا يصل `/health` — إعادة إنتاج محلية مؤكدة (اختبار واحد يفشل في 60 ث ثم ينجح بـ2.5 ث بعد الإصلاح).
- **الإصلاح**: مخرجات الخادم إلى ملف مؤقت، وذيله يُلحق برسالة المهلة/الموت عند الإقلاع (تشخيص أعمق دائم) — بلا تغيير في منطق الاختبار.
- **التحقق المحلي بعد الإصلاح**: `tests/test_r163_restart.py` في وضعين (افتراضي وspecialists) 2+2 نجاح، و`test_r163_restart.py + test_r163_durable_queue.py` معاً 36 نجاح.

### R-163 — إصلاح فشل CI الثاني (commit إضافي على الفرع نفسه، 2026-10-18)

- **الفشل**: خطوة CI ‏`storage` فشلت في اختباري `tests/test_r163_live_queue.py` («insert_task failed: RuntimeError») — أول تشغيل حقيقي لهذه الاختبارات ضد PostgREST حي.
- **السبب**: جلسات aiohttp مرتبطة بحلقة الأحداث التي أنشأتها؛ الاختبار كان يستدعي `asyncio.run` منفصلاً لكل عملية تخزين على المُفرد المشترك — الثانية تعيد استخدام جلسة حلقة ميتة فترفع RuntimeError.
- **الإصلاح**: كل اختبار يشغّل سيناريوهه كاملاً داخل `asyncio.run` واحدة ويغلق الجلسة داخل الحلقة نفسها قبل العودة (`await s.close()` في `finally`).
- **التحقق المحلي**: الاختباران الحيان نجحا ضد خادم PostgREST وهمي مخدوم في عملية منفصلة عبر مسار `R163_LIVE=1` نفسه (المسار الذي يفشل كان قد أعيد إنتاجه محلياً وأصبح ينجح بعد الإصلاح)؛ والحزمة الكاملة 1449+6+4 (السجل `150`).

### R-163 — إصلاح فشل CI الثالث (commit إضافي على الفرع نفسه، 2026-10-18)

- **الفشل**: خطوة CI ‏`storage` مجدداً: `queue_claim rejected: HTTP 400` ضد PostgREST ‏12.2.3 حي (لا خطأ قاعدة — الطلب رُفض قبلها).
- **السبب (PGRST109)**: تقييد UPDATE بـ`limit` في PostgREST يتطلب `order` صريحاً على عمود فريد — عمليات الطابور كانت ترسل `limit=1` بلا `order`.
- **الإصلاح**: حُذف `limit=1` من مرشحات كل عمليات الـPATCH الست (claim/start/release/renew/finish/transition) — مرشح `task_id=eq.X` وحيد يكفي، فالسلوك متطابق. والوهمي في وحدة `test_r163_durable_queue.py` صار يحاكي PGRST109 (يرد 400 على PATCH بـ`limit` بلا `order` فريد) كي يلتقط الوحدات هذا الصنف دون PostgREST حي.
- **التحقق المحلي**: 36 اختبار طابور + إعادة تشغيل نجاح؛ الاختباران الحيان عبر مسار `R163_LIVE=1` ضد خادم مخدوم نجاحاً؛ الحزمة الكاملة 1449+6+4 (السجل `150`).

### R-163 — إصلاح فشل CI الرابع (commit إضافي على الفرع نفسه، 2026-10-18)

- **الفشل**: خطوة CI ‏`storage` — الاختباران الحيان لـR-163 نجحا، لكن مدقق تكامل R-161 بعدها على المكدس نفسه فشل: «row matches task_row v1 (ValidationError)».
- **السبب**: عقد `task_row` v1 صارم (`extra="forbid"`) والصف الحقيقي بعد ترحيل 003 يحمل أعمدة الطابور الست — العقد لم يعد يطابق الواقع.
- **الإصلاح**: رفع العقد إلى v2 (`TaskRowV2` بأعمدة الطابور الست اختيارية — صفوف 001/002 والوضع المتطاير لا تضبطها أبداً؛ ملف v1 محفوظ) وتحديث نص المدقق؛ ملفات العقود 28؛ `task_list` v1 أعيد توليده لمرجعية v2.
- **التحقق المحلي**: عقود R-160/R-161 (130 اختباراً) وطابور R-163 (36) نجاح؛ الحزمة الكاملة 1449+6+4 (السجل `150`).

## R-163 — قبول الدمج (`done`، دفعة m8/r163-acceptance، 2026-10-18)

- **الدمج**: PR ‏#185 مدموج بحساب المالك (`mergedBy=reeveero-tech`) في 2026-10-18T12:57:27Z — SHA الدمج `ac9178308e5da2a7dc1566a7ea06dec346ebbf32`، والمختبر (رأس الـPR) `c580a52fef307873f6f3a7400496c47a8c496a98` (الأب الثاني للدمج ✓).
- **CI main على SHA الدمج نفسه**: التشغيل 39129458937 نجاح بكل وظائفه — test ‏1449 passed, 6 skipped, 4 deselected في خطواته الثلاث (3.11 و3.12 و3.12-specialists، مطابق للحزمة المحلية `150`)، وstorage ‏R-163 الحية 2 passed + فحوص R-042 + مدقق R-161 أخضر بـ`task_row` v2 على المكدس المحلي، وnetwork ‏4، وquality وdocker وaudit وbuild نجاح، وpublish ‏skipped بحكم D-011. التفاصيل الخام في `logs/raw/151_r163_acceptance_main_ci.log`.
- **لا تغيير كود** في هذه الدفعة — قبول وتوثيق فقط. الحالة حُوّلت `awaiting_merge` ← `done` بحقول الدليل كاملة في ROADMAP وجدول بنود M8 في PROJECT_STATE.
- **إصلاحات CI الأربعة على الفرع قبل الدمج** (PIPE بلا قارئ في specialists، حلقة aiohttp واحدة لكل اختبار، إسقاط `limit` من PATCH بحكم PGRST109، رفع `task_row` إلى v2) موثقة بأقسامها أعلاه.
- **الحدود المعلنة (D-082)** كلها لـR-164 وما بعده ولم تبدأ: تكرار أثر جانبي حتى `max_attempts`، وموافقة SEC-06 fail-closed للمسترد، وأحداث SSE للعملية الميتة، والاسترداد بعامل واحد في العملية.

## R-163 — دفعة تقوية لاحقة (m8/r163-hardening، 2026-10-18)

- **السياق**: مراجعة نهائية للدفعة كشفت ثلاثة عيوب سلوكية في الوضع المتين لم يغطها القبول الأول — أُصلحت في دفعة ذرّية واحدة (كود + اختبارات + وثائق + أدلة).
- **العيب 1 — النص الكامل فوق حد الـ500 (فقد بيانات صامت)**: `insert_task` يخزّن `prompt[:500]`، والصف مصدر التنفيذ المتين — فالمهمة ذات نص أطول كانت تُنفَّذ على نص مبتور، في المحاولة الأولى والمستردة سواء (الوضع المتطاير غير متأثر — ينفّذ من الذاكرة). **الإصلاح**: النص الكامل يسافر داخل الحمولة تحت المفتاح المحجوز `_agos_prompt` (`{"_agos_prompt": …, "data": …}`)، والمُرسِل يستخرجه (`_unwrap_durable_payload`)؛ الصفوف ذات الحمل الخام تسقط للعمود المقطوع. اختبار: `test_r163_full_prompt_survives_the_500_char_storage_bound` (نص 1207 حرفاً ينفَّذ كاملاً والعمود مقطوع فعلاً إلى 500).
- **العيب 2 — الإلغاء وقت انقطاع التخزين كان يرد 409 زوراً**: `transition_task` كانت تعيد bool — فشل الطلب نفسه (None من `_queue_patch`) لا يتميز عن «0 صفوف متطابقة»، فالإلغاء غير المؤكد كان يُعلَن «المهمة منتهية» (409) والصف قد يُعاد جدولته لاحقاً. **الإصلاح**: `transition_task` تعيد `Optional[bool]` (None = فشل الطلب؛ False = 0 صفوف) ونقطة الإلغاء ترد **503** «storage unavailable: cancel not confirmed — the task may still run» بلا أي أثر جانبي. اختبار: `test_r163_cancel_with_store_down_answers_503_not_409`.
- **العيب 3 — احتمال تعليق صف في `retrying`**: المحصّل كان ينفّذ انتقالين منفصلين (منتهي العقد→retrying ثم retrying→queued/dead_letter) — موت العملية بينهما يترك الصف معلقاً بلا مستردّ (لا الإرسال يلتقطه ولا المحصّل). **الإصلاح**: انتقال شرطي واحد لكل صف (claimed/running→queued أو →dead_letter مباشرة بمرشح انتهاء العقد)؛ وآلة الحالة تسمح بالمسارات المباشرة. اختبار: `test_r163_recovery_transitions_are_direct_no_retrying_strand` (مستوى الآلة + طرف إلى طرف).
- **مصنّف العقود (تحقق R-160)**: مقارنة `task_list.v1` قبل R-163 وبعده بالمصنّف نفسه: **جمعي خالص** (ست خصائص اختيارية على عناصر الصف، صفر كسر) — فإبقاء `task_list` على v1 سليم، لا يلزم رفع نسخة. وتصحيح عدّ: ملفات العقود **29** (لا 27/28).
- **مصالحة صياغة SEC-06**: الصياغة السابقة «المهمة المستردة تعمل fail-closed» كانت أضيق من الواقع — الموافقة لا تُنقل لسياق المُرسِل أصلاً، فكل تشغيل متين (الأول والمسترد) يعمل بلا موافقة. صُححت في D-082 وARCHITECTURE وDATA_MODEL.
- **ملاحظة أمانة أدلة**: سجل الحزمة `150` حُفظ مقتطعاً (سطر خلاصة واحد) وسجل البوابات فيه بقايا تشغيل وسيط — سجل `152` هو المرجع الكامل (حزمة كاملة + بوابات بنتائجها).
- **القياس المحلي (Python 3.14.3)**: الحزمة الكاملة `1452 passed, 6 skipped, 4 deselected` (`152`) — +3 عن القبول؛ ruff F,E722 صفر؛ بوابات الحوكمة الثلاث خضراء (السجل `152`).

### R-163 — سجل دمج دفعة التقوية (2026-10-18)

- PR ‏#187 مدموج (SHA الدمج `7665efbb49f3de94a3457ba61cb85ba8d5f29df9`) — CI main على SHA الدمج نفسه (تشغيل 39134898800) نجاح بكل وظائفه: test ‏1452 passed, 6 skipped, 4 deselected في خطواته الثلاث، وstorage وnetwork وquality وdocker وbuild وaudit نجاح، وpublish ‏skipped بحكم D-011. فحوص الـPR نفسه (تشغيل 39134142802) خضراء من أول تشغيل بلا إصلاحات.
