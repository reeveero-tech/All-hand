#!/usr/bin/env python3
"""SelfDeveloperAgent0933 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0933 as SelfDeveloperAgent0933  # noqa: F401

__all__ = ["SelfDeveloperAgent0933", "create"]


def create(hub, aid: int = 933):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0933(name="self_developer_0933", hub=hub, agent_id=aid)
