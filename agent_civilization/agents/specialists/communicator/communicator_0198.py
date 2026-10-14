#!/usr/bin/env python3
"""CommunicatorAgent0198 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0198 as CommunicatorAgent0198  # noqa: F401

__all__ = ["CommunicatorAgent0198", "create"]


def create(hub, aid: int = 198):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0198(name="communicator_0198", hub=hub, agent_id=aid)
