#!/usr/bin/env python3
"""AnalystAgent0395 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0395 as AnalystAgent0395  # noqa: F401

__all__ = ["AnalystAgent0395", "create"]


def create(hub, aid: int = 395):
    """The historical create() — kept with the same default."""
    return AnalystAgent0395(name="analyst_0395", hub=hub, agent_id=aid)
