#!/usr/bin/env python3
"""MonitorAgent0088 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0088 as MonitorAgent0088  # noqa: F401

__all__ = ["MonitorAgent0088", "create"]


def create(hub, aid: int = 88):
    """The historical create() — kept with the same default."""
    return MonitorAgent0088(name="monitor_0088", hub=hub, agent_id=aid)
