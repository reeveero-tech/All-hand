# M0 — Baseline Audit

- **التاريخ**: 2026-10-12
- **المرجع**: `be480820b414b679ca7c5252d49e17257ca6d774`
- **البيئة**: Python 3.14.3 / 3.12.13، Node v20.20.2، pytest 9.1.1، ruff

## الملفات

- [EVIDENCE.md](EVIDENCE.md) — النتائج والتحليلات المرتبطة بالسجلات.
- [logs/](logs/) — 12 سجل تنفيذ (الأمر، returncode، stdout/stderr المقتطعة، المدة) — الاقتطاع موثق داخل EVIDENCE.
- [BASELINE_SHA.txt](BASELINE_SHA.txt)، [ENVIRONMENT.txt](ENVIRONMENT.txt).

## ملخص

| الفحص | النتيجة |
|---|---|
| `pytest tests/` | 34 ناجح، 9 فاشل، 18 خطأ إعداد |
| `import server` | فشل (3.12/3.14) — استيراد `utc` |
| `pytest agos-kernel/ --collect-only` | 4 أخطاء جمع (145 مُجمع) |
| `pytest agos-kernel/` (كامل) | مهلة 120 ثانية — تجربة غير مكتملة |
| `ruff agos-kernel/` | 8,961 خطأ |
| `ruff` المسارات الحية | 525 خطأ |
| `npm run build` في `web/` | نجاح (902ms) |
| `npm run build` في الجذر | فشل (rc=1) |
| Docker build | لم يُنفَّذ (Docker غير متوفر) |

## أبرز الاكتشافات

1. `server.py:28` — استيراد `utc` غير صالح على Python 3.12+.
2. CI يستهدف `agos-kernel/` فقط ولا يشمل المسارات الحية.
3. 9 إخفاقات اختبار بأربعة أسباب جذرية مختلفة.
4. `/health` يعيد 200 دائماً (يحسب 503 ولا يمرره).
5. المصادقة غير مُفرضة فعلياً على أي مسار.
6. واجهة الجذر (TanStack) لا تُبنى لملف مفقود.
7. 1,021 ملفاً في `specialists/` و~50 دليلاً جذرياً — غير مرتبطة بمسار التشغيل المُتحقق منه (أسئلة حسم: Q-001..Q-004).
