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
