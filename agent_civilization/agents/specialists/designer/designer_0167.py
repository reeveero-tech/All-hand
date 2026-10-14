#!/usr/bin/env python3
"""DesignerAgent0167 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0167 as DesignerAgent0167  # noqa: F401

__all__ = ["DesignerAgent0167", "create"]


def create(hub, aid: int = 167):
    """The historical create() — kept with the same default."""
    return DesignerAgent0167(name="designer_0167", hub=hub, agent_id=aid)
