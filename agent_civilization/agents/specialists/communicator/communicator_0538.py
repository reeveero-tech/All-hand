#!/usr/bin/env python3
"""CommunicatorAgent0538 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single template body in
_templates/communicator_template.py; this historical module now just re-exports the
generated class and the create() function so any existing importer keeps working.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0538 as CommunicatorAgent0538  # noqa: F401

__all__ = ["CommunicatorAgent0538", "create"]


def create(hub, aid: int = 538):
    """The historical create() — kept with the same default."""
    return CommunicatorAgent0538(name="communicator_0538", hub=hub, agent_id=aid)
