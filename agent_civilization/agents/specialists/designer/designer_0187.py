#!/usr/bin/env python3
"""DesignerAgent0187 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0187 as DesignerAgent0187  # noqa: F401

__all__ = ["DesignerAgent0187", "create"]


def create(hub, aid: int = 187):
    """The historical create() — kept with the same default."""
    return DesignerAgent0187(name="designer_0187", hub=hub, agent_id=aid)
