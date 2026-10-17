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
