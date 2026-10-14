#!/usr/bin/env python3
"""DataProcessorAgent0960 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/data_processor_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0960 as DataProcessorAgent0960  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["DataProcessorAgent0960", "create"]

create = generate_create("data_processor", DataProcessorAgent0960, 960)
