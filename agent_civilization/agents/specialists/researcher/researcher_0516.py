#!/usr/bin/env python3
"""ResearcherAgent0516 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0516 as ResearcherAgent0516  # noqa: F401

__all__ = ["ResearcherAgent0516", "create"]


def create(hub, aid: int = 516):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0516(name="researcher_0516", hub=hub, agent_id=aid)
