#!/usr/bin/env python3
"""ArchitectAgent0514 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/architect_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0514 as ArchitectAgent0514  # noqa: F401

__all__ = ["ArchitectAgent0514", "create"]


def create(hub, aid: int = 514):
    """The historical create() — kept with the same default."""
    return ArchitectAgent0514(name="architect_0514", hub=hub, agent_id=aid)
