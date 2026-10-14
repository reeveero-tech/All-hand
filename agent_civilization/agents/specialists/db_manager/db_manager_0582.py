#!/usr/bin/env python3
"""DbManagerAgent0582 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/db_manager_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.db_manager import DbManagerAgent0582 as DbManagerAgent0582  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["DbManagerAgent0582", "create"]

create = generate_create("db_manager", DbManagerAgent0582, 582)
