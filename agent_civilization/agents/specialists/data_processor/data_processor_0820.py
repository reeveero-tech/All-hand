#!/usr/bin/env python3
"""DataProcessorAgent0820 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/data_processor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0820 as DataProcessorAgent0820  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["DataProcessorAgent0820", "create"]

create = generate_create("data_processor", DataProcessorAgent0820, 820)
