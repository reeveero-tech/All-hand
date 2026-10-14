#!/usr/bin/env python3
"""DbManagerAgent0042 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/db_manager_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0042 as DbManagerAgent0042  # noqa: F401

__all__ = ["DbManagerAgent0042", "create"]


def create(hub, aid: int = 42):
    """The historical create() — kept with the same default."""
    return DbManagerAgent0042(name="db_manager_0042", hub=hub, agent_id=aid)
