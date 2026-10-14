#!/usr/bin/env python3
"""ArchitectAgent0774 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/architect_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0774 as ArchitectAgent0774  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ArchitectAgent0774", "create"]

create = generate_create("architect", ArchitectAgent0774, 774)
