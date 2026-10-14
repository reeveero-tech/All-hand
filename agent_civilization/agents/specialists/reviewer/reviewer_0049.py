#!/usr/bin/env python3
"""ReviewerAgent0049 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/reviewer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.reviewer import ReviewerAgent0049 as ReviewerAgent0049  # noqa: F401

__all__ = ["ReviewerAgent0049", "create"]


def create(hub, aid: int = 49):
    """The historical create() — kept with the same default."""
    return ReviewerAgent0049(name="reviewer_0049", hub=hub, agent_id=aid)
