#!/usr/bin/env python3
"""ModifierAgent0071 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0071 as ModifierAgent0071  # noqa: F401

__all__ = ["ModifierAgent0071", "create"]


def create(hub, aid: int = 71):
    """The historical create() — kept with the same default."""
    return ModifierAgent0071(name="modifier_0071", hub=hub, agent_id=aid)
