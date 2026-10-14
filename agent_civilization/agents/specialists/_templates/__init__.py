"""R-105 (D-049): the 20 specialist template bodies — the single source of truth.

Before R-105, `specialists/` held 1,000 near-identical files (20 templates copied
50 times each; within a specialization the copies differed ONLY in class name,
agent_id, and logger name — verified by AST-normalized comparison of all 1,000
files in docs/audit/M6/logs/raw/r105_baseline_reference.json).

This package holds ONE REAL base class per specialization (`<spec>_base.py`)
plus the factory (`_factory.py`) and the class layout (`_spec_layout.py`).
The 50 classes per specialization are GENERATED at import time via `type()`
subclasses of the base — same names, same `__all__`, same `agent_id` defaults,
same `create()` defaults as before. The 1,000 historical files remain as thin
compatibility shims that re-export the generated classes (no duplicated
implementation logic).
"""
