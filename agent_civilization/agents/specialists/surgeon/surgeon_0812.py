#!/usr/bin/env python3
"""SurgeonAgent0812 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0812 as SurgeonAgent0812  # noqa: F401

__all__ = ["SurgeonAgent0812", "create"]


def create(hub, aid: int = 812):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0812(name="surgeon_0812", hub=hub, agent_id=aid)
