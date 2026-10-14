#!/usr/bin/env python3
"""AnalystAgent0615 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/analyst_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.analyst import AnalystAgent0615 as AnalystAgent0615  # noqa: F401

__all__ = ["AnalystAgent0615", "create"]


def create(hub, aid: int = 615):
    """The historical create() — kept with the same default."""
    return AnalystAgent0615(name="analyst_0615", hub=hub, agent_id=aid)
