#!/usr/bin/env python3
"""SelfDeveloperAgent0873 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0873 as SelfDeveloperAgent0873  # noqa: F401

__all__ = ["SelfDeveloperAgent0873", "create"]


def create(hub, aid: int = 873):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0873(name="self_developer_0873", hub=hub, agent_id=aid)
