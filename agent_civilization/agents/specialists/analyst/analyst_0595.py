#!/usr/bin/env python3
"""AnalystAgent0595 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0595 as AnalystAgent0595  # noqa: F401

__all__ = ["AnalystAgent0595", "create"]


def create(hub, aid: int = 595):
    """The historical create() — kept with the same default."""
    return AnalystAgent0595(name="analyst_0595", hub=hub, agent_id=aid)
