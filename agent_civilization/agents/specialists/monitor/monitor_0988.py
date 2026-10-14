#!/usr/bin/env python3
"""MonitorAgent0988 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/monitor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.monitor import MonitorAgent0988 as MonitorAgent0988  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["MonitorAgent0988", "create"]

create = generate_create("monitor", MonitorAgent0988, 988)
