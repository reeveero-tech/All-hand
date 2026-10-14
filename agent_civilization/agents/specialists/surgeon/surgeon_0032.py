#!/usr/bin/env python3
"""SurgeonAgent0032 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0032 as SurgeonAgent0032  # noqa: F401

__all__ = ["SurgeonAgent0032", "create"]


def create(hub, aid: int = 32):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0032(name="surgeon_0032", hub=hub, agent_id=aid)
