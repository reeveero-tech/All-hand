#!/usr/bin/env python3
"""DbManagerAgent0922 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/db_manager_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0922 as DbManagerAgent0922  # noqa: F401

__all__ = ["DbManagerAgent0922", "create"]


def create(hub, aid: int = 922):
    """The historical create() — kept with the same default."""
    return DbManagerAgent0922(name="db_manager_0922", hub=hub, agent_id=aid)
