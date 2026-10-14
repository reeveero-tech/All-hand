#!/usr/bin/env python3
"""ArchitectAgent0754 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0754 as ArchitectAgent0754  # noqa: F401

__all__ = ["ArchitectAgent0754", "create"]


def create(hub, aid: int = 754):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0754(name="architect_0754", hub=hub, agent_id=aid)
