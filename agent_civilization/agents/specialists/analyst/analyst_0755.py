#!/usr/bin/env python3
"""AnalystAgent0755 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0755 as AnalystAgent0755  # noqa: F401

__all__ = ["AnalystAgent0755", "create"]


def create(hub, aid: int = 755):
    """The historical create() — kept with the same default."""
    return AnalystAgent0755(name="analyst_0755", hub=hub, agent_id=aid)
