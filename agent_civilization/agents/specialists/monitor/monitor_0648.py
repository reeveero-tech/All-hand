#!/usr/bin/env python3
"""MonitorAgent0648 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/monitor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0648 as MonitorAgent0648  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["MonitorAgent0648", "create"]

create = generate_create("monitor", MonitorAgent0648, 648)
