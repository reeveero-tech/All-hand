#!/usr/bin/env python3
"""ReviewerAgent0069 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/reviewer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0069 as ReviewerAgent0069  # noqa: F401

__all__ = ["ReviewerAgent0069", "create"]


def create(hub, aid: int = 69):
    """The historical create() — kept with the same default."""
    return ReviewerAgent0069(name="reviewer_0069", hub=hub, agent_id=aid)
