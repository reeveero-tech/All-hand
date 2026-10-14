#!/usr/bin/env python3
"""AnalystAgent0855 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0855 as AnalystAgent0855  # noqa: F401

__all__ = ["AnalystAgent0855", "create"]


def create(hub, aid: int = 855):
    """The historical create() — kept with the same default."""
    return AnalystAgent0855(name="analyst_0855", hub=hub, agent_id=aid)
