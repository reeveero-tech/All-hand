#!/usr/bin/env python3
"""MonitorAgent0368 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0368 as MonitorAgent0368  # noqa: F401

__all__ = ["MonitorAgent0368", "create"]


def create(hub, aid: int = 368):
    """The historical create() — kept with the same default."""
    return MonitorAgent0368(name="monitor_0368", hub=hub, agent_id=aid)
