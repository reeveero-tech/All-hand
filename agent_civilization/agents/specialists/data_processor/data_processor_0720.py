#!/usr/bin/env python3
"""DataProcessorAgent0720 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0720 as DataProcessorAgent0720  # noqa: F401

__all__ = ["DataProcessorAgent0720", "create"]


def create(hub, aid: int = 720):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0720(name="data_processor_0720", hub=hub, agent_id=aid)
