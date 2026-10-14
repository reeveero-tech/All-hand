#!/usr/bin/env python3
"""ArchitectAgent0134 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0134 as ArchitectAgent0134  # noqa: F401

__all__ = ["ArchitectAgent0134", "create"]


def create(hub, aid: int = 134):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0134(name="architect_0134", hub=hub, agent_id=aid)
