#!/usr/bin/env python3
"""SurgeonAgent0792 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0792 as SurgeonAgent0792  # noqa: F401

__all__ = ["SurgeonAgent0792", "create"]


def create(hub, aid: int = 792):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0792(name="surgeon_0792", hub=hub, agent_id=aid)
