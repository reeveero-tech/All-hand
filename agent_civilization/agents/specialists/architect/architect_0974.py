#!/usr/bin/env python3
"""ArchitectAgent0974 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/architect_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.architect import ArchitectAgent0974 as ArchitectAgent0974  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ArchitectAgent0974", "create"]

create = generate_create("architect", ArchitectAgent0974, 974)
