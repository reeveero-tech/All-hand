#!/usr/bin/env python3
"""DataProcessorAgent0740 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0740 as DataProcessorAgent0740  # noqa: F401

__all__ = ["DataProcessorAgent0740", "create"]


def create(hub, aid: int = 740):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0740(name="data_processor_0740", hub=hub, agent_id=aid)
