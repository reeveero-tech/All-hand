#!/usr/bin/env python3
"""DesignerAgent0247 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0247 as DesignerAgent0247  # noqa: F401

__all__ = ["DesignerAgent0247", "create"]


def create(hub, aid: int = 247):
    """The historical create() — kept with the same default."""
    return DesignerAgent0247(name="designer_0247", hub=hub, agent_id=aid)
