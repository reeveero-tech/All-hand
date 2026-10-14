#!/usr/bin/env python3
"""AnalystAgent0475 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0475 as AnalystAgent0475  # noqa: F401

__all__ = ["AnalystAgent0475", "create"]


def create(hub, aid: int = 475):
    """The historical create() — kept with the same default."""
    return AnalystAgent0475(name="analyst_0475", hub=hub, agent_id=aid)
