#!/usr/bin/env python3
"""ModifierAgent0011 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0011 as ModifierAgent0011  # noqa: F401

__all__ = ["ModifierAgent0011", "create"]


def create(hub, aid: int = 11):
    """The historical create() — kept with the same default."""
    return ModifierAgent0011(name="modifier_0011", hub=hub, agent_id=aid)
