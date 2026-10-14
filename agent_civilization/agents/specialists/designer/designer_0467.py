#!/usr/bin/env python3
"""DesignerAgent0467 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0467 as DesignerAgent0467  # noqa: F401

__all__ = ["DesignerAgent0467", "create"]


def create(hub, aid: int = 467):
    """The historical create() — kept with the same default."""
    return DesignerAgent0467(name="designer_0467", hub=hub, agent_id=aid)
