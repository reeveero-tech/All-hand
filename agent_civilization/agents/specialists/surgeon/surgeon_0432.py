#!/usr/bin/env python3
"""SurgeonAgent0432 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0432 as SurgeonAgent0432  # noqa: F401

__all__ = ["SurgeonAgent0432", "create"]


def create(hub, aid: int = 432):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0432(name="surgeon_0432", hub=hub, agent_id=aid)
