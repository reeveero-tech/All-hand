#!/usr/bin/env python3
"""SurgeonAgent0052 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0052 as SurgeonAgent0052  # noqa: F401

__all__ = ["SurgeonAgent0052", "create"]


def create(hub, aid: int = 52):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0052(name="surgeon_0052", hub=hub, agent_id=aid)
