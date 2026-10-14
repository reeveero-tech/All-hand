#!/usr/bin/env python3
"""AnalystAgent0635 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0635 as AnalystAgent0635  # noqa: F401

__all__ = ["AnalystAgent0635", "create"]


def create(hub, aid: int = 635):
    """The historical create() — kept with the same default."""
    return AnalystAgent0635(name="analyst_0635", hub=hub, agent_id=aid)
