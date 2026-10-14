#!/usr/bin/env python3
"""StrategistAgent0837 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/strategist_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.strategist import StrategistAgent0837 as StrategistAgent0837  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["StrategistAgent0837", "create"]

create = generate_create("strategist", StrategistAgent0837, 837)
