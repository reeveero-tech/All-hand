#!/usr/bin/env python3
"""AnalystAgent0115 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0115 as AnalystAgent0115  # noqa: F401

__all__ = ["AnalystAgent0115", "create"]


def create(hub, aid: int = 115):
    """The historical create() — kept with the same default."""
    return AnalystAgent0115(name="analyst_0115", hub=hub, agent_id=aid)
