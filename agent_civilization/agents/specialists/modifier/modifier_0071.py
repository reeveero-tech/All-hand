#!/usr/bin/env python3
"""ModifierAgent0071 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/modifier_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0071 as ModifierAgent0071  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ModifierAgent0071", "create"]

create = generate_create("modifier", ModifierAgent0071, 71)
