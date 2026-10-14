#!/usr/bin/env python3
"""CommunicatorAgent0058 — thin compatibility shim (R-105 / D-049).

The implementation was deduplicated into the single base class in
_templates/communicator_base.py; this historical module re-exports the generated
class (a type() subclass with the historical name and agent_id default) and
the create() function.
"""

from agent_civilization.agents.specialists.communicator import CommunicatorAgent0058 as CommunicatorAgent0058  # noqa: F401
from agent_civilization.agents.specialists._templates._factory import generate_create

__all__ = ["CommunicatorAgent0058", "create"]

create = generate_create("communicator", CommunicatorAgent0058, 58)
