# الدراسة المقارنة للمنتجات المرجعية

- **الحالة:** `designed`.
- **تاريخ الجمع:** 2026-11-25.

## منهج التوثيق

لكل معلومة مستوى ثقة:

- **معلن:** صفحة تسويقية أو مقال من الشركة.
- **موثق:** مركز مساعدة أو وثائق تقنية رسمية.
- **مختبر:** جرّبناه بأنفسنا.

**لم يُختبر أي منتج عمليًا في هذه الدفعة**، فكلها تتطلب اشتراكات مدفوعة، ولم يُنفق أي مبلغ. لذلك كل ما يلي إما «معلن» أو «موثق».

---

## 1. Perplexity Computer

المصادر:

- [What is Computer? — Help Center](https://perplexity-help-center.mintlify.app/help-center/en/articles/13837784-what-is-computer)
- [صفحة المنتج](https://www.perplexity.ai/en-GB/products/computer)
- [The AI is the Computer — المدونة](https://www.perplexity.ai/hub/blog/the-ai-is-the-computer)
- [What are plugins?](https://www.perplexity.ai/help-center/en/articles/2026100701-what-are-plugins)
- [Computer MCP Server — Docs](https://perplexity.mintlify.app/docs/getting-started/integrations/computer-mcp-server)
- [How AI Agents Reshape Knowledge Work](https://www.perplexity.ai/hub/blog/how-ai-agents-reshape-knowledge-work)

| المجال | ما يقدمه المنتج | كيف يعمل | الثقة | ما نتعلمه | ما سنبنيه في All-hand |
|---|---|---|---|---|---|
| بدء المهمة | وصف النتيجة المطلوبة بلغة طبيعية مع الملفات والأدوات | مربع واحد، والنظام يقرر الباقي | معلن | المستخدم لا يختار الوكلاء | مربع مهمة واحد مع إرفاق ومشروع. لا أرقام وكلاء افتراضيًا |
| التخطيط والوكلاء | منسق يقسم الهدف إلى مهام ويولّد وكلاء فرعيين متخصصين. نماذج متعددة (20+) | planner ← orchestrator ← sub-agents، مع ذاكرة مشتركة | معلن (المدونة) | الفصل بين المخطط والمنسق والعامل | موجود جزئيًا لدينا. نضيف اختيار النموذج حسب الدور |
| التوازي والمهام الطويلة | مهام في الخلفية لساعات، وجدولة ومحفّزات (Automations) | تشغيل غير متزامن، والمستخدم يعود لاحقًا | موثق | الحالة الدائمة شرط أساسي | عامل خلفي وحالة مهمة في قاعدة البيانات (المرحلة 2) |
| بيئة التنفيذ | sandbox سحابي: نظام ملفات، shell، متصفح | «الذكاء الاصطناعي هو الحاسوب» | معلن | الملفات وتشغيل الكود هما ما يحوّل الإجابة إلى عمل | مساحة ملفات لكل مهمة، وتشغيل كود في حاوية (المرحلة 3) |
| الموصلات | أكثر من 400 موصل، وOAuth بخطوة واحدة، وMCP مخصص | الموصل أداة مصادَق عليها. الوكيل لا يرى إلا الصلاحيات المطلوبة | موثق | لا تعرض موصلًا لا يتصل | إطار موصلات + MCP عن بُعد. نبدأ بموصل واحد حقيقي (GitHub) |
| المهارات | ملفات Markdown قابلة للرفع، تُحمَّل عند الحاجة. Plugins تجمع موصلات ومهارات | تعليمات منهجية قابلة لإعادة الاستخدام | موثق | المهارة «كيف» والموصل «ماذا» | سجل مهارات بصيغة `SKILL.md` (المرحلة 4) |
| حالات المهمة | `working` / `input_required` / `completed` / `cancelled` / `failed` | حالة `input_required` للموافقات والأسئلة | موثق (MCP Server) | الموافقة حالة رسمية في دورة الحياة، وليست نافذة عابرة | نضيف `waiting_input` و`waiting_approval` |
| التسليم | ملفات ومصادر مرفقة بالنتيجة | `files[]` و`sources[]` في النتيجة | موثق | المخرجات كيانات لها رابط ونوع وحجم | كيان Artifact (المرحلة 3) |

## 2. ChatGPT Work (كان سابقًا ChatGPT agent)

المصادر:

- [ChatGPT agent — Help Center](https://help.openai.com/en/articles/11752874-chatgpt-agent): الصفحة تذكر أن «ChatGPT agent is no longer available» وتحيل إلى Work.
- [ChatGPT Work and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work)
- [Get started with ChatGPT Work](https://learn.chatgpt.com/docs/get-started-with-work)
- [Scheduled tasks](https://help.openai.com/en/articles/10291617-chatgpt-work)
- [Introducing ChatGPT agent](https://www.openai.com/index/introducing-chatgpt-agent/)

| المجال | ما يقدمه المنتج | كيف يعمل | الثقة | ما نتعلمه | ما سنبنيه في All-hand |
|---|---|---|---|---|---|
| الفصل بين الأوضاع | Chat للأسئلة، Work للمهام والمخرجات، Codex للبرمجة | تجارب منفصلة في تطبيق واحد | موثق | ليست كل رسالة «مهمة» | وضعان: «سريع» للجواب المباشر و«مهمة» للعمل المنظم (المنسق يقرر بـ `simple`) |
| المتابعة والتوجيه | متابعة التقدم، والإجابة على الأسئلة، وتغيير الاتجاه أثناء العمل | رسائل المستخدم تصل إلى المهمة الجارية | موثق | التوجيه أثناء التنفيذ مهم | رسائل متابعة على المهمة الجارية (المرحلة 2) |
| الموافقات | موافقة أو رفض على الإجراءات المهمة. قواعد مخصصة: «نفّذ دون سؤال / إن سبقت الموافقة / اسأل / سلّمني» | الإجراء يوقف المهمة حتى يقرر المستخدم | موثق | «طلب مسودة لا يعني الإذن بالإرسال» | سياسة موافقة لكل أداة حسب الخطورة، وكيان Approval |
| المتصفح | متصفح مرئي بلقطات شاشة، و«Take over» لتسجيل الدخول دون التقاط لقطات | المستخدم يسيطر ثم يعيد التحكم | موثق | لا تُلتقط بيانات الدخول | المرحلة 6: متصفح معزول مع تسليم يدوي للمستخدم |
| الجدولة | مهام مرة واحدة أو متكررة أو بمحفّز من Gmail وSlack وGitHub | المهمة المجدولة تتوقف عند الحاجة لموافقة | موثق | الجدولة تعيد استخدام المهمة نفسها | المرحلة 8 |
| المشاريع | المشروع يجمع المحادثات والملفات والتعليمات | سياق مشترك | موثق | المشروع وحدة السياق | كيان Project (المرحلة 1–5) |
| الحدود | حدود شهرية لعدد المهام (مثلًا Plus: 40 رسالة/شهر في agent mode السابق) | — | موثق | الشفافية في الاستهلاك | عرض التكلفة والاستهلاك لكل مهمة |

## 3. Claude: Computer Use وSkills وMCP

المصادر:

- [Computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference)
- [The Complete Guide to Building Skills](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)

| المجال | ما يقدمه المنتج | كيف يعمل | الثقة | ما نتعلمه | ما سنبنيه في All-hand |
|---|---|---|---|---|---|
| استخدام الحاسوب | Toolset (`computer_toolset_20260801`) فيه 17 أداة: لقطة شاشة، نقر، كتابة، تكبير | النموذج يطلب الفعل، و**تطبيقك ينفذه في بيئة تتحكم بها** | موثق | التنفيذ مسؤولية المنصة، والنموذج يقترح فقط | Browser Runtime نملكه نحن: حاوية وPlaywright، وكل خطوة تُسجَّل بلقطة |
| أدوات الخادم والعميل | بحث ويب، جلب، تنفيذ كود، ذاكرة، bash، محرر نصوص، MCP connector | تصنيف server / client tools | موثق | يحتاج سجل الأدوات تصنيف «من ينفذ» | حقل `executor` وحقل `risk` لكل أداة |
| المهارات | مجلد فيه `SKILL.md` بـ frontmatter (`name`، `description`) ومراجع وسكربتات. يُحمَّل عند المطابقة فقط | تحميل تدريجي (progressive disclosure) | موثق | الوصف قصير دائمًا في السياق، والمحتوى يُحمَّل عند الحاجة | نفس الصيغة، لنتوافق مع مهارات موجودة |
| MCP مقابل Skills | «MCP = ماذا يستطيع Claude فعله، Skills = كيف يفعله» | — | موثق (الدليل) | فصل واضح بين الاتصال والمنهج | صفحتان منفصلتان في الواجهة، يربطهما مفهوم Plugin لاحقًا |

## 4. Manus

المصادر:

- [Welcome — Manus Docs](https://manus.im/docs/introduction/welcome)
- [Wide Research — Docs](https://manus.im/docs/features/wide-research)
- [Wide Research: Beyond the Context Window](https://manus.im/blog/manus-wide-research-solve-context-problem)

| المجال | ما يقدمه المنتج | كيف يعمل | الثقة | ما نتعلمه | ما سنبنيه في All-hand |
|---|---|---|---|---|---|
| البيئة | آلة افتراضية كاملة بإنترنت ونظام ملفات دائم، وتستطيع تثبيت برامج | sandbox لكل مهمة | موثق | — | المرحلة 3 |
| Wide Research | مئات الوكلاء بالتوازي، لكل عنصر وكيل بسياق نظيف. الوكلاء لا يتواصلون فيما بينهم، وكل التنسيق عبر المتحكم الرئيسي | تقسيم ← نشر متوازٍ ← تجميع | موثق (المدونة) | **السياق النظيف لكل عنصر يمنع تدهور الجودة وانتشار الهلوسة** | نمط «map» في المخطط: مهمة واحدة تتكرر على قائمة عناصر بوكيل لكل عنصر (المرحلة 2) |
| الموصلات | Gmail وNotion وSlack وGitHub | — | موثق | — | — |

## 5. MCP (البروتوكول نفسه)

المصادر:

- [Specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)
- [إعلان إصدار 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/)

**موثق:** الإصدار الأحدث 2026-07-28. نواته عديمة الحالة (stateless)، وفيه Multi Round-Trip Requests، وTasks للعمل الطويل، وتقوية التفويض، وإطار امتدادات يشمل «Skills over MCP». الرسائل JSON-RPC 2.0.

**القرار:** نبني عميل MCP عبر HTTP (streamable) لموصلات المستخدم المخصصة، ونعامل كل أداة MCP كأداة في سجلنا بمستوى خطورة وسياسة موافقة.

---

## الخلاصة: ما الذي يميز All-hand

1. **الصدق القابل للتحقق:** مدقق مستقل لكل ناتج، وإعلان صريح لما فشل، وتمييز «تقدير» عن «حقيقة». المنتجات المرجعية لا توثق مدققًا مستقلًا ظاهرًا للمستخدم.
2. **العربية أولًا** مع RTL صحيح ودعم الإنجليزية. المنتجات المرجعية إنجليزية أولًا.
3. **مفتوح المصدر وBYOK:** يعمل بمفتاح المستخدم أو بنموذج مجاني، دون اشتراك إلزامي.
4. **شفافية كاملة للعمل:** كل وكيل وأداة وحكم مدقق وتكلفة ظاهر من سجل حقيقي.

## ما لن ندعيه

400 موصل، أو 20 نموذجًا، أو مئات الوكلاء المتوازين، أو تشغيلًا لأشهر، قبل أن يكون كل ذلك منفذًا ومختبرًا لدينا.
