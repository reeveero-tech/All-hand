# R-150 (Branch protection): تدقيق حماية `main` الفعلية مقابل الحوكمة

> مهمة تدقيق **للقراءة فقط** — لم تُغيَّر أي إعدادات GitHub حية ضمن هذا العمل. خط الأساس: main عند `960c5e0520f931ec46baa6c8b21c73b57df27ac0` (رأس main وقت التدقيق — مُتحقق منه في القراءة الحية نفسها). الفرع `m7/r150-branch-protection-audit`. الأدلة الخام: `docs/audit/M7/logs/raw/291..294`.

## مصدر البيانات وطريقة القراءة

- **الوقت (UTC)**: 2026-10-16T22:14Z (قراءة `branches/main`) وما تلاها مباشرة في الجلسة نفسها.
- **الأداة**: `gh api` (CLI موثّق مسبقًا، صلاحية قراءة فقط — لم تُطلب أي صلاحية جديدة ولم يُعرض أي رمز اعتماد).
- **الأوامر** (كلها GET، ورمز الخروج الحقيقي لكل أمر مسجل أدناه):
  - `GET /repos/reeveero-tech/All-hand/branches/main` — RC=0 (السجل `291`).
  - `GET /repos/reeveero-tech/All-hand/branches/main/protection` — RC=0 (السجل `292`).
  - `GET /repos/reeveero-tech/All-hand/rulesets?includes_parents=true` — RC=0 (السجل `293`).
  - `GET /repos/reeveero-tech/All-hand/rules/branches/main` — RC=0 (السجل `294`).
- **حد الوصول السابق**: قراءة سابقة لنقطة النهاية التفصيلية عبر اعتماد integration أعادت `403 Resource not accessible by integration` — عولج هنا بقراءة باعتماد CLI مخوّل للقراءة أعادت 200. الملخص الأولي (protected: true، الفحوص، enforcement_level: non_admins، rulesets: []) تبيّن صحيحًا وأكمله التدقيق.

## الحماية المطبَّقة: تقليدية لا Rulesets

- `rulesets` (مع `includes_parents=true`): `[]` — لا قواعد Rulesets على المستودع (السجل `293`).
- `rules/branches/main`: `[]` — لا قواعد Rulesets مطبَّقة على الفرع (السجل `294`).
- الحماية إذن هي **قاعدة حماية فرع تقليدية** (Branch protection rule) — `branches/main` يعيد `protected: true` و`protection_url` (السجل `291`).

## المقارنة: الواقع مقابل الوثائق

| البند | ادعاء الوثيقة | القيمة الحية | الدليل | الفجوة |
|---|---|---|---|---|
| الحماية مفعلة | مفروضة فعليًا منذ R-024 (GOVERNANCE_GATE.md) | `protected: true` | السجلان `291`/`292` | لا فجوة |
| الفحوص الإلزامية | quality, test, build, docker, governance, audit (GOVERNANCE_GATE.md + R-024) | `contexts: [quality, test, build, docker, governance, audit]` — كلها `app_id: 15368` (GitHub Actions) | السجل `292` | لا فجوة |
| strict | strict=true (R-024) | `strict: true` | السجل `292` | لا فجوة |
| تطبيق الفحوص على المسؤولين | `enforce_admins=false` موثق كحد (قرار المالك — GOVERNANCE_GATE.md) | `enforce_admins.enabled: false` | السجل `292` | لا فجوة (حد موثق لا اختلاف) |
| force-push / الحذف | allow_force_pushes=false, allow_deletions=false (R-024) | كلاهما `enabled: false` | السجل `292` | لا فجوة |
| المراجعات | required_approving_review_count=0 (R-024 — المالك هو المراجع) | `required_approving_review_count: 0`، dismiss_stale/require_code_owner/require_last_push كلها false | السجل `292` | لا فجوة |
| قيود الدفع | restrictions: null (R-024) | `restrictions: null` | السجل `292` | لا فجوة |
| الفحوص الإلزامية مقابل فحوص CI | — | فحوص CI تشمل أيضًا `network` و`storage` و`publish` — **ليست** ضمن الفحوص الإلزامية للدمج | مقارنة `292` بـ`.github/workflows/ci.yml` | لا فجوة (متسق مع الحوكمة) |
| إعدادات أخرى ذات صلة | غير موثقة | required_signatures=false، block_creations=false، lock_branch=false، allow_fork_syncing=false، required_conversation_resolution=false، required_linear_history=false | السجل `292` | لا إعداد يمنع تطبيق سياسة الحوكمة |

## النتيجة

**تطابق تام** بين الحماية الفعلية والحوكمة الموثقة (GOVERNANCE_GATE.md وR-024): الفحوص الستة الإلزامية بأسمائها الدقيقة، strict=true، enforce_admins=false (حد موثق بقرار المالك — يستطيع المالك تجاوز الفحوص)، لا force-push ولا حذف، لا قيود دفع، ولا Rulesets موازية. الحماية قاعدة حماية فرع فعلية يفرضها GitHub على الدمج في `main` — لا مجرد وصف.

**الحدود الموثقة (لا تعني اختلافًا عن الحوكمة)**:
- `enforce_admins=false`: حساب المالك يستطيع تجاوز الفحوص — قرار مالك موثق منذ R-024، ويظل حدًا معروفًا.
- `required_approving_review_count=0`: لا موافقات إلزامية — المالك هو المراجع الوحيد.
- ذرّية الدفعة لكل commit على حدة تعتمد على فحص الـPR المجمّع (حد موثق في GOVERNANCE_GATE.md).

## حدود إثبات هذا التدقيق

- نجاح اختبارات سابق لا يُستخدم دليلًا للحماية؛ الدليل هنا هو قراءة إعدادات الحماية نفسها (السجلات `291`–`294`).
- لم يُختبر التجاوز بمحاولة دفع أو دمج مخالف — مهمة قراءة فقط.
- لم تُغيَّر أي إعدادات؛ أي توصية تغيير مستقبلية تكون مهمة منفصلة (لا توصية مطلوبة هنا — لا فجوة).
