#!/usr/bin/env python3
"""SelfDeveloperAgent0813 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0813 as SelfDeveloperAgent0813  # noqa: F401

__all__ = ["SelfDeveloperAgent0813", "create"]


def create(hub, aid: int = 813):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0813(name="self_developer_0813", hub=hub, agent_id=aid)
