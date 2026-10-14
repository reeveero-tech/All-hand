#!/usr/bin/env python3
"""SelfDeveloperAgent0493 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0493 as SelfDeveloperAgent0493  # noqa: F401

__all__ = ["SelfDeveloperAgent0493", "create"]


def create(hub, aid: int = 493):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0493(name="self_developer_0493", hub=hub, agent_id=aid)
