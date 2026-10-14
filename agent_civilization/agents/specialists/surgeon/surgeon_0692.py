#!/usr/bin/env python3
"""SurgeonAgent0692 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/surgeon_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0692 as SurgeonAgent0692  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["SurgeonAgent0692", "create"]

create = generate_create("surgeon", SurgeonAgent0692, 692)
