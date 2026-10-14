#!/usr/bin/env python3
"""DataProcessorAgent0920 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0920 as DataProcessorAgent0920  # noqa: F401

__all__ = ["DataProcessorAgent0920", "create"]


def create(hub, aid: int = 920):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0920(name="data_processor_0920", hub=hub, agent_id=aid)
