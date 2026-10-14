#!/usr/bin/env python3
"""SurgeonAgent0132 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/surgeon_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0132 as SurgeonAgent0132  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["SurgeonAgent0132", "create"]

create = generate_create("surgeon", SurgeonAgent0132, 132)
