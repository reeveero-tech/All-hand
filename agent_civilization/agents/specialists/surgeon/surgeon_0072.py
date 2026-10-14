#!/usr/bin/env python3
"""SurgeonAgent0072 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0072 as SurgeonAgent0072  # noqa: F401

__all__ = ["SurgeonAgent0072", "create"]


def create(hub, aid: int = 72):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0072(name="surgeon_0072", hub=hub, agent_id=aid)
