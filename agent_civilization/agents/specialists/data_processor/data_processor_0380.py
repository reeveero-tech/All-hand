#!/usr/bin/env python3
"""DataProcessorAgent0380 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0380 as DataProcessorAgent0380  # noqa: F401

__all__ = ["DataProcessorAgent0380", "create"]


def create(hub, aid: int = 380):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0380(name="data_processor_0380", hub=hub, agent_id=aid)
