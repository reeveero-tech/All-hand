#!/usr/bin/env python3
"""DataProcessorAgent0680 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/data_processor_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.data_processor import DataProcessorAgent0680 as DataProcessorAgent0680  # noqa: F401

__all__ = ["DataProcessorAgent0680", "create"]


def create(hub, aid: int = 680):
    """The historical create() — kept with the same default."""
    return DataProcessorAgent0680(name="data_processor_0680", hub=hub, agent_id=aid)
