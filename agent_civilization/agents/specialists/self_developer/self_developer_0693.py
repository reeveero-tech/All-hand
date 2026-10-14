#!/usr/bin/env python3
"""SelfDeveloperAgent0693 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0693 as SelfDeveloperAgent0693  # noqa: F401

__all__ = ["SelfDeveloperAgent0693", "create"]


def create(hub, aid: int = 693):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0693(name="self_developer_0693", hub=hub, agent_id=aid)
