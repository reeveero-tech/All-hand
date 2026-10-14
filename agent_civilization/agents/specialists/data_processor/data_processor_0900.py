#!/usr/bin/env python3
"""DataProcessorAgent0900 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0900 as DataProcessorAgent0900  # noqa: F401

__all__ = ["DataProcessorAgent0900", "create"]


def create(hub, aid: int = 900):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0900(name="data_processor_0900", hub=hub, agent_id=aid)
