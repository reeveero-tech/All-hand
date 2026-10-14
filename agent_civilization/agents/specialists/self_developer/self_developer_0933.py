#!/usr/bin/env python3
"""SelfDeveloperAgent0933 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/self_developer_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0933 as SelfDeveloperAgent0933  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["SelfDeveloperAgent0933", "create"]

create = generate_create("self_developer", SelfDeveloperAgent0933, 933)
