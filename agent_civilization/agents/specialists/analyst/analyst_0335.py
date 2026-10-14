#!/usr/bin/env python3
"""AnalystAgent0335 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/analyst_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0335 as AnalystAgent0335  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["AnalystAgent0335", "create"]

create = generate_create("analyst", AnalystAgent0335, 335)
