# بوابة الحوكمة (GOVERNANCE_GATE)

> **الحالة**: workflow منفّذ في `.github/workflows/governance-gate.yml` (دفعة M3/3، R-020). **مفروضة فعلياً منذ R-024 (M7/2، 2026-10-12)**: حماية الفرع `main` تفرض الفحوص الإلزامية (quality/test/build/docker/governance/audit؛ strict). اختُبر منطقه محلياً (حالتان إيجابيتان وأربع سلبيات) في `docs/audit/M3/logs/gate_test_harness.sh`. حد موثق: `enforce_admins=false` فحساب المالك يستطيع التجاوز (قرار المالك).

## القاعدة

أي دفعة (commit أو PR) تُغيّر أي شيء — كوداً أو إعدادات أو بيانات أو تحليلاً أو قراراً أو توثيقاً — يجب أن تتضمن في نفس الدفعة:

1. **التغيير الفعلي** (كود، إعدادات، بيانات، قرار، تحليل، إصلاح، توثيق).
2. **تحديث ROADMAP.md**: البند، حالته الجديدة، ما تم، ما تبقى.
3. **تحديث PROJECT_STATE.md**: الحالة، المخاطر، العوائق، القرارات.
4. **تحديث الوثائق المتأثرة**: معمارية، API، قاعدة بيانات، تشغيل، أمان، اختبارات.
5. **دليل التحقق**: اختبارات، سجلات، لقطات، نتائج build — في `docs/audit/`.
6. **تحديث DECISIONS.md** إذا اتُّخذ قرار مهم.
7. **تحديث CHANGELOG.md**.

**لا استثناءات**: تغييرات التوثيق الخالصة تخضع للقاعدة نفسها (تُحدَّث الخارطة/الحالة/السجل ضمنياً بوصف البند).

**ممنوع**: فصل التوثيق عن العمل بأي شكل (PR منفصل، commit متأخر، بند خارج الخارطة دون إضافته في نفس الدفعة).

## بوابة عدم الحذف (R-152، D-063/D-064)

- خطوة **No-delete check (R-152)** في job ‏`governance`: `python3 scripts/check_no_delete.py origin/main HEAD` — تفشل إن حُذف أو نُقل أو أُعيدت تسمية **أي** ملف متعقب في الـdiff الصافي للـPR، وتذكر كل مسار (وإلى أين نُقل إن كان نقلاً). مرجع git غير صالح = خطأ (rc=2) لا نجاح. خطوة «Atomic batch check» تعمل بعدها حتى لو فشلت (`if: !cancelled()`) فيظهر الحكمان معاً.
- **لا استثناء داخل المستودع** (D-064): لا ملف allowlist ولا label ولا نص PR. الحذف المصرّح يحتاج قرار مالك منفصلاً يُدخل آلية مراجَعة في دفعته الخاصة.
- الاختبارات: `tests/test_r152_no_delete_gate.py` (13، مستودعات git حقيقية) + PR تجريبية حية سالبة (`docs/audit/M8/logs/raw/016`).

## فحص علامات التعارض (السجل 295)

- خطوة **Conflict-marker check** في job ‏`governance`: `python3 scripts/check_conflict_markers.py origin/main HEAD` — تفشل إن أضافت الـPR سطرًا يبدأ بـ`<<<<<<<` أو `>>>>>>>` أو `|||||||` (يليه مسافة أو نهاية السطر). `=======` وحده لا يُفشل (شائع في مخرجات pytest المسجلة) ويُذكر فقط مع علامة أخرى في الملف نفسه. الأسطر المضافة فقط — الملفات التاريخية لا يعاد الحكم عليها.
- السبب: حادثة 2026-10-25 — إعادة أساس دفعت علامات تعارض إلى ROADMAP/PROJECT_STATE/CHANGELOG ومرّت كل البوابات الأخرى.
- الاختبارات: `tests/test_conflict_marker_gate.py` (20، مستودعات git حقيقية، مسارات عربية ومقتبسة وبمسافات).

## فحص الذرية v2 (R-153، D-065)

- خطوة **Atomic batch check v2 (R-153)** بعد الفحص المضمن v1 (الباقي بلا تغيير): `python3 scripts/check_atomic_batch.py origin/main HEAD`. القواعد: **R0** ROADMAP/PROJECT_STATE/CHANGELOG دائماً؛ **R1** أي تغيير تنفيذي (مسارات v1 + `scripts/`، `.github/`، Docker/Render، `requirements*`، `package(-lock).json`، إعدادات البناء والاختبار، `public/`، `.env.example`، `docs/sql/`) يتطلب دليلاً في `docs/audit/`؛ **R2** API.md؛ **R3** DATA_MODEL.md؛ **R4** QUALITY.md؛ **R5** Python تشغيلي ⇒ تغيير في `tests/`؛ **R6** مصدر `web/` ⇒ تغيير في `web/tests/` أو `tests/`. تُذكر كل المخالفات دفعةً واحدة؛ خطأ git ⇒ rc=2.
- **حد صادق**: يثبت وجود الاختبارات/الأدلة في الدفعة لا كفايتها؛ `src/` بلا إطار اختبار بعد (R-158).
- الاختبارات: `tests/test_r153_atomic_batch_v2.py` (54) + إعادة تشغيل تاريخية (`docs/audit/M8/logs/raw/025`) + PR تجريبية حية (`035`).

## فحص الحالات والأدلة (R-154، D-066)

- خطوة **Status evidence check (R-154)**: `python3 scripts/check_status_evidence.py .` — تطبّق القواعد العشر في [`STATUS_DEFINITIONS.md`](STATUS_DEFINITIONS.md) §3 على ROADMAP وجدول «حالة بنود M8» في PROJECT_STATE: المفردات الست، حقول الدليل لكل حالة، ربط git لـ`done`، احتواء سجل القبول على `merge_sha`/`tested_sha`/`ci_run`/`success`، تناقض «مدموج ولا يزال awaiting_merge»، التزامن، والترتيب. خطأ git/ملفات ⇒ rc=2.
- الاختبارات: `tests/test_r154_status_evidence.py` + PR تجريبية حية (`docs/audit/M8/logs/raw/048`).

## حدود التنفيذ الآلي (للأمانة)

- فحص diff في workflow يتحقق من **الدفعة المجمعة للـ PR**؛ ذرّية كل commit على حدة تتطلب سياسة فريق أو فحص push events — يوثق كحد معروف.
- workflow وحده لا يفرض شيئاً دون **حماية فروع** (require status checks + منع الدفع المباشر لـ main).
- لذا لا تُوصف هذه البوابة بأنها "مفعّلة" قبل تنفيذها واختبارها بـ PR إيجابي وآخر سلبي.

## مسودة workflow (تُختبر عند التنفيذ)

```yaml
name: Governance Gate
on:
  pull_request:
    branches: [main]
jobs:
  governance:
    runs-on: ubuntu-26.04   # D-041 (R-088)
    steps:
      - uses: actions/checkout@v7
        with: { fetch-depth: 0 }
      - name: Atomic batch check
        run: |
          set -euo pipefail
          CHANGED=$(git diff --name-only origin/main...HEAD || true)
          # (منطق الفحص: تغيير أي مسار يتطلب وجود تحديثات الوثائق المقابلة في نفس الـ diff)
```

> ملاحظة تقنية: `grep -c` يعيد exit code 1 عند غياب التطابق؛ يجب استخدام `|| true` أو `set +e` بحذر حتى لا تفشل الخطوة مبكراً لأسباب خاطئة — تُختبر قبل الاعتماد.
