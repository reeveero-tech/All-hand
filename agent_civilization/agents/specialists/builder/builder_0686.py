#!/usr/bin/env python3
"""BuilderAgent0686 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/builder_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0686 as BuilderAgent0686  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["BuilderAgent0686", "create"]

create = generate_create("builder", BuilderAgent0686, 686)
