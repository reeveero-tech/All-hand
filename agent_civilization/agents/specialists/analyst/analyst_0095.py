#!/usr/bin/env python3
"""AnalystAgent0095 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0095 as AnalystAgent0095  # noqa: F401

__all__ = ["AnalystAgent0095", "create"]


def create(hub, aid: int = 95):
    """The historical create() — kept with the same default."""
    return AnalystAgent0095(name="analyst_0095", hub=hub, agent_id=aid)
