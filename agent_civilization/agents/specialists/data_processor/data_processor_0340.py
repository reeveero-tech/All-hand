#!/usr/bin/env python3
"""DataProcessorAgent0340 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0340 as DataProcessorAgent0340  # noqa: F401

__all__ = ["DataProcessorAgent0340", "create"]


def create(hub, aid: int = 340):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0340(name="data_processor_0340", hub=hub, agent_id=aid)
