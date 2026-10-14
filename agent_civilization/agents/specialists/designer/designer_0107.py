#!/usr/bin/env python3
"""DesignerAgent0107 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/designer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.designer import DesignerAgent0107 as DesignerAgent0107  # noqa: F401

__all__ = ["DesignerAgent0107", "create"]


def create(hub, aid: int = 107):
    """The historical create() — kept with the same default."""
    return DesignerAgent0107(name="designer_0107", hub=hub, agent_id=aid)
