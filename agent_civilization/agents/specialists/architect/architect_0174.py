#!/usr/bin/env python3
"""ArchitectAgent0174 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0174 as ArchitectAgent0174  # noqa: F401

__all__ = ["ArchitectAgent0174", "create"]


def create(hub, aid: int = 174):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0174(name="architect_0174", hub=hub, agent_id=aid)
