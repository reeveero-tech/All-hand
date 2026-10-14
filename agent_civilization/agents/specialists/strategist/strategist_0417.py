#!/usr/bin/env python3
"""StrategistAgent0417 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/strategist_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.strategist import StrategistAgent0417 as StrategistAgent0417  # noqa: F401

__all__ = ["StrategistAgent0417", "create"]


def create(hub, aid: int = 417):
    """The historical create() — kept with the same default."""
    return StrategistAgent0417(name="strategist_0417", hub=hub, agent_id=aid)
