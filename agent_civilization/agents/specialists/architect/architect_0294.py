#!/usr/bin/env python3
"""ArchitectAgent0294 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0294 as ArchitectAgent0294  # noqa: F401

__all__ = ["ArchitectAgent0294", "create"]


def create(hub, aid: int = 294):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0294(name="architect_0294", hub=hub, agent_id=aid)
