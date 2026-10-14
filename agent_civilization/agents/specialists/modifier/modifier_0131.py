#!/usr/bin/env python3
"""ModifierAgent0131 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/modifier_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.modifier import ModifierAgent0131 as ModifierAgent0131  # noqa: F401

__all__ = ["ModifierAgent0131", "create"]


def create(hub, aid: int = 131):
    """The historical create() — kept with the same default."""
    return ModifierAgent0131(name="modifier_0131", hub=hub, agent_id=aid)
