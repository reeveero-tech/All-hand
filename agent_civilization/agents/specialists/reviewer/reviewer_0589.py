#!/usr/bin/env python3
"""ReviewerAgent0589 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/reviewer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0589 as ReviewerAgent0589  # noqa: F401

__all__ = ["ReviewerAgent0589", "create"]


def create(hub, aid: int = 589):
    """The historical create() — kept with the same default."""
    return ReviewerAgent0589(name="reviewer_0589", hub=hub, agent_id=aid)
