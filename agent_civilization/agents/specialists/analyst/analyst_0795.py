#!/usr/bin/env python3
"""AnalystAgent0795 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0795 as AnalystAgent0795  # noqa: F401

__all__ = ["AnalystAgent0795", "create"]


def create(hub, aid: int = 795):
    """The historical create() — kept with the same default."""
    return AnalystAgent0795(name="analyst_0795", hub=hub, agent_id=aid)
