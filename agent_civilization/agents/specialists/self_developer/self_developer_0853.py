#!/usr/bin/env python3
"""SelfDeveloperAgent0853 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0853 as SelfDeveloperAgent0853  # noqa: F401

__all__ = ["SelfDeveloperAgent0853", "create"]


def create(hub, aid: int = 853):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0853(name="self_developer_0853", hub=hub, agent_id=aid)
