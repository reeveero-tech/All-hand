#!/usr/bin/env python3
"""ReviewerAgent0109 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/reviewer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0109 as ReviewerAgent0109  # noqa: F401

__all__ = ["ReviewerAgent0109", "create"]


def create(hub, aid: int = 109):
    """The historical create() — kept with the same default."""
    return ReviewerAgent0109(name="reviewer_0109", hub=hub, agent_id=aid)
