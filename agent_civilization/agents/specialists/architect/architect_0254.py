#!/usr/bin/env python3
"""ArchitectAgent0254 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0254 as ArchitectAgent0254  # noqa: F401

__all__ = ["ArchitectAgent0254", "create"]


def create(hub, aid: int = 254):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0254(name="architect_0254", hub=hub, agent_id=aid)
