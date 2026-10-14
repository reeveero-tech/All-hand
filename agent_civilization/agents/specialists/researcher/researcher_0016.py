#!/usr/bin/env python3
"""ResearcherAgent0016 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0016 as ResearcherAgent0016  # noqa: F401

__all__ = ["ResearcherAgent0016", "create"]


def create(hub, aid: int = 16):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0016(name="researcher_0016", hub=hub, agent_id=aid)
