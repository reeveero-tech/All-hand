# مراجع رسمية للمراجعة عند التنفيذ

**تاريخ البحث لهذه الحزمة: 2026-10-17.** يجب على Builder Agent إعادة التحقق من الإصدارات الثابتة والتوافق والترخيص والثغرات الأمنية وقت اعتماد الأداة؛ لا تعتبر الروابط التالية موافقة تلقائية على تثبيت أداة.

## ضغط الخدمات

- Grafana k6 — constant arrival rate executor: https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/  
  ملائم لتحديد معدل بدء iterations مستقل عن سرعة رد الخدمة؛ يجب التحقق من تخصيص VUs وعدم الخلط بين معدل الطلبات والنجاح.
- Grafana k6 — ramping arrival rate: https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/  
  ملائم للتدرج حتى نقطة الضغط المحددة.
- Locust docs: https://docs.locust.io/en/latest/  
  مرجع لاختبارات أحمال مكونة من سيناريوهات Python؛ راجع stable release/compatibility قبل إدخاله.

## اختبارات LLM والهلوسة

- Promptfoo red teaming: https://www.promptfoo.dev/docs/guides/llm-redteaming/  
  يغطي adversarial tests مثل prompt injection، data leaks، excessive agency والهلوسة. ثبّت النسخة بدل `@latest` في CI/production.
- OWASP GenAI Security Project — Top 10 for LLM Applications 2025: https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/  
  مرجع لبناء threat model، وليس بديلًا عن مراجعة مخاطر المشروع الفعلية.

## الأدوات والتنسيق والرصد

- MCP specification 2026-07-28 (تحقق من النسخة stable المعتمدة عند التنفيذ): https://modelcontextprotocol.io/specification/2026-07-28/server/tools  
  عقود الأدوات والـschemas يجب أن تُعامل بأمان؛ metadata/annotations الخارجة من خادم غير موثوق لا تمنح صلاحية بحد ذاتها.
- OpenTelemetry GenAI conventions (الوضع وبعض السمات قيد التطوير، لذلك تُراجع stability قبل التبني): https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md و https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md  
  مرجع لتتبع استدعاء الوكلاء وworkflow وtool execution. راجع حدود الخصوصية قبل تسجيل المحتوى.

## سياسة شراء/تثبيت الأدوات

لا تضف مكتبة لمجرد أنها رائجة أو “الأحدث”. افحص maintenance، stable releases، changelog، CVEs/advisories، license، runtime compatibility، security posture، تكلفة التشغيل، ووجود أداة مماثلة داخل المشروع. ثبّت إصدارًا محددًا داخل lockfile، وأرفق اختبار توافق ودليل في PR نفسها.
