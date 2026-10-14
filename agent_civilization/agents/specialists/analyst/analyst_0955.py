#!/usr/bin/env python3
"""AnalystAgent0955 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0955 as AnalystAgent0955  # noqa: F401

__all__ = ["AnalystAgent0955", "create"]


def create(hub, aid: int = 955):
    """The historical create() — kept with the same default."""
    return AnalystAgent0955(name="analyst_0955", hub=hub, agent_id=aid)
