#!/usr/bin/env python3
"""SelfDeveloperAgent0033 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0033 as SelfDeveloperAgent0033  # noqa: F401

__all__ = ["SelfDeveloperAgent0033", "create"]


def create(hub, aid: int = 33):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0033(name="self_developer_0033", hub=hub, agent_id=aid)
