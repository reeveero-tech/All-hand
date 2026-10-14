#!/usr/bin/env python3
"""ResearcherAgent0296 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/researcher_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0296 as ResearcherAgent0296  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["ResearcherAgent0296", "create"]

create = generate_create("researcher", ResearcherAgent0296, 296)
