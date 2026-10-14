#!/usr/bin/env python3
"""CommunicatorAgent0318 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0318 as CommunicatorAgent0318  # noqa: F401

__all__ = ["CommunicatorAgent0318", "create"]


def create(hub, aid: int = 318):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0318(name="communicator_0318", hub=hub, agent_id=aid)
