#!/usr/bin/env python3
"""MonitorAgent0428 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/monitor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0428 as MonitorAgent0428  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["MonitorAgent0428", "create"]

create = generate_create("monitor", MonitorAgent0428, 428)
