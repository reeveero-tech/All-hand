#!/usr/bin/env python3
"""ArchitectAgent0934 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0934 as ArchitectAgent0934  # noqa: F401

__all__ = ["ArchitectAgent0934", "create"]


def create(hub, aid: int = 934):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0934(name="architect_0934", hub=hub, agent_id=aid)
