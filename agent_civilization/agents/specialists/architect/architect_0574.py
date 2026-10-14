#!/usr/bin/env python3
"""ArchitectAgent0574 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0574 as ArchitectAgent0574  # noqa: F401

__all__ = ["ArchitectAgent0574", "create"]


def create(hub, aid: int = 574):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0574(name="architect_0574", hub=hub, agent_id=aid)
