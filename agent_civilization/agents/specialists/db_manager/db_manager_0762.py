#!/usr/bin/env python3
"""DbManagerAgent0762 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/db_manager_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0762 as DbManagerAgent0762  # noqa: F401

__all__ = ["DbManagerAgent0762", "create"]


def create(hub, aid: int = 762):
    """The historical create() — kept with the same default."""
    return DbManagerAgent0762(name="db_manager_0762", hub=hub, agent_id=aid)
