#!/usr/bin/env python3
"""StrategistAgent0617 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/strategist_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.strategist import StrategistAgent0617 as StrategistAgent0617  # noqa: F401

__all__ = ["StrategistAgent0617", "create"]


def create(hub, aid: int = 617):
    """The historical create() — kept with the same default."""
    return StrategistAgent0617(name="strategist_0617", hub=hub, agent_id=aid)
