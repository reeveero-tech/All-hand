#!/usr/bin/env python3
"""SelfDeveloperAgent0973 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0973 as SelfDeveloperAgent0973  # noqa: F401

__all__ = ["SelfDeveloperAgent0973", "create"]


def create(hub, aid: int = 973):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0973(name="self_developer_0973", hub=hub, agent_id=aid)
