#!/usr/bin/env python3
"""SelfDeveloperAgent0073 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0073 as SelfDeveloperAgent0073  # noqa: F401

__all__ = ["SelfDeveloperAgent0073", "create"]


def create(hub, aid: int = 73):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0073(name="self_developer_0073", hub=hub, agent_id=aid)
