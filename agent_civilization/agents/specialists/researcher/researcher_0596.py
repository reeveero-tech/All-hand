#!/usr/bin/env python3
"""ResearcherAgent0596 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0596 as ResearcherAgent0596  # noqa: F401

__all__ = ["ResearcherAgent0596", "create"]


def create(hub, aid: int = 596):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0596(name="researcher_0596", hub=hub, agent_id=aid)
