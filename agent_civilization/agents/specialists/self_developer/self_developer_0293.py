#!/usr/bin/env python3
"""SelfDeveloperAgent0293 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0293 as SelfDeveloperAgent0293  # noqa: F401

__all__ = ["SelfDeveloperAgent0293", "create"]


def create(hub, aid: int = 293):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0293(name="self_developer_0293", hub=hub, agent_id=aid)
