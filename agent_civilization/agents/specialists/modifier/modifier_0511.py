#!/usr/bin/env python3
"""ModifierAgent0511 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0511 as ModifierAgent0511  # noqa: F401

__all__ = ["ModifierAgent0511", "create"]


def create(hub, aid: int = 511):
    """The historical create() — kept with the same default."""
    return ModifierAgent0511(name="modifier_0511", hub=hub, agent_id=aid)
