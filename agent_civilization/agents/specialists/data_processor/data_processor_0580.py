#!/usr/bin/env python3
"""DataProcessorAgent0580 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0580 as DataProcessorAgent0580  # noqa: F401

__all__ = ["DataProcessorAgent0580", "create"]


def create(hub, aid: int = 580):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0580(name="data_processor_0580", hub=hub, agent_id=aid)
