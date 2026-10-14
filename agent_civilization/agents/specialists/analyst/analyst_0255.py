#!/usr/bin/env python3
"""AnalystAgent0255 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0255 as AnalystAgent0255  # noqa: F401

__all__ = ["AnalystAgent0255", "create"]


def create(hub, aid: int = 255):
    """The historical create() — kept with the same default."""
    return AnalystAgent0255(name="analyst_0255", hub=hub, agent_id=aid)
