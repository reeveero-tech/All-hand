#!/usr/bin/env python3
"""DesignerAgent0307 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0307 as DesignerAgent0307  # noqa: F401

__all__ = ["DesignerAgent0307", "create"]


def create(hub, aid: int = 307):
    """The historical create() — kept with the same default."""
    return DesignerAgent0307(name="designer_0307", hub=hub, agent_id=aid)
