#!/usr/bin/env python3
"""CommunicatorAgent0338 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/communicator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0338 as CommunicatorAgent0338  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["CommunicatorAgent0338", "create"]

create = generate_create("communicator", CommunicatorAgent0338, 338)
