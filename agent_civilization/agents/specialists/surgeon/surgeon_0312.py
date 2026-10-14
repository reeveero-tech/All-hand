#!/usr/bin/env python3
"""SurgeonAgent0312 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0312 as SurgeonAgent0312  # noqa: F401

__all__ = ["SurgeonAgent0312", "create"]


def create(hub, aid: int = 312):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0312(name="surgeon_0312", hub=hub, agent_id=aid)
