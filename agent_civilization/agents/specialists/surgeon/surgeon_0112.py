#!/usr/bin/env python3
"""SurgeonAgent0112 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0112 as SurgeonAgent0112  # noqa: F401

__all__ = ["SurgeonAgent0112", "create"]


def create(hub, aid: int = 112):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0112(name="surgeon_0112", hub=hub, agent_id=aid)
