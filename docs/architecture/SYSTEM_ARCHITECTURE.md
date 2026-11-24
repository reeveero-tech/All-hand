# معمارية النظام

- **الحالة:**
  - القسم 1 (الحالي): `implemented`.
  - الأقسام 2–4: `proposed`.
- **التاريخ:** 2026-11-25.

## 1. المعمارية الحالية

```
المتصفح (web/*.html,js,css)
   │ HTTP + SSE
   ▼
FastAPI (app/main.py) ── خدمة واحدة على Render، ونسخة واحدة
   │
   ├─ Orchestrator (في الذاكرة: _runs dict, asyncio tasks)
   │     ├─ plan → DAG → agents (agents.py) → verify ×3 → synthesize → final check
   │     └─ tools.py: web_search / fetch_url / run_python (عملية فرعية بحدود rlimit)
   ├─ llm.py → Pollinations (مجاني) | BYOK | المنصة
   ├─ store.py → Supabase PostgREST: جدول runs (يُحفظ عند الانتهاء)
   └─ memory.py → Supabase: جدول memories
```

نقاط الضعف المعمارية (التفاصيل في `docs/INTAKE_REPORT.md`):

1. الحالة في الذاكرة، فتضيع المهام الجارية عند إعادة التشغيل أو النشر.
2. لا هوية للمستخدم ولا ملكية على `main` (يصلحه PR #399 بمفتاح عميل).
3. تنفيذ الكود (عند تفعيله) في عملية فرعية على الخادم نفسه، دون عزل حاوية.
4. لا مخطط قاعدة بيانات في المستودع.
5. لا قياس للتكلفة لكل مهمة على مستوى التخزين.

## 2. المعمارية المستهدفة: Modular Monolith

قرار: **خدمة واحدة مقسمة إلى وحدات**، وليست خدمات مصغرة. السبب: حدود الطبقة المجانية في Render، وفريق صغير، وسهولة الاختبار. نفصل العامل (worker) لاحقًا فقط إذا لزم.

```
app/
  api/          routers: runs, memory, me, files, connectors, skills, projects
  core/         config, ids, errors, auth (client key → owner)
  runs/         model, repository (Supabase), state machine, events
  engine/       planner, scheduler (DAG + map), agent loop, verifier, synthesizer
  tools/        registry (name, schema, risk, executor, approval policy), builtin tools
  llm/          providers, retry, usage/cost
  memory/       store, ranking, extraction, provenance
  artifacts/    (المرحلة 3) تخزين الملفات
  connectors/   (المرحلة 4) MCP client, GitHub
  skills/       (المرحلة 4) SKILL.md loader
migrations/     ملفات SQL مرقمة (لا تُطبق على الإنتاج دون موافقة المالك)
web/            تطبيق واجهة بوحدات ES بدون أداة بناء
```

### المبادئ

- **كل حدث يُكتب أولًا ثم يُبث.** جدول `run_events` هو مصدر الحقيقة، وSSE قراءة منه. هذا يجعل الاستئناف وإعادة الاتصال ممكنين.
- **آلة حالة صريحة للمهمة:** `queued → running → (waiting_input | waiting_approval) → running → done | failed | cancelled | interrupted`.
- **كل أداة تمر عبر السجل:** يتحقق السجل من الصلاحية والخطورة والموافقة، ويسجل الاستدعاء.
- **الملكية في طبقة المستودع (repository):** كل استعلام يأخذ `owner`، ولا يوجد مسار يقرأ بلا مالك.
- **الأسرار:** مفاتيح BYOK لا تُخزن على الخادم ولا تُسجل. مفاتيح المنصة في متغيرات بيئة Render فقط.

## 3. الهوية والملكية

| المرحلة | الآلية | لماذا |
|---|---|---|
| الآن (PR #399) | `X-Client-Key` عشوائي في المتصفح، والخادم يخزن sha256 فقط | مجاني، بلا حسابات، ويوقف التسرب فورًا |
| المرحلة 1 | توحيد مفتاح العميل ومساحة الذاكرة في هوية واحدة، مع تصدير واستيراد | تبسيط، وانتقال بين الأجهزة |
| لاحقًا (اختياري) | Supabase Auth (بريد أو OAuth، مجاني ضمن الحدود)، وربط الهوية المحلية بالحساب | فرق ومشاركة. يحتاج موافقة المالك على الإعداد |

## 4. مسار الانتقال (دون كسر الإنتاج)

1. دمج PR #399 بعد موافقة المالك. الملكية تعمل دون migration.
2. إضافة `migrations/001_runs.sql` الذي يوثق الجدول الحالي (`IF NOT EXISTS`)، و`003_run_events.sql` للأحداث. **لا تُطبق إلا بموافقة المالك.**
3. نقل الكود إلى الوحدات تدريجيًا خلف الواجهات نفسها، مع بقاء الاختبارات خضراء في كل خطوة.
4. تفعيل الكتابة إلى `run_events` خلف متغير بيئة. عند غياب الجدول يرجع النظام إلى السلوك الحالي.
5. عند بدء التشغيل: كل مهمة `running` بلا عامل حي تصبح `interrupted`، مع زر استئناف.
