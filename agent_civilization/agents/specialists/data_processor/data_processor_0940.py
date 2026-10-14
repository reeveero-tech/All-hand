#!/usr/bin/env python3
"""DataProcessorAgent0940 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0940 as DataProcessorAgent0940  # noqa: F401

__all__ = ["DataProcessorAgent0940", "create"]


def create(hub, aid: int = 940):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0940(name="data_processor_0940", hub=hub, agent_id=aid)
