#!/usr/bin/env python3
"""CommunicatorAgent0638 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/communicator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0638 as CommunicatorAgent0638  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["CommunicatorAgent0638", "create"]

create = generate_create("communicator", CommunicatorAgent0638, 638)
