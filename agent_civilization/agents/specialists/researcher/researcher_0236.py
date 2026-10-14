#!/usr/bin/env python3
"""ResearcherAgent0236 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0236 as ResearcherAgent0236  # noqa: F401

__all__ = ["ResearcherAgent0236", "create"]


def create(hub, aid: int = 236):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0236(name="researcher_0236", hub=hub, agent_id=aid)
