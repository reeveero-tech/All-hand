# ميثاق المشروع (PROJECT_CHARTER)

## لماذا هذا المشروع؟

**AGOS** (Agent Civilization) هو منصة وكلاء ذكاء اصطناعي متعددة، تهدف إلى تنسيق مجتمع من الوكلاء المتخصصين (حتى 1,000 وكيل موزعين على 20 تخصصاً) لتنفيذ مهام متنوعة: تحليل بيانات، توليد كود، تصميم، تواصل، إلخ. يوفر المشروع:

1. **خادم API** (FastAPI) يوفر واجهات لإنشاء المهام، والتحدث مع الوكلاء، ومراقبة النظام.
2. **طبقة حضارة الوكلاء** (`agent_civilization/`) التي تدير الوكلاء والذاكرة والذكاء.
3. **واجهة ويب** (React/Vite) لعرض لوحات المعلومات والدردشة.
4. **طبقة تخزين** (Supabase REST) للمهام والأحداث والإحصائيات.

## الهدف النهائي

تشغيل منصة وكلاء قابلة للإنتاج، آمنة، وموثقة، مع خط CI/CD حقيقي يغطي المسارات الفعلية.

## المبادئ الحاكمة (غير القابلة للتفاوض)

- **قاعدة الدفعة الذرية**: أي commit/PR يجب أن يحتوي في نفس الدفعة على: التغيير + تحديث ROADMAP + تحديث PROJECT_STATE + تحديث الوثائق المتأثرة + دليل التحقق. ممنوع فصل التوثيق عن العمل.
- **لا عمل خارج الخارطة**: كل بند عمل يجب أن يكون مسجلاً في ROADMAP قبل تنفيذه.
- **الأدلة قبل الادعاء**: كل نتيجة يُطلب لها دليل (اختبار، سجل، build، أو لقطة شاشة).
- **الصدق في التوثيق**: تسجيل الفشل كما هو، وعدم المبالغة في ادعاء النجاح.

## النطاق

### داخل النطاق (المسار الحي المُتحقق منه)

- الخادم `server.py` + `agent_civilization/` (باستثناء `specialists/` stubs).
- `web/` (واجهة React التي يخدمها Docker — تم التحقق من البناء بنجاح).
- `agos_kernel/` (البنية التحتية الحية: circuit breaker، metrics، health، redis — مُستوردة من `server.py`).
- `tests/` (الاختبارات الجذرية).
- `.github/workflows/ci.yml` (CI الحالي — يحتاج إعادة توجيه).

### خارج النطاق (غير مرتبط بمسار التشغيل المُتحقق منه)

- `agos-kernel/` (dash) — حُذف من main في R-033 (D-026) وأُرشف في `archive/agos-kernel-v1`؛ لم يكن مستخدماً في أي مسار تشغيل.
- `src/` + root `package.json` (TanStack TS) — فشل البناء (`lovable-error-reporting` مفقود). راجع Q-002.
- المجلدات الجذرية غير المستوردة (`foundation`, `rie`, `meta`, `research`, `intelligence`, `evolution`, `enterprise`, `ecosystem`, `domain`, `cognition`, `seos`, `products`, `cloud`, `civilization`, `consolidation`, `constitution`, `execution`, `execution-program`, `knowledge-base`, `rie-sprint1`, `sdk`, `sdks`, `sdl-loop`, `spec-formal`, `sprint-000`, إلخ) — **لم يُتحقق من استخدامها** في مسار التشغيل؛ راجع Q-003.
- `agent_civilization/agents/specialists/` — 1,021 ملف Python (21 `__init__.py` + 1,000 ملف وكيل stub)؛ **لم يُتحقق من استيرادها** من أي ملف حي؛ راجع Q-004.
- `agents.yaml` — يُقرأ فقط من `agent_manager.py` و`auto_fetch_agents.py` (سكربتات مستقلة، غير مستوردة من `server.py`)؛ راجع Q-006.
