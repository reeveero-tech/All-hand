#!/usr/bin/env python3
"""EducatorAgent0230 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/educator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0230 as EducatorAgent0230  # noqa: F401

__all__ = ["EducatorAgent0230", "create"]


def create(hub, aid: int = 230):
    """The historical create() — kept with the same default."""
    return EducatorAgent0230(name="educator_0230", hub=hub, agent_id=aid)
