#!/usr/bin/env python3
"""ResearcherAgent0216 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0216 as ResearcherAgent0216  # noqa: F401

__all__ = ["ResearcherAgent0216", "create"]


def create(hub, aid: int = 216):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0216(name="researcher_0216", hub=hub, agent_id=aid)
