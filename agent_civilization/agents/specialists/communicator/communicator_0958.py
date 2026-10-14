#!/usr/bin/env python3
"""CommunicatorAgent0958 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0958 as CommunicatorAgent0958  # noqa: F401

__all__ = ["CommunicatorAgent0958", "create"]


def create(hub, aid: int = 958):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0958(name="communicator_0958", hub=hub, agent_id=aid)
