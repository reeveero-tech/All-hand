#!/usr/bin/env python3
"""DataProcessorAgent0460 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0460 as DataProcessorAgent0460  # noqa: F401

__all__ = ["DataProcessorAgent0460", "create"]


def create(hub, aid: int = 460):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0460(name="data_processor_0460", hub=hub, agent_id=aid)
