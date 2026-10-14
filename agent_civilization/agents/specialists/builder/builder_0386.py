#!/usr/bin/env python3
"""BuilderAgent0386 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/builder_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.builder import BuilderAgent0386 as BuilderAgent0386  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["BuilderAgent0386", "create"]

create = generate_create("builder", BuilderAgent0386, 386)
