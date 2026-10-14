#!/usr/bin/env python3
"""EducatorAgent0970 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/educator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0970 as EducatorAgent0970  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["EducatorAgent0970", "create"]

create = generate_create("educator", EducatorAgent0970, 970)
