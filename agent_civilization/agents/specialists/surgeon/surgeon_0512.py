#!/usr/bin/env python3
"""SurgeonAgent0512 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0512 as SurgeonAgent0512  # noqa: F401

__all__ = ["SurgeonAgent0512", "create"]


def create(hub, aid: int = 512):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0512(name="surgeon_0512", hub=hub, agent_id=aid)
