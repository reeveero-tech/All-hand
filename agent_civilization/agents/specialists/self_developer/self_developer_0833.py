#!/usr/bin/env python3
"""SelfDeveloperAgent0833 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0833 as SelfDeveloperAgent0833  # noqa: F401

__all__ = ["SelfDeveloperAgent0833", "create"]


def create(hub, aid: int = 833):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0833(name="self_developer_0833", hub=hub, agent_id=aid)
