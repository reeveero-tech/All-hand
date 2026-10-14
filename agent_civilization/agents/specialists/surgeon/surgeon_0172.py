#!/usr/bin/env python3
"""SurgeonAgent0172 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0172 as SurgeonAgent0172  # noqa: F401

__all__ = ["SurgeonAgent0172", "create"]


def create(hub, aid: int = 172):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0172(name="surgeon_0172", hub=hub, agent_id=aid)
