#!/usr/bin/env python3
"""ModifierAgent0631 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0631 as ModifierAgent0631  # noqa: F401

__all__ = ["ModifierAgent0631", "create"]


def create(hub, aid: int = 631):
    """The historical create() — kept with the same default."""
    return ModifierAgent0631(name="modifier_0631", hub=hub, agent_id=aid)
