#!/usr/bin/env python3
"""DataProcessorAgent0440 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0440 as DataProcessorAgent0440  # noqa: F401

__all__ = ["DataProcessorAgent0440", "create"]


def create(hub, aid: int = 440):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0440(name="data_processor_0440", hub=hub, agent_id=aid)
