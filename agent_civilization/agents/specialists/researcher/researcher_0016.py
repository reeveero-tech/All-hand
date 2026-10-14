#!/usr/bin/env python3
"""ResearcherAgent0016 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/researcher_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0016 as ResearcherAgent0016  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ResearcherAgent0016", "create"]

create = generate_create("researcher", ResearcherAgent0016, 16)
