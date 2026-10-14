#!/usr/bin/env python3
"""SelfDeveloperAgent0273 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0273 as SelfDeveloperAgent0273  # noqa: F401

__all__ = ["SelfDeveloperAgent0273", "create"]


def create(hub, aid: int = 273):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0273(name="self_developer_0273", hub=hub, agent_id=aid)
