#!/usr/bin/env python3
"""EducatorAgent0850 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/educator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0850 as EducatorAgent0850  # noqa: F401

__all__ = ["EducatorAgent0850", "create"]


def create(hub, aid: int = 850):
    """The historical create() — kept with the same default."""
    return EducatorAgent0850(name="educator_0850", hub=hub, agent_id=aid)
