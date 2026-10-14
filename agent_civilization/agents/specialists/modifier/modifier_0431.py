#!/usr/bin/env python3
"""ModifierAgent0431 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0431 as ModifierAgent0431  # noqa: F401

__all__ = ["ModifierAgent0431", "create"]


def create(hub, aid: int = 431):
    """The historical create() — kept with the same default."""
    return ModifierAgent0431(name="modifier_0431", hub=hub, agent_id=aid)
