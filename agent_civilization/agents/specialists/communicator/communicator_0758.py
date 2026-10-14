#!/usr/bin/env python3
"""CommunicatorAgent0758 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0758 as CommunicatorAgent0758  # noqa: F401

__all__ = ["CommunicatorAgent0758", "create"]


def create(hub, aid: int = 758):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0758(name="communicator_0758", hub=hub, agent_id=aid)
