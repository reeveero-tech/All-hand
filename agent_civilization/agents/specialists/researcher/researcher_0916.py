#!/usr/bin/env python3
"""ResearcherAgent0916 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/researcher_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.researcher import ResearcherAgent0916 as ResearcherAgent0916  # noqa: F401

__all__ = ["ResearcherAgent0916", "create"]


def create(hub, aid: int = 916):
    """The historical create() — kept with the same default."""
    return ResearcherAgent0916(name="researcher_0916", hub=hub, agent_id=aid)
