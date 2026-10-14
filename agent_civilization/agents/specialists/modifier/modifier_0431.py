#!/usr/bin/env python3
"""ModifierAgent0431 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/modifier_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0431 as ModifierAgent0431  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ModifierAgent0431", "create"]

create = generate_create("modifier", ModifierAgent0431, 431)
