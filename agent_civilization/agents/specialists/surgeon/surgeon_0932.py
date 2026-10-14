#!/usr/bin/env python3
"""SurgeonAgent0932 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0932 as SurgeonAgent0932  # noqa: F401

__all__ = ["SurgeonAgent0932", "create"]


def create(hub, aid: int = 932):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0932(name="surgeon_0932", hub=hub, agent_id=aid)
