#!/usr/bin/env python3
"""BuilderAgent0066 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/builder_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0066 as BuilderAgent0066  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["BuilderAgent0066", "create"]

create = generate_create("builder", BuilderAgent0066, 66)
