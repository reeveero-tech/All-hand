#!/usr/bin/env python3
"""AnalystAgent0015 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0015 as AnalystAgent0015  # noqa: F401

__all__ = ["AnalystAgent0015", "create"]


def create(hub, aid: int = 15):
    """The historical create() — kept with the same default."""
    return AnalystAgent0015(name="analyst_0015", hub=hub, agent_id=aid)
