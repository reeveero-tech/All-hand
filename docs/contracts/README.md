# عقود الخدمات القابلة للتحقق (R-160، D-072)

> المصدر الوحيد: النماذج في [`agent_civilization/core/contracts.py`](../../agent_civilization/core/contracts.py). الملفات هنا **مولّدة** — لا تُعدَّل يدوياً.
> التوليد: `python3 scripts/export_contract_schemas.py` — الفحص: `python3 scripts/export_contract_schemas.py --check` (rc=1 عند أي انحراف). يشغّل الفحصَ `tests/test_r160_contracts.py::test_committed_schemas_match_the_code` في كل CI.

## العقود (v1)

| المجال | العقد | ما يصفه |
|---|---|---|
| API | `task_accepted` | جسم 202 من `POST /api/tasks` |
| API | `task_record` | سجل مهمة `POST /api/tasks` أو `/api/chat` كما يعيده `GET /api/tasks/{id}` من الذاكرة |
| API | `execute_record` | سجل `/civilization/execute` في الذاكرة |
| API | `task_row` | صف المهمة من التخزين (`source: database`) |
| API | `task_list` | `GET /api/tasks` (كل عنصر أحد المتغيرات الثلاثة) |
| API | `chat_response`، `execute_response` | استجابتا `/api/chat` و`/civilization/execute` |
| API | `system_status`، `ready`، `not_ready`، `memory_list` | `/api/system/status`، `/health/ready` (200/503)، `/api/memory` |
| API | `error_detail`، `validation_error` | أجسام الأخطاء 4xx/5xx و422 |
| API (طلب) | `task_request`، `chat_request`، `execute_request`، `external_agent_request` | النماذج القائمة في الخادم، مُصدَّرة كما هي |
| SSE | `sse_frame` | `task_created`، `task_started`، `task_completed`، `heartbeat`، `stream_gap` |
| وكيل/أداة | `agent_outcome`، `handler_result` | ناتج `execute()` في الوضعين `real` و`specialists` وللوكيل الخارجي، وناتج المعالج |
| LLM | `llm_result` | ناتج `LLMClient.complete` (ok وcontent وerror ثابت) |
| الطابور | `queue_task` | `Task.to_dict()` في `redis_queue.py` |
| التخزين | `storage_task_insert`، `storage_task_update`، `storage_event_insert`، `storage_agent_stats` | الأجسام المكتوبة إلى PostgREST؛ مفاتيحها ⊆ أعمدة `docs/sql/001+002` |

## الإصدارات والتوافق
- كل مخطط يحمل `x-contract-version` واسم ملفه `<name>.v<N>.schema.json`.
- `--check` يصنّف الفرق: **BREAKING** (حذف حقل، تغيير نوع أو قيم enum/const، صار إلزامياً أو لم يعد، تغيير سياسة الحقول غير المعروفة) أو **ADDITIVE** (حقل اختياري جديد فقط). التصنيف محافظ: ما لا يُثبت توافقه = BREAKING.
- أي فرق يفشل الفحص حتى يُعاد التوليد ويُراجع. الفرق BREAKING يتطلب رفع الإصدار، و**ملف الإصدار السابق يبقى** (D-064) — الفحص يرفض غيابه.

## سياسة الحقول غير المعروفة
- **الطلبات**: تُتجاهل ولا تُنفَّذ (سلوك FastAPI القائم). `tenant_id` في الجسم لا يغيّر المالك.
- **الاستجابات**: النماذج صارمة (`extra=forbid`) في الاختبارات، فيظهر أي حقل يضيفه الخادم أو يحذفه.
- **نتيجة المعالج** (`handler_result`): مفتوحة، والمفاتيح الأساسية (`ok`، `answer`، `error`) منمّطة.
- **القراء**: قارئ الطابور (`Task.from_dict`) والواجهة (`web/src/taskView.js`) يتجاهلون الحقول الأحدث.

## الأمثلة المشتركة (`examples/`)
`task_list.memory.json`، `task_list.database.json`، `chat_response.json`، `sse_frames.json`. تتحقق منها `tests/test_r160_contracts.py` مقابل المخططات **ومقابل مفاتيح الاستجابات الحية** للتطبيق داخل العملية، وتقرؤها `web/tests/task_view.test.mjs`. لذلك لا ينحرف الخادم أو الواجهة دون فشل اختبار.

## سلوك مثبت كعقد v1 (مرصود، لم يتغير)
- الطلب المكرر = مهمتان (لا idempotency key — G-160-4).
- قطع العميل لا يلغي العمل: السجل يكتمل ويُسترجع (uvicorn حقيقي، `docs/audit/M8/logs/raw/112`).
- SSE بلا `id:` ولا إعادة تشغيل بعد إعادة الاتصال (G-160-5).

## أنواع الأدلة
| النوع | ماذا يعني هنا |
|---|---|
| وحدات / mock | النماذج، مدقق التوافق، Redis وهمي في الذاكرة، نقل LLM مُرقَّع، التطبيق داخل العملية (TestClient/ASGI) |
| تكامل محلي | خوادم aiohttp على 127.0.0.1 بدل PostgREST ونقطة OpenAI-compatible، عبر العملاء الحقيقيين؛ والخادم الحقيقي تحت uvicorn عبر TCP |
| حي | **لا شيء** في R-160 (لا LLM مدفوع، ولا Supabase سحابي، ولا Render) — R-161 |

## خارج النطاق
`src/` (Q-002)، وعقود MCP، وما لا يصل إليه الخادم الحالي.
