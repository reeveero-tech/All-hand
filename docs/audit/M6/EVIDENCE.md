# أدلة التحقق — المرحلة M6 (بنية بديلة وقابلية تشغيل خارجية)

> كل فحص مُوثق في logs/ باسم البند المطلوب. أكواد الخروج مأخوذة مباشرة من
> تشغيل الفحص لا من نهاية السجل — نتيجة `0` تعني نجاحاً فعلياً.

## R-043 (M6/1) — اختبار تكامل web/ مع API

الهدف: عقد API الذي تستهلكه واجهة web/ فعلياً (web/src/api.js) — 10 اختبارات
جديدة في `tests/test_web_api_contract.py` تتحقق من كل نداء تُصدره الواجهة
ضد الخادم الحقيقي عبر TestClient، مع شكل الاستجابة بما يطابق ما تقرأه
المكونات (Dashboard.jsx، Specializations.jsx، Agents.jsx، Tasks.jsx، Chat.jsx).

| نداء الواجهة | ما يقرؤه المكون | الاختبار | النتيجة |
|---|---|---|---|
| GET /api/system/status | Dashboard.jsx (status, total_agents, tasks_*) | `test_system_status_fields_dashboard_reads` | ✓ |
| GET /api/specializations | Specializations.jsx (name, description, icon, agents) | `test_specializations_shape_specializations_component` | ✓ |
| GET /api/agents (+?specialization=) | Agents.jsx (name, specialization) | `test_agents_list_and_filter` | ✓ |
| POST /api/tasks {specialization,prompt,data} | Specializations.jsx (task_id, status) | `test_e2e_task_from_ui_shape_and_poll` | ✓ |
| GET /api/tasks?limit= | Tasks.jsx (id, prompt, status) | `test_list_tasks_frontend_shape` | ✓ |
| GET /api/events?limit= | Dashboard.jsx (events) | `test_events_list_dashboard` | ✓ |
| POST /api/chat {message} | Chat.jsx (task_id, routed_to, outcome) | `test_chat_frontend_contract` | ✓ |
| POST /api/agents/external + DELETE | Agents.jsx | `test_external_agent_register_and_remove` | ✓ |
| SSE /api/events/stream | App.jsx (createEventSource) | `test_sse_stream_yields_event` | ✓ |

سيناريو كامل (نفس payload الواجهة): إنشاء مهمة analyst → poll → النتيجة
مكتملة مع result.ok. علامة: تأكيد أن التخصص غير المعروف يُرفض بـ400.

### قيود موثقة (صدقاً)
- SSE اختُبر باستدعاء endpoint مباشرة مع حدث مُعد مسبقاً — TestClient يعلق
  على stream لانهائي. هذا يثبت أن المولد يصدر حدثاً بالشكل الصحيح؛ لا يثبت
  التكامل الكامل لEventSource عبر HTTP حقيقي (نطاق docker job يغطي بناء
  الصورة وتشغيلها و/health — لكن SSE نفسه ليس مغطى).
- auth في CI/الاختبارات معطّل (ENABLE_AUTH=False بلا ADMIN_API_KEY) —
  عقد POST /api/tasks و/api/chat و/api/agents/external مع auth مفعل
  مغطى بM4 (R-041 tests) وليس هنا.

| الفحص | النتيجة | الدليل |
|---|---|---|
| pytest (143 + 10 جديدة) | 153/153، exit 0 | `logs/01_r043_pytest.log` |
| ruff (كل المسارات) | صفر أخطاء، exit 0 | `logs/02_r043_ruff.log` |
