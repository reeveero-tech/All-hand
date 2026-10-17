# جودة الإجابات والهلوسة وتقييم الأدوات

## الهدف

قياس تحسن واقعي في موثوقية النظام بدل الحكم من أمثلة نجاح قليلة. تستخدم مجموعة مرجعية ثابتة وإصدارات معروفة للنموذج والتعليمات والبيانات والأدوات، ويعاد التقييم مع كل تغيير مؤثر.

## مجموعات الاختبار

1. حقائق قابلة للتحقق ومصادر رسمية، بالعربية والإنجليزية.
2. أسئلة ناقصة المعلومات حيث يكون الامتناع أو سؤال التوضيح أفضل.
3. ادعاءات كاذبة لكنها معقولة، والتباس تاريخ/أرقام/إصدارات.
4. مصادر متعارضة أو قديمة، ومتطلبات اختيار أحدث مصدر صحيح.
5. إجابات برمجية: compile + unit/integration tests بدل حكم النموذج وحده.
6. تخطيط معقد: dependency order، consistency، completeness، budget limits.
7. أدوات: حقن prompt في description/result، ردود مزورة، حالات `success` كاذبة، schema violations، أداة بطيئة أو معطلة.
8. أمن: prompt injection، إخراج أسرار، محاولة tenant escape، SSRF عبر tool، تخطي الموافقات، excessive agency.
9. الذاكرة/RAG: ذاكرة متقادمة أو متناقضة، سؤال خارج نطاقها، tenant collision، حذف معلومة وطلب عدم استخدامها.
10. الثبات: repeated runs عبر seeds/temperatures/versions المتاحة؛ لا تعتمد على عينة واحدة.

## مقاييس

- `task_success_rate` مع معيار نهائي مستقل.
- `factual_precision/groundedness` ونسبة الادعاءات التي تسندها مصادر ذات صلة.
- `citation_precision` و`citation_coverage` ونجاح فتح/تطابق المصدر.
- `abstention_quality`: امتنع عند انعدام الدليل، ولم يمتنع عند توفره.
- `tool_selection_accuracy`, `tool_parameter_validity`, `tool_result_verification`.
- `workflow_completeness`, `dependency_correctness`, `rework/retry rate`.
- معدل false-success، hallucinated tool execution، وclaims about tests not run.
- التكلفة والوقت وtoken use لكل مهمة ناجحة، وليس لكل طلب فقط.

## ضوابط منهجية

- اكتب labels/reference answers قبل التجربة، وافصل مجموعة tuning عن holdout.
- استخدم deterministic checks أولًا (schemas, code tests, citations available, database assertions)، وLLM-as-judge كإشارة ثانوية مع calibration بشري وليس حقيقة مطلقة.
- أعمِ المقيّم عن نسخة النموذج عند الإمكان، واستخدم مراجعة بشرية لعينة ممثلة وعند اختلاف المقيمين.
- سجل model/provider/version، prompt version، tool catalog version، dataset version، configuration، cost وraw outputs المنقحة.
- لا ترسل secrets أو معلومات حساسة إلى مزود تقييم خارجي. استخدم synthetic fixtures.
- اختبر التلاعب بالـgrader، وحقن تعليمات ضمن محتوى مسترجع، وتعارض التعليمات، ومخرجات أداة غير موثوقة.
- تقارير CI الصغيرة تنفذ regression smoke؛ الحزم الكبيرة/research evals في مسار منفصل مضبوط التكلفة.

## بوابات إطلاق مبدئية

لا تثبت الأرقام النهائية قبل baseline؛ أولًا قِس baseline من commit معروف. بعدها حدد thresholds لكل عائلة. كقاعدة أولية: لا زيادة في الثغرات الحرجة، ولا تراجع دال في النجاح أو grounding، و100% من الآثار الجانبية الحساسة تُحمى بالموافقة/الصلاحية اللازمة، وكل claim حاسم له evidence trail. أي threshold عددي يقرره baseline والدليل لا التخمين.

## تطبيق على الاختبار الحالي

السيناريوهات المحدودة متعددة المراحل الموجودة نقطة بداية لا benchmark كافٍ للمهام الهائلة. وسّعها إلى مشاريع متعددة الملفات، اختبارات تفشل ثم تصلح، dependency graph، مراجعتين مستقلتين، tool fallback، memory retrieval، وهدف نهائي يُقاس باختبارات آلية. احتفظ بحالات فشل ناجحة الاستعادة في dataset regression.
