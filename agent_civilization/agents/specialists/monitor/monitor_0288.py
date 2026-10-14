#!/usr/bin/env python3
"""MonitorAgent0288 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/monitor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0288 as MonitorAgent0288  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["MonitorAgent0288", "create"]

create = generate_create("monitor", MonitorAgent0288, 288)
