#!/usr/bin/env python3
"""SurgeonAgent0392 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0392 as SurgeonAgent0392  # noqa: F401

__all__ = ["SurgeonAgent0392", "create"]


def create(hub, aid: int = 392):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0392(name="surgeon_0392", hub=hub, agent_id=aid)
