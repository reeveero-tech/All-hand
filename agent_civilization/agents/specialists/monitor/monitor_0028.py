#!/usr/bin/env python3
"""MonitorAgent0028 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0028 as MonitorAgent0028  # noqa: F401

__all__ = ["MonitorAgent0028", "create"]


def create(hub, aid: int = 28):
    """The historical create() — kept with the same default."""
    return MonitorAgent0028(name="monitor_0028", hub=hub, agent_id=aid)
