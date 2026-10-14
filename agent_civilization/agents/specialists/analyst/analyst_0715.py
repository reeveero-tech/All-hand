#!/usr/bin/env python3
"""AnalystAgent0715 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0715 as AnalystAgent0715  # noqa: F401

__all__ = ["AnalystAgent0715", "create"]


def create(hub, aid: int = 715):
    """The historical create() — kept with the same default."""
    return AnalystAgent0715(name="analyst_0715", hub=hub, agent_id=aid)
