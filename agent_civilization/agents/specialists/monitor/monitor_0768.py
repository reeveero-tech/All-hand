#!/usr/bin/env python3
"""MonitorAgent0768 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0768 as MonitorAgent0768  # noqa: F401

__all__ = ["MonitorAgent0768", "create"]


def create(hub, aid: int = 768):
    """The historical create() — kept with the same default."""
    return MonitorAgent0768(name="monitor_0768", hub=hub, agent_id=aid)
