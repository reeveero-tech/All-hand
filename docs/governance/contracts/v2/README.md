# عقود AGOS v2 (R-202، D-151)

> المصدر الوحيد: `agos_v2/contracts.py`. ملفات `*.schema.json` هنا **مولّدة** — لا تُعدَّل يدويًا.
> التوليد: `python3 scripts/export_v2_contract_schemas.py` — الفحص: `--check` (rc=1 عند أي انحراف). يشغّله `tests/test_r202_v2_schema.py::test_committed_v2_schemas_match_the_code` في كل CI.

| العقد | الملف | ما يصفه | الجدول |
|---|---|---|---|
| `task` | `task.v1.schema.json` | هدف المستخدم بمعايير قبول وميزانية وحالة | `agos.tasks` (حقل = عمود) |
| `step` | `step.v1.schema.json` | خطوة في خطة المهمة مع التحقق والمحاولات والتكلفة | `agos.steps` |
| `agent_spec` | `agent_spec.v1.schema.json` | تعريف الوكيل: الدور، التعليمات، سياسة النموذج، الأدوات، الذاكرة، حلقة العمل، الصلاحيات، الحدود، مجموعة التقييم | جسم `agos.agent_specs.spec` |
| `artifact` | `artifact.v1.schema.json` | ناتج مهمة (مضمّن أو ملف في التخزين) مع الحجم وsha256 | `agos.artifacts` |
| `message` | `message.v1.schema.json` | رسالة محادثة | `agos.messages` |

- كل مخطط JSON Schema draft 2020-12، يحمل `$id` = `agos://contracts/v2/<name>/v1` و`x-contract-version` و`x-rules` (القواعد بين الحقول التي يفرضها النموذج والقاعدة معًا).
- كل العقود صارمة: `additionalProperties: false`.
- الأمثلة في `examples/` سيناريو واحد مترابط (سؤال بحثي → 3 خطوات متحقق منها → ناتج → رسالتان). يُدرجها الاختبار الحي في القاعدة كما هي.
- تغيير عقد كاسر = إصدار جديد `v2` بملف جديد، ويبقى ملف `v1` (D-064).
