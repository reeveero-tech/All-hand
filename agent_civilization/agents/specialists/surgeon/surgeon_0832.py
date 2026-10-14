#!/usr/bin/env python3
"""SurgeonAgent0832 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/surgeon_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.surgeon import SurgeonAgent0832 as SurgeonAgent0832  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["SurgeonAgent0832", "create"]

create = generate_create("surgeon", SurgeonAgent0832, 832)
