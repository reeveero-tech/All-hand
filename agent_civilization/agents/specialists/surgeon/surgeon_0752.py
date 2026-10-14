#!/usr/bin/env python3
"""SurgeonAgent0752 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0752 as SurgeonAgent0752  # noqa: F401

__all__ = ["SurgeonAgent0752", "create"]


def create(hub, aid: int = 752):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0752(name="surgeon_0752", hub=hub, agent_id=aid)
