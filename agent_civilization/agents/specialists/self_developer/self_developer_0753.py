#!/usr/bin/env python3
"""SelfDeveloperAgent0753 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0753 as SelfDeveloperAgent0753  # noqa: F401

__all__ = ["SelfDeveloperAgent0753", "create"]


def create(hub, aid: int = 753):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0753(name="self_developer_0753", hub=hub, agent_id=aid)
