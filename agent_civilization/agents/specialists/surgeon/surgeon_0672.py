#!/usr/bin/env python3
"""SurgeonAgent0672 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/surgeon_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0672 as SurgeonAgent0672  # noqa: F401

__all__ = ["SurgeonAgent0672", "create"]


def create(hub, aid: int = 672):
    """The historical create() — kept with the same default."""
    return SurgeonAgent0672(name="surgeon_0672", hub=hub, agent_id=aid)
