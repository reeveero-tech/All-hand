#!/usr/bin/env python3
"""CommunicatorAgent0138 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0138 as CommunicatorAgent0138  # noqa: F401

__all__ = ["CommunicatorAgent0138", "create"]


def create(hub, aid: int = 138):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0138(name="communicator_0138", hub=hub, agent_id=aid)
