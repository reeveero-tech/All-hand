#!/usr/bin/env python3
"""StrategistAgent0757 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/strategist_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.strategist import StrategistAgent0757 as StrategistAgent0757  # noqa: F401

__all__ = ["StrategistAgent0757", "create"]


def create(hub, aid: int = 757):
    """The historical create() — kept with the same default."""
    return StrategistAgent0757(name="strategist_0757", hub=hub, agent_id=aid)
