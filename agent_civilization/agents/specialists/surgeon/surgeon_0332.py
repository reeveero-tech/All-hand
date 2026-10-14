#!/usr/bin/env python3
"""SurgeonAgent0332 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0332 as SurgeonAgent0332  # noqa: F401

__all__ = ["SurgeonAgent0332", "create"]


def create(hub, aid: int = 332):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0332(name="surgeon_0332", hub=hub, agent_id=aid)
