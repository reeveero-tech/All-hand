#!/usr/bin/env python3
"""ResearcherAgent0276 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0276 as ResearcherAgent0276  # noqa: F401

__all__ = ["ResearcherAgent0276", "create"]


def create(hub, aid: int = 276):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0276(name="researcher_0276", hub=hub, agent_id=aid)
