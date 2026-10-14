#!/usr/bin/env python3
"""DbManagerAgent0342 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/db_manager_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0342 as DbManagerAgent0342  # noqa: F401

__all__ = ["DbManagerAgent0342", "create"]


def create(hub, aid: int = 342):
    """The historical create() — kept with the same default."""
    return DbManagerAgent0342(name="db_manager_0342", hub=hub, agent_id=aid)
