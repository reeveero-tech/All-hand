#!/usr/bin/env python3
"""EducatorAgent0330 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/educator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0330 as EducatorAgent0330  # noqa: F401

__all__ = ["EducatorAgent0330", "create"]


def create(hub, aid: int = 330):
    """The historical create() — kept with the same default."""
    return EducatorAgent0330(name="educator_0330", hub=hub, agent_id=aid)
