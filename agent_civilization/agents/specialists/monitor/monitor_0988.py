#!/usr/bin/env python3
"""MonitorAgent0988 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/monitor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0988 as MonitorAgent0988  # noqa: F401

__all__ = ["MonitorAgent0988", "create"]


def create(hub, aid: int = 988):
    """The historical create() — kept with the same default."""
    return MonitorAgent0988(name="monitor_0988", hub=hub, agent_id=aid)
