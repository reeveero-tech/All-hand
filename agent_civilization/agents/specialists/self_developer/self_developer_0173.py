#!/usr/bin/env python3
"""SelfDeveloperAgent0173 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0173 as SelfDeveloperAgent0173  # noqa: F401

__all__ = ["SelfDeveloperAgent0173", "create"]


def create(hub, aid: int = 173):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0173(name="self_developer_0173", hub=hub, agent_id=aid)
