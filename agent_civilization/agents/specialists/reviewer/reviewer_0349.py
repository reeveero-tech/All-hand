#!/usr/bin/env python3
"""ReviewerAgent0349 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/reviewer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0349 as ReviewerAgent0349  # noqa: F401

__all__ = ["ReviewerAgent0349", "create"]


def create(hub, aid: int = 349):
    """The historical create() — kept with the same default."""
    return ReviewerAgent0349(name="reviewer_0349", hub=hub, agent_id=aid)
