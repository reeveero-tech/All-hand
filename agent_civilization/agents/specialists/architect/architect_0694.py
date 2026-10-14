#!/usr/bin/env python3
"""ArchitectAgent0694 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0694 as ArchitectAgent0694  # noqa: F401

__all__ = ["ArchitectAgent0694", "create"]


def create(hub, aid: int = 694):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0694(name="architect_0694", hub=hub, agent_id=aid)
