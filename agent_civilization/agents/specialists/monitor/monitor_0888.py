#!/usr/bin/env python3
"""MonitorAgent0888 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0888 as MonitorAgent0888  # noqa: F401

__all__ = ["MonitorAgent0888", "create"]


def create(hub, aid: int = 888):
    """The historical create() — kept with the same default."""
    return MonitorAgent0888(name="monitor_0888", hub=hub, agent_id=aid)
