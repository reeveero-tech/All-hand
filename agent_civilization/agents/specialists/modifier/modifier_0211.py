#!/usr/bin/env python3
"""ModifierAgent0211 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/modifier_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0211 as ModifierAgent0211  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ModifierAgent0211", "create"]

create = generate_create("modifier", ModifierAgent0211, 211)
