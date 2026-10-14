#!/usr/bin/env python3
"""DbManagerAgent0862 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/db_manager_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0862 as DbManagerAgent0862  # noqa: F401

__all__ = ["DbManagerAgent0862", "create"]


def create(hub, aid: int = 862):
    """The historical create() — kept with the same default."""
    return DbManagerAgent0862(name="db_manager_0862", hub=hub, agent_id=aid)
