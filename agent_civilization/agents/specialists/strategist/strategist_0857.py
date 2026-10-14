#!/usr/bin/env python3
"""StrategistAgent0857 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/strategist_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.strategist import StrategistAgent0857 as StrategistAgent0857  # noqa: F401

__all__ = ["StrategistAgent0857", "create"]


def create(hub, aid: int = 857):
    """The historical create() — kept with the same default."""
    return StrategistAgent0857(name="strategist_0857", hub=hub, agent_id=aid)
