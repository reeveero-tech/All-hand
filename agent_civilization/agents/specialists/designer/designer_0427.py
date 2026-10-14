#!/usr/bin/env python3
"""DesignerAgent0427 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/designer_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0427 as DesignerAgent0427  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["DesignerAgent0427", "create"]

create = generate_create("designer", DesignerAgent0427, 427)
