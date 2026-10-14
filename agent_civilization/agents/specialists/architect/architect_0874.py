#!/usr/bin/env python3
"""ArchitectAgent0874 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0874 as ArchitectAgent0874  # noqa: F401

__all__ = ["ArchitectAgent0874", "create"]


def create(hub, aid: int = 874):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0874(name="architect_0874", hub=hub, agent_id=aid)
