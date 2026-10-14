#!/usr/bin/env python3
"""ResearcherAgent0456 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0456 as ResearcherAgent0456  # noqa: F401

__all__ = ["ResearcherAgent0456", "create"]


def create(hub, aid: int = 456):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0456(name="researcher_0456", hub=hub, agent_id=aid)
