# سجل التغييرات (CHANGELOG)

## 2026-10-12 — M0: Baseline Audit + Roadmap Bootstrap

### مضاف
- `docs/governance/` — مجموعة الوثائق الحاكمة: PROJECT_CHARTER، PROJECT_STATE، ROADMAP (R-000..R-030)، ARCHITECTURE، DOMAIN، API، DATA_MODEL، OPERATIONS، SECURITY، QUALITY، DECISIONS (D-001..D-007)، RISKS (RISK-001..016)، OPEN_QUESTIONS (Q-001..Q-014)، GOVERNANCE_GATE، README.
- `docs/audit/M0/` — دليل التحقق: EVIDENCE.md + 12 سجل تنفيذ (الأمر، returncode، stdout/stderr المقتطعة، المدة — الاقتطاع موثق في EVIDENCE) + BASELINE_SHA + ENVIRONMENT.

### خط الأساس المقاس
- `pytest tests/`: 34 ناجح، 9 فاشل، 18 خطأ إعداد (Python 3.14.3).
- `import server`: فشل على 3.12.13 و3.14.3 (استيراد `utc` من `datetime` — `server.py:28`).
- `ruff agos-kernel/`: 8,961 خطأ. `ruff` المسارات الحية: 525 خطأ.
- `pytest agos-kernel/ --collect-only`: 4 أخطاء جمع؛ التنفيذ الكامل انتهى بمهلة (تجربة غير مكتملة).
- `npm run build` في `web/`: نجاح (902ms). في الجذر: فشل (rc=1).
- Docker: لم يُنفَّذ (غير متوفر في بيئة التدقيق).

### مُحدَّث (وصل توثيقي)
- `README.md` الجذري — قسم حوكمة جديد يصل بـ`docs/governance/`.
- `SECURITY.md` الجذري — قسم "الوضع الأمني المقاس (M0)" يوثق الفجوة بين التوثيق والقياس ويحيل إلى `docs/governance/SECURITY.md`.

### لم يُغيَّر
- لا تغيير تطبيقي على أي كود أو إعدادات. `git diff` عن `be480820` يحتوي `docs/` + وصل README/SECURITY الجذريين فقط (تغيير توثيقي).
