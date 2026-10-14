#!/usr/bin/env python3
"""EducatorAgent0950 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/educator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0950 as EducatorAgent0950  # noqa: F401

__all__ = ["EducatorAgent0950", "create"]


def create(hub, aid: int = 950):
    """The historical create() — kept with the same default."""
    return EducatorAgent0950(name="educator_0950", hub=hub, agent_id=aid)
