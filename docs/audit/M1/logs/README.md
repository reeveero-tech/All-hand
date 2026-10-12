# سجلات M1 (R-001 + R-003a-d + R-002)

- `01_pytest_after_M1.log` — pytest tests/ بعد الإصلاحات: 61 ناجح (كان: 34 ناجح، 9 فاشل، 18 خطأ إعداد).
- `02_ruff_live_paths.log` — ruff على المسارات الحية قبل تعديل البوابة (يُحفظ عند التنفيذ).
- `03_ci_workflow_change.txt` — diff لملف `.github/workflows/ci.yml`.

**الأوامر**:
```
python -m pytest tests/ --tb=short -q
python -c "import server"
ruff check server.py agent_civilization/ --exclude agent_civilization/agents/specialists/ agos_kernel/ tests/ --statistics
```
