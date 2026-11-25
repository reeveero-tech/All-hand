"""The baseline migration must cover every column the code writes."""
import re
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "migrations" / "001_baseline.sql").read_text()


def _columns(table: str) -> set[str]:
    body = re.search(rf"create table if not exists public\.{table} \((.*?)\n\);", SQL, re.S).group(1)
    return {line.strip().split()[0] for line in body.splitlines() if line.strip()}


def test_runs_columns_cover_store_save():
    src = (Path(__file__).resolve().parents[1] / "app" / "store.py").read_text()
    row = re.search(r"row = \{(.*?)\}", src, re.S).group(1)
    keys = set(re.findall(r'"(\w+)":', row))
    assert keys and keys <= _columns("runs")


def test_memories_columns_cover_memory_store():
    src = (Path(__file__).resolve().parents[1] / "app" / "memory.py").read_text()
    for col in ("id", "space", "kind", "content", "run_id", "importance", "created_at"):
        assert col in _columns("memories")
        assert col in src


def test_migration_is_idempotent_and_non_destructive():
    low = SQL.lower()
    assert "drop " not in low and "delete " not in low and "truncate" not in low
    assert low.count("create table if not exists") == 2


def test_baseline_plus_provenance_cover_every_memory_column_written():
    from app import memory
    sql_002 = (Path(__file__).resolve().parents[1] / "migrations" / "002_memory_provenance.sql").read_text().lower()
    added = set(re.findall(r"add column if not exists (\w+)", sql_002))
    written = {"id", "kind", "content", "run_id", "importance", "space", "created_at", *memory.META}
    assert written <= _columns("memories") | added
    for n in memory.NATURES:  # every nature the code can write passes the CHECK constraint
        assert f"'{n}'" in sql_002
    assert "drop column" not in sql_002.split("-- rollback")[0]
