#!/usr/bin/env python3
"""SelfDeveloperAgent0253 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/self_developer_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.self_developer import SelfDeveloperAgent0253 as SelfDeveloperAgent0253  # noqa: F401

__all__ = ["SelfDeveloperAgent0253", "create"]


def create(hub, aid: int = 253):
    """The historical create() — kept with the same default."""
    return SelfDeveloperAgent0253(name="self_developer_0253", hub=hub, agent_id=aid)
