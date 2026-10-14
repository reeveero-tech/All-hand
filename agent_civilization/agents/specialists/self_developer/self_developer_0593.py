#!/usr/bin/env python3
"""SelfDeveloperAgent0593 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0593 as SelfDeveloperAgent0593  # noqa: F401

__all__ = ["SelfDeveloperAgent0593", "create"]


def create(hub, aid: int = 593):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0593(name="self_developer_0593", hub=hub, agent_id=aid)
