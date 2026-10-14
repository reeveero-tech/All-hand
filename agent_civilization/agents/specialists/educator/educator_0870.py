#!/usr/bin/env python3
"""EducatorAgent0870 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/educator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.educator import EducatorAgent0870 as EducatorAgent0870  # noqa: F401

__all__ = ["EducatorAgent0870", "create"]


def create(hub, aid: int = 870):
    """The historical create() — kept with the same default."""
    return EducatorAgent0870(name="educator_0870", hub=hub, agent_id=aid)
