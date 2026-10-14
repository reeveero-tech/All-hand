#!/usr/bin/env python3
"""MonitorAgent0188 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0188 as MonitorAgent0188  # noqa: F401

__all__ = ["MonitorAgent0188", "create"]


def create(hub, aid: int = 188):
    """The historical create() — kept with the same default."""
    return MonitorAgent0188(name="monitor_0188", hub=hub, agent_id=aid)
