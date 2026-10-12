# سجلات M1 (R-001 + R-003a-d)

- `01_pytest_after_M1.log` — pytest tests/ بعد الإصلاحات: 61 ناجح (كان: 34 ناجح، 9 فاشل، 18 خطأ إعداد).

**الأوامر**:
```
python -m pytest tests/ --tb=short -q
python -c "import server"
ruff check <changed files> --statistics
```
