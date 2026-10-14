#!/usr/bin/env python3
"""DataProcessorAgent0220 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0220 as DataProcessorAgent0220  # noqa: F401

__all__ = ["DataProcessorAgent0220", "create"]


def create(hub, aid: int = 220):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0220(name="data_processor_0220", hub=hub, agent_id=aid)
