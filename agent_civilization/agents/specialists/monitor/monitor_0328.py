#!/usr/bin/env python3
"""MonitorAgent0328 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0328 as MonitorAgent0328  # noqa: F401

__all__ = ["MonitorAgent0328", "create"]


def create(hub, aid: int = 328):
    """The historical create() — kept with the same default."""
    return MonitorAgent0328(name="monitor_0328", hub=hub, agent_id=aid)
