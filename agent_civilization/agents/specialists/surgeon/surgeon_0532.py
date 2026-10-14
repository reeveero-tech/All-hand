#!/usr/bin/env python3
"""SurgeonAgent0532 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0532 as SurgeonAgent0532  # noqa: F401

__all__ = ["SurgeonAgent0532", "create"]


def create(hub, aid: int = 532):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0532(name="surgeon_0532", hub=hub, agent_id=aid)
