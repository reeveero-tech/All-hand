# تدقيق تحضيري — جرد الأدوات الحالية مقابل عقد سجل R-171

> **2026-10-24 — قراءة فقط، لا تغيير شيفرة ولا حالة.** R-171 يبقى `planned`: اعتماديته «كل P4» غير مستوفاة
> (R-170 `in_progress`). هذا الجرد مدخل لـR-171 لا بدء له. العقد المرجعي:
> `docs/governance/roadmaps/revival-scale/specs/tool_registry_contract.schema.json` (مسودة؛ 18 حقلًا مطلوبًا).

## الطريقة
تحليل ساكن لـ`agent_civilization/core/agents/real_dispatcher.py` (`_HANDLERS`): جسم كل معالج مع الدوال المساعدة
الخاصة التي يستدعيها، وبحث عن مؤشرات (`safe_urlopen`/`aiohttp` = شبكة؛ `sandbox`/`subprocess` = تنفيذ شيفرة؛
`get_llm` = نموذج؛ `sqlite`/`cursor` = قاعدة بيانات؛ `timeout=` = مهل حرفية). **هذه مؤشرات ساكنة لا تحقق تشغيلي**؛
قد تفوت مسارًا غير مباشرًا أو تلتقط نصًا غير منفذ. الملف الآلي: `PREP_AUDIT_R171_TOOL_INVENTORY.json`.

## الجرد (20 معالجًا = 20 تخصصًا)
| التخصص | المعالج | شبكة | تنفيذ شيفرة | نموذج | قاعدة بيانات | مهل حرفية (ث) | يعيد tool_output | متطلب البوابة الافتراضي | فئة خطر |
|---|---|---|---|---|---|---|---|---|---|
| `code_generator` | `_code_generator` (64 سطر) | — | — | نعم | — | — | نعم | {"code": true} | low (مقترح) |
| `surgeon` | `_surgeon` (12 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `test_runner` | `_test_runner` (58 سطر) | — | نعم | — | — | — | نعم | {"tool_output": true} | high (مقترح) |
| `validator` | `_validator` (65 سطر) | — | — | — | — | — | نعم | {"tool_output": true} | low (مقترح) |
| `analyst` | `_analyst` (49 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `data_processor` | `_data_processor` (31 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `db_manager` | `_db_manager` (21 سطر) | — | — | نعم | نعم | — | نعم | — | medium (مقترح) |
| `network_client` | `_network_client` (77 سطر) | نعم | — | — | — | 6 | نعم | {"tool_output": true} | medium (مقترح) |
| `api_integrator` | `_api_integrator` (32 سطر) | نعم | — | — | — | 8 | نعم | {"tool_output": true} | medium (مقترح) |
| `researcher` | `_researcher` (71 سطر) | نعم | — | — | — | — | نعم | {"tool_output": true, "citations": true} | medium (مقترح) |
| `architect` | `_architect` (7 سطر) | — | — | — | — | — | لا | — | low (مقترح) |
| `strategist` | `_strategist` (23 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `reviewer` | `_reviewer` (26 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `monitor` | `_monitor` (53 سطر) | — | — | — | — | — | نعم | — | low (مقترح) |
| `educator` | `_educator` (45 سطر) | — | — | نعم | — | — | نعم | — | low (مقترح) |
| `communicator` | `_communicator` (46 سطر) | — | — | نعم | — | — | نعم | — | low (مقترح) |
| `designer` | `_designer` (4 سطر) | — | — | — | — | — | لا | — | low (مقترح) |
| `builder` | `_builder` (4 سطر) | — | — | — | — | — | لا | — | low (مقترح) |
| `modifier` | `_modifier` (25 سطر) | — | نعم | — | — | — | نعم | — | high (مقترح) |
| `self_developer` | `_self_developer` (103 سطر) | — | — | — | — | 120 | نعم | — | low (مقترح) |

## الفجوة مقابل العقد (ما لا يوجد اليوم)
- لا سجل canonical: «الأداة» اليوم = مفتاح في `_HANDLERS` بلا `tool_id`/`version`/`source`/`owner`/`license`.
- لا `input_schema`/`output_schema` معلنة؛ الأشكال ضمنية في الشيفرة (المدخل `work`: `prompt`/`data`؛ المخرج
  `ok`/`answer`/`tool_output`/`error`). بوابة R-170 تتحقق من بعض المخرجات لا من مخطط.
- `risk_class`/`read_only`/`idempotent`/`requires_approval`/`tenant_scope`/`auth_scope` غير معلنة. الفئة أعلاه
  **مقترحة** من المؤشرات (تنفيذ شيفرة = high؛ شبكة/قاعدة = medium؛ غير ذلك = low) وتحتاج مراجعة في R-171/R-172.
- `timeout_ms`: مهل حرفية في 4 معالجات فقط؛ البقية تعتمد مهل المجمعات/المهمة (R-164).
- `health_status`/`test_status`: لا مصدر حي؛ الاختبارات موجودة لكن غير مربوطة بسجل.
- العدد الحالي 20 أداة داخلية؛ هدف 10,000 أداة **غير مثبت** ويتطلب R-171..R-175 واختبارات حجم (انظر خطة الضغط).

## مقترح لـR-171 (لا يُنفذ قبل استيفاء الاعتمادية)
1. سجل في الذاكرة + تخزين دائم بمخطط مُراجع (تثبيت `$id`، مراجعة الحقول) مع CRUD/lookup/search وuniqueness
   وversioning وسجل تدقيق — حسب معايير القبول.
2. تسجيل الـ20 الحالية آليًا من `_HANDLERS` مع metadata يدوية مراجعة؛ اختبار أن كل مفتاح في `_HANDLERS` مسجل
   والعكس (لا أداة شبحية).
3. اختبارات عقد سالبة: معرف مكرر، مخطط غير صالح، إصدار متراجع، حقل مفقود.
